from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
import time
import threading
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request as FastAPIRequest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import delete, func
from sqlmodel import select

from backend.cache.redis_client import redis_cache
from backend.config import get_settings
from backend.db.database import get_session
import math

from backend.models.curriculum import (
    Curriculum,
    CurriculumJob,
    GenerateCurriculumJobResponse,
    GenerateCurriculumRequest,
    GenerateCurriculumResponse,
    CurriculumProgress,
    DayCompletionRequest,
    DayCompletionResponse,
)
from backend.models.moat_features import (
    ConceptNode,
    LearningSession,
    TranscriptIntelligence,
    VideoPerformanceScore,
)
from backend.models.video import Video
from backend.services.dependency_graph import dependency_graph_service, SpacedRepetitionExpander
from backend.services.curriculum_planner import curriculum_planner
from backend.services.goal_parser import goal_parser
from backend.services.path_assembler import path_assembler
from backend.services.scoring_engine import scoring_engine
from backend.services.transcript_analyzer import transcript_analyzer
from backend.services.content_fetcher import content_fetcher
from backend.services.embeddings import embeddings_service
from backend.config import get_settings
from backend.services.llm_client import llm_service
from backend.services.analytics import track_event


router = APIRouter(prefix="/api/v1/curriculum", tags=["curriculum"])
settings = get_settings()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
USER_FACING_FAILURE_MESSAGE = "We couldn't generate your curriculum right now. Please try again in a moment."
MAX_SCORABLE_CANDIDATES_PER_CONCEPT = 6
CONCEPTS_PER_WEEK = 7
INITIAL_WEEKS = 1
# Active curriculum generation can take several minutes, so keep the stale
# threshold well above a normal run to avoid re-queuing jobs that are still progressing.
JOB_STALE_SECONDS = 3600
_IN_MEMORY_RATE_LIMIT: dict[str, list[float]] = {}
_IN_MEMORY_RATE_LIMIT_LOCK = threading.Lock()


def _request_identity(request: FastAPIRequest, session_id: str | None) -> str:
    if session_id and str(session_id).strip():
        return f"session:{str(session_id).strip()}"

    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else ""
    if not ip and request.client:
        ip = request.client.host or ""
    if not ip:
        ip = "unknown"
    return f"ip:{ip}"


def _check_rate_limit_in_memory(key: str, limit: int, window_seconds: int) -> tuple[bool, int | None]:
    now = time.time()
    cutoff = now - max(window_seconds, 1)

    with _IN_MEMORY_RATE_LIMIT_LOCK:
        timestamps = _IN_MEMORY_RATE_LIMIT.get(key, [])
        timestamps = [ts for ts in timestamps if ts > cutoff]

        if len(timestamps) >= limit:
            retry_after = max(1, int(window_seconds - (now - min(timestamps))))
            _IN_MEMORY_RATE_LIMIT[key] = timestamps
            return False, retry_after

        timestamps.append(now)
        _IN_MEMORY_RATE_LIMIT[key] = timestamps
        return True, None


def _check_rate_limit(key: str, limit: int, window_seconds: int) -> tuple[bool, int | None]:
    count = redis_cache.increment_with_ttl(key, window_seconds)
    if count is not None:
        if count <= limit:
            return True, None
        ttl = redis_cache.ttl_seconds(key)
        return False, max(1, ttl or window_seconds)
    return _check_rate_limit_in_memory(key, limit, window_seconds)


def _remaining_generation_requests(identity: str, hourly_limit: int) -> int | None:
    hourly_key = f"rl:generation:hour:{identity}"
    count = redis_cache.get_int(hourly_key)
    if count is None:
        with _IN_MEMORY_RATE_LIMIT_LOCK:
            timestamps = _IN_MEMORY_RATE_LIMIT.get(hourly_key, [])
            return max(0, hourly_limit - len(timestamps))

    return max(0, hourly_limit - count)


def _enforce_generation_limits(http_request: FastAPIRequest, session_id: str | None) -> None:
    if not settings.generation_rate_limit_enabled:
        return

    identity = _request_identity(http_request, session_id)

    burst_limit = max(1, int(settings.generation_burst_limit_count))
    burst_window = max(1, int(settings.generation_burst_window_seconds))
    hourly_limit = max(1, int(settings.generation_hourly_limit_count))
    hourly_window = max(1, int(settings.generation_hourly_window_seconds))

    burst_key = f"rl:generation:burst:{identity}"
    burst_ok, burst_retry = _check_rate_limit(burst_key, burst_limit, burst_window)
    if not burst_ok:
        raise HTTPException(
            status_code=429,
            detail=f"Too many curriculum requests. Please wait {burst_retry or burst_window}s and try again.",
        )

    hourly_key = f"rl:generation:hour:{identity}"
    hourly_ok, hourly_retry = _check_rate_limit(hourly_key, hourly_limit, hourly_window)
    if not hourly_ok:
        raise HTTPException(
            status_code=429,
            detail=f"Hourly curriculum limit reached. Please retry in {hourly_retry or hourly_window}s.",
        )


def _curriculum_cache_key(topic: str, level: str, duration_weeks: int, hours_per_day: float) -> str:
    source = f"{topic}:{level}:{duration_weeks}:{hours_per_day}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return f"curriculum:v5-groq:{digest}"


def _generate_curriculum_description(
    topic: str,
    level: str,
    weeks: list[dict[str, Any]],
    total_videos: int,
) -> str:
    """Generate a user-friendly description of the curriculum based on its structure."""
    level_display = level.capitalize()
    
    # Count unique concepts covered
    concepts = set()
    for week in weeks:
        for day in week.get("days", []):
            concepts.add(day.get("concept", ""))
    concepts.discard("")
    
    concept_count = len(concepts)
    week_count = len(weeks)
    
    # Generate description based on structure
    if level.lower() == "beginner":
        phase1 = "Start with foundational concepts"
        phase2 = "build practical skills"
    elif level.lower() == "advanced":
        phase1 = "Master complex topics"
        phase2 = "explore advanced applications"
    else:  # intermediate
        phase1 = "Deepen your understanding"
        phase2 = "apply intermediate techniques"
    
    return f"{phase1}, then {phase2}. {total_videos} videos across {concept_count} key concepts."


def _chain_progression_concepts(
    concepts: list[dict[str, Any]],
    previous_concept: str | None = None,
) -> list[dict[str, Any]]:
    chained: list[dict[str, Any]] = []
    last_name = previous_concept

    for concept in concepts:
        name = str(concept.get("name", "")).strip()
        if not name:
            continue

        prereqs = [str(item).strip() for item in (concept.get("prerequisites", []) or []) if str(item).strip()]
        if last_name and last_name not in prereqs:
            prereqs = [last_name, *prereqs]
        elif not prereqs and last_name:
            prereqs = [last_name]

        cleaned = dict(concept)
        cleaned["name"] = name
        cleaned["prerequisites"] = list(dict.fromkeys(prereqs))
        chained.append(cleaned)
        last_name = name

    return chained


def _get_mastered_concepts(session_id: str, topic: str) -> set[str]:
    """Retrieve concepts the user has already mastered in this topic."""
    from backend.models.moat_features import ConceptMastery
    
    try:
        with get_session() as session:
            mastered = session.exec(
                select(ConceptMastery)
                .where(ConceptMastery.session_id == session_id)
                .where(ConceptMastery.topic == topic)
                .where(ConceptMastery.confidence > 0.3)  # Only confident mastery
            ).all()
            return {m.concept for m in mastered}
    except Exception as exc:
        logger.warning(f"Could not load mastered concepts: {exc}")
        return set()


def _record_mastered_concepts(
    session_id: str,
    topic: str,
    concept_names: list[str],
    level: str,
) -> None:
    """Record which concepts were presented in this curriculum (mark as learning started)."""
    from backend.models.moat_features import ConceptMastery
    
    try:
        with get_session() as session:
            for concept in concept_names:
                mastery = ConceptMastery(
                    session_id=session_id,
                    topic=topic,
                    concept=concept,
                    level_learned=level,
                    confidence=0.3,  # Mark as "learning in progress"
                )
                session.add(mastery)
            session.commit()
            logger.info(f"Recorded {len(concept_names)} mastered concepts for session {session_id}")
    except Exception as exc:
        logger.warning(f"Could not record mastered concepts: {exc}")


def _get_viewed_content_ids(session_id: str) -> set[str]:
    """Get all content IDs the user has viewed in previous sessions."""
    from backend.models.moat_features import ContentInteraction
    
    try:
        with get_session() as session:
            interactions = session.exec(
                select(ContentInteraction)
                .where(ContentInteraction.session_id == session_id)
                .where(ContentInteraction.interaction_type.in_(["viewed", "started", "completed"]))
            ).all()
            return {i.content_id for i in interactions}
    except Exception as exc:
        logger.warning(f"Could not load viewed content IDs: {exc}")
        return set()


def _record_content_interaction(
    session_id: str,
    curriculum_id: uuid.UUID,
    content_id: str,
    source: str,
    concept: str,
    interaction_type: str = "viewed",
) -> None:
    """Record a user's interaction with specific content."""
    from backend.models.moat_features import ContentInteraction
    
    try:
        with get_session() as session:
            interaction = ContentInteraction(
                session_id=session_id,
                content_id=content_id,
                source=source,
                curriculum_id=curriculum_id,
                concept=concept,
                interaction_type=interaction_type,
            )
            session.add(interaction)
            session.commit()
    except Exception as exc:
        logger.warning(f"Could not record content interaction: {exc}")


def _score_candidates_for_concept(
    concept: str,
    prerequisites: list[str],
    candidates: list[dict[str, Any]],
    personalization_weights: dict[str, float] | None = None,
    level: str = "beginner",
) -> list[dict[str, Any]]:
    """Score candidates with optional performance and personalization factors."""
    scored: list[dict[str, Any]] = []
    loop_start = time.perf_counter()
    
    # Batch load DB-backed features quickly, then release connection before heavier scoring work.
    batch_load_start = time.perf_counter()
    youtube_ids = [v["youtube_id"] for v in candidates]
    ti_by_id: dict[str, TranscriptIntelligence] = {}
    vp_by_id: dict[str, VideoPerformanceScore] = {}

    with get_session() as session:
        ti_records = session.exec(
            select(TranscriptIntelligence).where(
                TranscriptIntelligence.youtube_id.in_(youtube_ids)
            )
        ).all()
        vp_records = session.exec(
            select(VideoPerformanceScore).where(
                VideoPerformanceScore.youtube_id.in_(youtube_ids)
            )
        ).all()
        ti_by_id = {rec.youtube_id: rec for rec in ti_records}
        vp_by_id = {rec.youtube_id: rec for rec in vp_records}

    logger.debug(f"[PERF] Batch loaded {len(ti_by_id)} TI + {len(vp_by_id)} VP records in {time.perf_counter() - batch_load_start:.3f}s")

    new_ti_records: list[TranscriptIntelligence] = []

    for idx, video in enumerate(candidates):
        video_start = time.perf_counter()
        youtube_id = video["youtube_id"]
        source = video.get("source", "youtube")

        # Skip transcript analysis for non-video sources
        if source != "youtube":
            analysis = {
                "relevance_overlap": 0.0,
                "transcript_word_count": 0,
                "transcript_available": False,
                "reading_level": 3.0 if source == "docs" else 8.0,  # Docs simpler, papers more complex
                "content_density": 0.6,
                "has_code_examples": source == "github",
                "key_concepts": [],
            }
        else:
            # Use batch-loaded record or analyze new for YouTube videos
            cached_analysis = ti_by_id.get(youtube_id)
            if cached_analysis and cached_analysis.analysis_version == "v1":
                # Use cached analysis
                analysis = {
                    "relevance_overlap": 0.0,  # Will be recalculated
                    "transcript_word_count": 0,
                    "transcript_available": True,
                    "reading_level": cached_analysis.reading_level,
                    "content_density": cached_analysis.content_density,
                    "has_code_examples": cached_analysis.has_code_examples,
                    "key_concepts": cached_analysis.key_concepts,
                }
            else:
                # Analyze new transcript
                analysis = transcript_analyzer.analyze(video, concept, prerequisites, allow_fetch=False)

                # Queue new analysis for later batch insert
                ti = TranscriptIntelligence(
                    youtube_id=youtube_id,
                    reading_level=float(analysis.get("reading_level", 0.0)),
                    mentions_prerequisites=any(p.lower() in analysis.get("transcript_text", "").lower() for p in prerequisites),
                    has_code_examples=bool(analysis.get("has_code_examples", False)),
                    content_density=float(analysis.get("content_density", 0.0)),
                    key_concepts=analysis.get("key_concepts", []),
                )
                new_ti_records.append(ti)

        # Get video performance score from batch-loaded records
        perf_score = vp_by_id.get(youtube_id)
        performance_score = perf_score.performance_score if perf_score else 0.5

        # Score with performance and personalization factors
        score_payload = scoring_engine.score_video(
            video,
            analysis,
            concept,
            performance_score=performance_score,
            personalization_weights=personalization_weights,
            level=level,
        )

        video_time = time.perf_counter() - video_start
        if idx % 20 == 0:  # Log every 20th video to avoid spam
            logger.debug(f"[PERF] Scored video {idx+1}/{len(candidates)} in {video_time:.3f}s")

        scored.append(
            {
                **video,
                **analysis,
                "score": score_payload["score"],
                "why_this_video": score_payload["why_this_video"],
                "score_components": score_payload["components"],
            }
        )

    # Batch insert new transcript intelligence records in a short-lived session.
    if new_ti_records:
        with get_session() as session:
            for ti in new_ti_records:
                session.add(ti)
            session.commit()

    scored.sort(key=lambda item: item["score"], reverse=True)
    total_loop_time = time.perf_counter() - loop_start
    avg_time = total_loop_time / max(len(candidates), 1)
    logger.info(f"[PERF] Scored {len(candidates)} candidates in {total_loop_time:.2f}s (avg: {avg_time:.3f}s per video)")
    return scored


def _pre_rank_candidates_for_concept(
    concept: str,
    candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    concept_terms = [
        term
        for term in re.findall(r"[a-z0-9]+", concept.lower())
        if len(term) > 2
    ]

    required_title_hits = 1 if len(concept_terms) <= 1 else 2
    generic_terms = {
        "basics",
        "basic",
        "workflow",
        "example",
        "examples",
        "pattern",
        "patterns",
        "practice",
        "review",
        "next",
        "steps",
        "guide",
        "intro",
        "introduction",
        "advanced",
        "mastery",
        "tutorial",
        "course",
        "lesson",
    }
    anchor_terms = [term for term in concept_terms if term not in generic_terms]

    def _rank_info(video: dict[str, Any]) -> tuple[int, int, int, float, int, int, float, float, float, int]:
        haystack = " ".join([
            str(video.get("title", "")),
            str(video.get("description", "")),
            str(video.get("channel", "")),
        ]).lower()
        title = str(video.get("title", "")).lower()
        description = str(video.get("description", "")).lower()

        title_hits = sum(1 for term in concept_terms if term in title)
        description_hits = sum(1 for term in concept_terms if term in description)
        term_hits = sum(1 for term in concept_terms if term in haystack)
        anchor_hits = sum(1 for term in anchor_terms if term in haystack)
        anchor_title_hits = sum(1 for term in anchor_terms if term in title)
        exact_phrase = 1.0 if concept.lower() in title else 0.0
        anchor_gate = (anchor_title_hits > 0) if anchor_terms else True
        strict_match = int(exact_phrase or (title_hits >= required_title_hits and anchor_gate))
        title_boost = 1.0 if title_hits > 0 else 0.0
        transcript_boost = 0.35 if video.get("transcript_cached") else 0.0
        engagement_boost = min(float(video.get("view_count", 0)) / 500_000.0, 1.0)
        recency_boost = 1.0 if "published_at" in video and str(video.get("published_at", "")) else 0.0
        
        # Boost non-video sources (docs/papers/repos) slightly if they match at all
        source = video.get("source", "youtube")
        source_boost = 0.5 if source != "youtube" and term_hits > 0 else 0.0

        # Primary sort: exact or near-exact title match first, then title overlap,
        # then broader lexical overlap, then cached transcript, engagement, recency.
        # Non-video sources can rank higher with lexical matches since they have different naming conventions
        # Calculate source affinity boost based on concept type
        from backend.services.scoring_engine import scoring_engine
        source_affinity = scoring_engine._calculate_source_affinity_boost(source, concept)
        
        # Primary sort: exact or near-exact title match first, then title overlap,
        # then broader lexical overlap, then cached transcript, engagement, recency.
        # Non-video sources can rank higher with lexical matches since they have different naming conventions
        return (
            strict_match,
            int(title_hits > 0),
            int(description_hits > 0),
            exact_phrase,
            title_hits,
            term_hits,
            transcript_boost,
            recency_boost + engagement_boost + source_boost + source_affinity * 0.2,
            1.0 if source != "youtube" else 0.0,
            anchor_hits,
        )

    def _apply_rank(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(cands, key=lambda v: _rank_info(v), reverse=True)

    ranked = _apply_rank(candidates)
    strict_ranked = [video for video in ranked if _rank_info(video)[0] == 1]
    if anchor_terms:
        title_ranked = [video for video in ranked if _rank_info(video)[1] == 1 and _rank_info(video)[9] > 0]
        lexical_ranked = [video for video in ranked if _rank_info(video)[9] > 0]
    else:
        title_ranked = [video for video in ranked if _rank_info(video)[1] == 1]
        lexical_ranked = [video for video in ranked if _rank_info(video)[5] > 0]  # term_hits > 0

    # Log pre-ranking summary to help diagnose strict filtering
    try:
        src_counts = {}
        for v in ranked:
            src = v.get("source", "youtube")
            src_counts[src] = src_counts.get(src, 0) + 1
        logger.info(
            "pre_rank_summary concept=%s total=%d strict=%d title=%d lexical=%d anchors=%s sources=%s",
            concept,
            len(ranked),
            len(strict_ranked),
            len(title_ranked),
            len(lexical_ranked),
            anchor_terms,
            src_counts,
        )
    except Exception:
        pass

    # For tight ranking, prefer videos. For lexical ranking, accept all sources
    if strict_ranked:
        # Prefer YouTube for strict matches, but include high-ranking non-video
        youtube_strict = [v for v in strict_ranked if v.get("source", "youtube") == "youtube"]
        other_strict = [v for v in strict_ranked if v.get("source", "youtube") != "youtube"]
        result = youtube_strict + other_strict
        logger.info("pre_rank_selected concept=%s tier=strict selected=%d", concept, len(result[:limit]))
        return result[:limit]
    if title_ranked:
        logger.info("pre_rank_selected concept=%s tier=title selected=%d", concept, len(title_ranked[:limit]))
        return title_ranked[:limit]
    if lexical_ranked:
        logger.info("pre_rank_selected concept=%s tier=lexical selected=%d", concept, len(lexical_ranked[:limit]))
        return lexical_ranked[:limit]

    return ranked[:limit]


@router.get("/senses")
def detect_senses(term: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Return candidate senses for an ambiguous term using the LLM.

    Response: {"senses": ["sense 1", "sense 2", ...]}
    """
    term = term.strip()
    if not term:
        raise HTTPException(status_code=400, detail="term is required and cannot be empty")

    prompt = (
        f"Return a JSON object with a single key \"senses\" mapping to an array of up to 6 short labels (3-5 words each) \n"
        f"describing distinct meanings or senses for the single-word or phrase: \"{term}\".\n"
        "Examples: \"Japanese sword\", \"video editing software\", \"brand name\".\n"
        "Do not include explanations, only the labels in the array. Return valid minified JSON only."
    )

    try:
        resp = llm_service.structured_json(prompt, max_retries=2)
        if isinstance(resp, dict) and "senses" in resp and isinstance(resp["senses"], list):
            senses = [str(s).strip() for s in resp["senses"] if str(s).strip()]
            return {"senses": senses}
    except Exception:
        logger.exception("LLM sense detection failed for term=%s", term)

    # Fallback heuristics if LLM fails
    fallback = []
    lowered = term.lower()
    if lowered in ("katana",):
        fallback = ["Japanese sword", "Katana software (Foundry)", "Katana brand or product"]
    elif lowered in ("python",):
        fallback = ["Programming language", "Snake species"]
    elif lowered in ("rust",):
        fallback = ["Programming language", "Oxidation on metal"]
    else:
        # naive split into common senses: generic, software, hardware, brand
        fallback = [term, f"{term} software", f"{term} product"]

    return {"senses": fallback}


def _save_video_rows(candidates: list[dict[str, Any]]) -> None:
    save_start = time.perf_counter()
    with get_session() as session:
        for idx, video in enumerate(candidates):
            row = session.exec(select(Video).where(Video.youtube_id == video["youtube_id"])).first()
            published_raw = video.get("published_at")
            if isinstance(published_raw, str):
                published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00")).replace(tzinfo=None)
            else:
                published_at = datetime.utcnow()

            payload = {
                "youtube_id": video["youtube_id"],
                "title": video["title"],
                "channel_name": video["channel"],
                "duration_seconds": video["duration_seconds"],
                "view_count": int(video.get("view_count", 0)),
                "like_count": int(video.get("like_count", 0)),
                "published_at": published_at,
                "transcript_cached": bool(video.get("transcript_available", False)),
                "transcript_text": video.get("transcript_text"),
                "score_cache": video.get("score_components"),
            }

            if row:
                for key, value in payload.items():
                    setattr(row, key, value)
                session.add(row)
            else:
                session.add(Video(**payload))
            
            if (idx + 1) % 50 == 0:
                logger.debug(f"[PERF] Saved {idx + 1} videos so far...")

        session.commit()
    logger.info(f"[PERF] _save_video_rows: Saved {len(candidates)} videos in {time.perf_counter() - save_start:.2f}s")


def _load_existing_curriculum(goal: str, level: str, cache_key: str | None = None) -> dict[str, Any] | None:
    with get_session() as session:
        query = select(Curriculum).where(Curriculum.goal_raw == goal).where(Curriculum.level == level)
        if cache_key:
            query = query.where(Curriculum.cache_key == cache_key)
        row = session.exec(query.order_by(Curriculum.created_at.desc())).first()
        if not row:
            return None
        return {"curriculum_id": row.id, **row.curriculum_json}


def _curriculum_social_proof(session, curriculum_uuid: uuid.UUID) -> dict[str, Any]:
    from backend.models.moat_features import CurriculumFeedback

    completion_count = session.exec(
        select(func.count(CurriculumProgress.id)).where(CurriculumProgress.curriculum_id == curriculum_uuid)
    ).one()
    feedback_rows = session.exec(
        select(CurriculumFeedback).where(CurriculumFeedback.curriculum_id == curriculum_uuid)
    ).all()
    rating_avg = 0.0
    if feedback_rows:
        rating_values: list[float] = []
        for fb in feedback_rows:
            ratings = [fb.difficulty_rating, fb.pacing_rating, fb.content_quality_rating, fb.relevance_rating]
            filtered = [float(value) for value in ratings if value and value > 0]
            if filtered:
                rating_values.append(sum(filtered) / len(filtered))
        if rating_values:
            rating_avg = round(sum(rating_values) / len(rating_values), 2)

    return {
        "completion_count": int(completion_count or 0),
        "rating_avg": rating_avg,
        "rating_count": len(feedback_rows),
    }


def _anonymous_handle(seed: str) -> str:
    adjectives = [
        "Arc", "Bright", "Calm", "Drift", "Echo", "Flint", "Glow", "Hush",
        "Ion", "Jade", "Kind", "Lumen", "Moss", "Nova", "Orb", "Pulse",
        "Quartz", "Rune", "Spark", "Tide", "Umber", "Vivid", "Wisp", "Zen",
    ]
    nouns = [
        "Fox", "Hawk", "Koala", "Lion", "Mantis", "Nova", "Otter", "Panda",
        "Quill", "Raven", "Sable", "Tiger", "Ursa", "Viper", "Wolf", "Yarrow",
    ]
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    adjective = adjectives[digest[0] % len(adjectives)]
    noun = nouns[digest[1] % len(nouns)]
    number = 100 + (digest[2] % 900)
    return f"{adjective}{noun}-{number}"


def _top_curricula_cache_key() -> str:
    return "curriculum:top:v2"


_TOP_CURRICULA_TTL_SECONDS = 39600
_TOP_CURRICULA_CACHE_LOCK = threading.Lock()
_TOP_CURRICULA_REFRESH_LOCK = threading.Lock()
_TOP_CURRICULA_CACHE: dict[str, Any] = {
    "items": [],
    "expires_at": 0.0,
}


def _get_top_curricula_from_memory(limit: int, allow_stale: bool = False) -> list[dict[str, Any]] | None:
    now = time.time()
    with _TOP_CURRICULA_CACHE_LOCK:
        items = _TOP_CURRICULA_CACHE.get("items")
        expires_at = float(_TOP_CURRICULA_CACHE.get("expires_at") or 0.0)
        if not isinstance(items, list) or not items:
            return None
        if allow_stale or expires_at > now:
            return items[:limit]
    return None


def _set_top_curricula_memory(items: list[dict[str, Any]]) -> None:
    with _TOP_CURRICULA_CACHE_LOCK:
        _TOP_CURRICULA_CACHE["items"] = items
        _TOP_CURRICULA_CACHE["expires_at"] = time.time() + _TOP_CURRICULA_TTL_SECONDS


def _invalidate_top_curricula_cache() -> None:
    with _TOP_CURRICULA_CACHE_LOCK:
        _TOP_CURRICULA_CACHE["items"] = []
        _TOP_CURRICULA_CACHE["expires_at"] = 0.0
    try:
        redis_cache.delete(_top_curricula_cache_key())
    except Exception:
        logger.debug("Failed to clear redis leaderboard cache", exc_info=True)


def _normalize_curriculum_estimates(payload: dict[str, Any], hours_per_day: float) -> dict[str, Any]:
    weeks = payload.get("weeks", []) if isinstance(payload, dict) else []
    if not isinstance(weeks, list):
        weeks = []

    concepts: list[dict[str, Any]] = []
    for week in weeks:
        for day in week.get("days", []) or []:
            concept_name = str(day.get("concept", "")).strip()
            if concept_name:
                concepts.append({"name": concept_name, "estimated_hours": 0.75})

    total_video_seconds = 0
    total_reading_minutes = 0
    
    for week in weeks:
        for day in week.get("days", []) or []:
            video = day.get("video", {}) or {}
            total_video_seconds += int(video.get("duration_seconds", 0) or 0)
            total_reading_minutes += int(video.get("reading_time_minutes", 0) or 0)

    total_video_hours = total_video_seconds / 3600.0
    total_reading_hours = total_reading_minutes / 60.0
    total_content_hours = total_video_hours + total_reading_hours
    
    practice_hours = sum(float(concept.get("estimated_hours", 0.75) or 0.75) for concept in concepts) or 0.75
    review_hours = len(concepts) * 0.25
    buffer_hours = max(0.1 * (total_content_hours + practice_hours + review_hours), 0.1)
    estimated_total_hours = total_content_hours + practice_hours + review_hours + buffer_hours
    hours_per_day_safe = max(hours_per_day or 1.0, 0.25)
    estimated_days = max(math.ceil(estimated_total_hours / hours_per_day_safe), len(weeks) or 1)

    normalized = dict(payload)
    normalized["duration_days"] = int(normalized.get("duration_days") or 0) or estimated_days
    normalized["estimated_total_hours"] = round(estimated_total_hours, 2)
    normalized["estimated_video_hours"] = round(total_video_hours, 2)
    normalized["estimated_reading_hours"] = round(total_reading_hours, 2)
    normalized["estimated_practice_hours"] = round(practice_hours, 2)
    normalized["estimated_review_hours"] = round(review_hours, 2)
    normalized["estimated_buffer_hours"] = round(buffer_hours, 2)
    return normalized


def _store_curriculum_job(job_id: uuid.UUID, **fields: Any) -> None:
    with get_session() as session:
        job = session.exec(select(CurriculumJob).where(CurriculumJob.id == job_id)).first()
        if not job:
            return
        for key, value in fields.items():
            if key == "result_json" and value is not None:
                value = _json_safe(value)
            if key == "error_message" and value is not None:
                value = str(value)
            setattr(job, key, value)
        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()

def _job_response(job: CurriculumJob) -> GenerateCurriculumJobResponse:
    payload: dict[str, Any] = {
        "job_id": job.id,
        "status": job.status,
        "curriculum_id": job.curriculum_id,
        "poll_url": f"/api/v1/curriculum/jobs/{job.id}",
        "error": job.error_message,
    }
    if job.result_json:
        payload.update(job.result_json)
    return GenerateCurriculumJobResponse.model_validate(payload)


def _selected_video_ids_from_weeks(weeks: list[dict[str, Any]]) -> set[str]:
    selected: set[str] = set()
    for week in weeks:
        for day in week.get("days", []):
            video = day.get("video", {})
            youtube_id = str(video.get("youtube_id", "")).strip()
            if youtube_id:
                selected.add(youtube_id)
            alt = day.get("alternative_video", {}) or {}
            alt_id = str(alt.get("youtube_id", "")).strip()
            if alt_id:
                selected.add(alt_id)
    return selected


def _generate_curriculum_slice(
    request: GenerateCurriculumRequest,
    topic: str,
    concepts: list[dict[str, Any]],
    learning_session: LearningSession,
    existing_video_ids: set[str] | None = None,
) -> tuple[dict[str, Any], str | None, list[dict[str, Any]]]:
    concept_names = [c["name"] if isinstance(c, dict) else str(c) for c in concepts]
    excluded_ids = existing_video_ids or set()

    yt_start = time.perf_counter()
    candidates, quota_warning = content_fetcher.fetch_candidates(concept_names, level=request.level)
    logger.info(f"[PERF] Content fetch took {time.perf_counter() - yt_start:.2f}s ({len(candidates)} candidates)")

    if excluded_ids:
        candidates = [candidate for candidate in candidates if candidate.get("youtube_id") not in excluded_ids]

    if not candidates:
        logger.warning("No YouTube candidates found for concepts: %s", concept_names)
        raise HTTPException(
            status_code=503,
            detail="Content search service returned no results. Please try again in a few moments or simplify your learning goal."
        )

    candidates = _filter_candidates_by_duration(
        candidates,
        learning_session.preferred_video_duration_seconds,
    )

    personalization_weights = _get_personalization_weights(learning_session)

    scored_by_concept: dict[str, list[dict[str, Any]]] = {}
    for idx, concept_obj in enumerate(concepts):
        concept_time = time.perf_counter()
        concept_name = concept_obj["name"] if isinstance(concept_obj, dict) else str(concept_obj)
        prerequisites = concept_obj.get("prerequisites", []) if isinstance(concept_obj, dict) else []
        shortlisted_candidates = _pre_rank_candidates_for_concept(
            concept_name,
            candidates,
            MAX_SCORABLE_CANDIDATES_PER_CONCEPT,
        )
        scored_by_concept[concept_name] = _score_candidates_for_concept(
            concept_name,
            prerequisites,
            shortlisted_candidates,
            personalization_weights=personalization_weights,
            level=request.level,
        )
        logger.info(f"[PERF] Scored slice concept {idx+1}/{len(concepts)} ({concept_name}) in {time.perf_counter() - concept_time:.2f}s")

    assembled = path_assembler.assemble(concepts, scored_by_concept, request.hours_per_day, getattr(request, "prefer_long_videos", False))

    selected_videos: list[dict[str, Any]] = []
    for week in assembled.get("weeks", []):
        for day in week.get("days", []):
            selected_videos.append(day["video"])
            alt = day.get("alternative_video")
            if alt:
                selected_videos.append(alt)

    return assembled, quota_warning, selected_videos


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _get_or_create_learning_session(session_id: str | None) -> tuple[LearningSession, bool]:
    """Get existing learning session or create new one. Returns (session, created)."""
    with get_session() as session:
        if session_id:
            # Load existing session
            existing = session.exec(
                select(LearningSession).where(LearningSession.session_id == session_id)
            ).first()
            if existing:
                existing.last_active_at = datetime.utcnow()
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing, False
        
        # Create new session
        new_session = LearningSession(
            session_id=session_id or str(uuid.uuid4()),
            topic_history=[],
            level_history="beginner",
            avg_completion_rate=0.0,
            preferred_video_duration_seconds=900,
            total_curricula_generated=0,
        )
        session.add(new_session)
        session.commit()
        session.refresh(new_session)
        return new_session, True


def _get_personalization_weights(learning_session: LearningSession) -> dict[str, float]:
    """Get personalization weights based on user's completion history."""
    weights = {
        "relevance": 0.30,
        "beginner_friendliness": 0.22,
        "depth": 0.18,
        "engagement_quality": 0.10,
        "recency": 0.10,
        "performance_score": 0.10,
    }
    
    # Adjust based on avg_completion_rate
    if learning_session.avg_completion_rate < 0.5:
        # User struggles - favor beginner-friendly content
        weights["beginner_friendliness"] = min(0.27, weights["beginner_friendliness"] + 0.05)
        weights["depth"] = max(0.13, weights["depth"] - 0.05)
    elif learning_session.avg_completion_rate > 0.8:
        # User excels - favor deeper content
        weights["depth"] = min(0.23, weights["depth"] + 0.05)
        weights["beginner_friendliness"] = max(0.17, weights["beginner_friendliness"] - 0.05)
    
    # Normalize weights to still sum to 1.0
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def _analyze_feedback_for_regeneration(
    feedback: dict[str, Any],
) -> dict[str, Any]:
    """Analyze user feedback to determine curriculum regeneration parameters."""
    difficulty_rating = feedback.get("difficulty_rating", 0)
    pacing_rating = feedback.get("pacing_rating", 0)
    
    adjustments = {
        "regenerate": False,
        "adjust_difficulty": None,
        "adjust_pacing": None,
        "reason": [],
    }
    
    # If difficulty is too high or too low, regenerate
    if difficulty_rating == 1:  # Too easy
        adjustments["regenerate"] = True
        adjustments["adjust_difficulty"] = "increase"
        adjustments["reason"].append("Curriculum too easy; increasing difficulty")
    elif difficulty_rating == 5:  # Too hard
        adjustments["regenerate"] = True
        adjustments["adjust_difficulty"] = "decrease"
        adjustments["reason"].append("Curriculum too hard; decreasing difficulty")
    
    # If pacing is off, regenerate
    if pacing_rating == 1:  # Too fast
        adjustments["regenerate"] = True
        adjustments["adjust_pacing"] = "slower"
        adjustments["reason"].append("Pacing too fast; adding more depth per concept")
    elif pacing_rating == 5:  # Too slow
        adjustments["regenerate"] = True
        adjustments["adjust_pacing"] = "faster"
        adjustments["reason"].append("Pacing too slow; reducing depth, more breadth")
    
    return adjustments


def _regenerate_with_feedback(
    original_request: GenerateCurriculumRequest,
    feedback_analysis: dict[str, Any],
) -> GenerateCurriculumRequest:
    """Create modified curriculum request based on feedback."""
    modified = GenerateCurriculumRequest(
        goal=original_request.goal,
        level=original_request.level,
        hours_per_day=original_request.hours_per_day,
        session_id=original_request.session_id,
    )
    
    # Adjust level based on difficulty feedback
    if feedback_analysis.get("adjust_difficulty") == "increase":
        if modified.level == "beginner":
            modified.level = "intermediate"
        elif modified.level == "intermediate":
            modified.level = "advanced"
    elif feedback_analysis.get("adjust_difficulty") == "decrease":
        if modified.level == "advanced":
            modified.level = "intermediate"
        elif modified.level == "intermediate":
            modified.level = "beginner"
    
    # Adjust hours based on pacing feedback
    if feedback_analysis.get("adjust_pacing") == "slower":
        # Slower pace = reduce hours per day to allow more depth
        modified.hours_per_day = max(0.5, modified.hours_per_day * 0.7)
    elif feedback_analysis.get("adjust_pacing") == "faster":
        # Faster pace = increase hours per day for more breadth
        modified.hours_per_day = min(4.0, modified.hours_per_day * 1.3)
    
    return modified


def _enhance_curriculum_with_hands_on_projects(
    curriculum_payload: dict[str, Any],
) -> dict[str, Any]:
    """Enhance curriculum weeks with hands-on project suggestions for practical concepts."""
    from backend.models.moat_features import ProjectSuggestion
    
    try:
        with get_session() as session:
            # Collect all concepts in the curriculum
            concepts_in_curriculum = []
            for week in curriculum_payload.get("weeks", []):
                for day in week.get("days", []):
                    concept = day.get("concept", "").strip()
                    if concept:
                        concepts_in_curriculum.append(concept)
            
            if not concepts_in_curriculum:
                return curriculum_payload
            
            # Look up project suggestions for these concepts
            projects = session.exec(
                select(ProjectSuggestion)
                .where(ProjectSuggestion.concept.in_(concepts_in_curriculum))
            ).all()
            
            projects_by_concept = {}
            for project in projects:
                if project.concept not in projects_by_concept:
                    projects_by_concept[project.concept] = []
                projects_by_concept[project.concept].append(project)
            
            # Enhance days with project suggestions
            for week in curriculum_payload.get("weeks", []):
                for day in week.get("days", []):
                    concept = day.get("concept", "").strip()
                    if concept and concept in projects_by_concept:
                        # Add first project as primary hands-on component
                        project = projects_by_concept[concept][0]
                        day["hands_on_project"] = {
                            "name": project.project_name,
                            "description": project.project_description,
                            "github_url": project.github_repo_url,
                            "estimated_hours": project.estimated_hours,
                            "skills_practiced": project.skills_practiced,
                        }
                        logger.info(f"Added hands-on project '{project.project_name}' for concept '{concept}'")
            
            return curriculum_payload
    except Exception as exc:
        logger.warning(f"Could not enhance curriculum with projects: {exc}")
        return curriculum_payload


def _get_cached_concept_graph(topic: str, level: str) -> dict[str, Any] | None:
    """Get cached concept graph if it has 5+ validated nodes."""
    with get_session() as session:
        nodes = session.exec(
            select(ConceptNode).where(
                (ConceptNode.topic == topic) & (ConceptNode.level == level)
            )
        ).all()
        
        if len(nodes) < 5:
            return None

        concept_list = [
            {"name": node.concept, "prerequisites": node.prerequisites}
            for node in nodes
        ]
        if dependency_graph_service.graph_needs_repair(
            topic,
            concept_list,
            min(8, len(concept_list)),
        ):
            logger.info(
                "Skipping stale cached concept graph for topic=%s level=%s due to templated/generic concepts",
                topic,
                level,
            )
            return None
        
        # Build graph structure
        graph = {}
        for node in nodes:
            graph[node.concept] = {
                "name": node.concept,
                "prerequisites": node.prerequisites,
            }
        
        return graph


def _persist_concept_graph(topic: str, level: str, graph: list[dict[str, Any]]) -> None:
    """Persist a concept graph to the database for future use."""
    with get_session() as session:
        for concept_obj in graph:
            concept_name = concept_obj.get("name", "")
            prerequisites = concept_obj.get("prerequisites", [])
            
            if not concept_name:
                continue
            
            # Check if node exists
            existing = session.exec(
                select(ConceptNode).where(
                    (ConceptNode.topic == topic)
                    & (ConceptNode.concept == concept_name)
                    & (ConceptNode.level == level)
                )
            ).first()
            
            if existing:
                existing.prerequisites = prerequisites
                existing.times_validated += 1
                session.add(existing)
            else:
                node = ConceptNode(
                    topic=topic,
                    concept=concept_name,
                    level=level,
                    prerequisites=prerequisites,
                    times_validated=1,
                )
                session.add(node)
        
        session.commit()


def _filter_candidates_by_duration(
    candidates: list[dict[str, Any]],
    preferred_seconds: int,
    tolerance_seconds: int = 180,
) -> list[dict[str, Any]]:
    """Filter candidates to prefer videos close to preferred duration."""
    # Only filter if we have candidates
    if not candidates:
        return candidates
    
    preferred_range = (preferred_seconds - tolerance_seconds, preferred_seconds + tolerance_seconds)
    in_range = [c for c in candidates if preferred_range[0] <= c.get("duration_seconds", 0) <= preferred_range[1]]
    
    # Return in-range candidates, fallback to all if none match
    return in_range if in_range else candidates


def _filter_novel_concepts(concepts: list[dict[str, Any]], mastered_concepts: set[str]) -> list[dict[str, Any]]:
    """Remove concepts the learner has already mastered for this topic."""
    if not concepts or not mastered_concepts:
        return concepts

    mastered_normalized = {str(concept).strip().lower() for concept in mastered_concepts if str(concept).strip()}
    if not mastered_normalized:
        return concepts

    filtered: list[dict[str, Any]] = []
    for concept in concepts:
        name = str(concept.get("name", "")).strip().lower()
        if name and name not in mastered_normalized:
            filtered.append(concept)
    return filtered or concepts


def _generate_curriculum_payload(request: GenerateCurriculumRequest, job_id: uuid.UUID | None = None) -> tuple[dict[str, Any], str, str, list[dict[str, Any]], str]:
    """Generate curriculum payload with moat features (concept caching, personalization).
    
    Returns: (curriculum_payload, topic, cache_key, all_selected_videos, session_id)
    """
    curriculum_payload, topic, cache_key, all_selected_videos, session_id_result = curriculum_planner.build_curriculum(request)
    return curriculum_payload, topic, cache_key, all_selected_videos, session_id_result


def _run_generation_job(job_id: uuid.UUID, request_data: dict[str, Any]) -> None:
    logger.info(f"[JOB] Starting generation for job {job_id}")
    try:
        request = GenerateCurriculumRequest.model_validate(request_data)
        logger.info(f"[JOB] Request validated: goal={request.goal[:50]}")
    except Exception as exc:
        logger.error(f"[JOB] Failed to validate request for job {job_id}: {exc}", exc_info=True)
        _store_curriculum_job(job_id, status="failed", error_message=USER_FACING_FAILURE_MESSAGE)
        return

    job_start = time.perf_counter()
    last_progress_message: str | None = None

    def _update_progress(message: str) -> None:
        nonlocal last_progress_message
        if not message or message == last_progress_message:
            return
        last_progress_message = message
        _store_curriculum_job(job_id, status="running", result_json={"progress_message": message})

    try:
        _store_curriculum_job(job_id, status="running", result_json={"progress_message": "Starting curriculum generation"})
        logger.info(f"[JOB] Job {job_id} status set to running")
        
        payload_start = time.perf_counter()
        _update_progress("Calling Groq to design the curriculum")
        curriculum_payload, topic, cache_key, all_selected_videos, session_id = curriculum_planner.build_curriculum(request, progress_callback=_update_progress)
        payload_time = time.perf_counter() - payload_start
        logger.info(f"[PERF] _generate_curriculum_payload took {payload_time:.2f}s, generated {len(curriculum_payload.get('weeks', []))} weeks")

        if not all_selected_videos and curriculum_payload.get("curriculum_id"):
            safe_payload = _json_safe(curriculum_payload)
            safe_payload["cached"] = True
            redis_cache.set_json(cache_key, safe_payload, settings.curriculum_cache_ttl_seconds)
            _store_curriculum_job(
                job_id,
                status="completed",
                curriculum_id=uuid.UUID(str(safe_payload["curriculum_id"])),
                result_json={**safe_payload, "progress_message": "Curriculum ready"},
            )
            logger.info(f"[PERF] Job {job_id} completed (cache hit) in {time.perf_counter() - job_start:.2f}s")
            return

        concepts_in_curriculum: set[str] = set()
        with get_session() as session:
            row = Curriculum(
                goal_raw=request.goal,
                topic=topic,
                level=request.level,
                duration_days=curriculum_payload["duration_days"],
                curriculum_json=curriculum_payload,
                cache_key=cache_key,
            )
            session.add(row)
            try:
                session.commit()
                logger.info(f"[JOB] Created new curriculum row {row.id}")
            except IntegrityError:
                session.rollback()
                existing = session.exec(select(Curriculum).where(Curriculum.cache_key == cache_key)).first()
                if existing:
                    row = existing
                    logger.info(f"[JOB] Using existing curriculum row {row.id}")
                else:
                    raise
            session.refresh(row)
            
            # Update learning session with new topic and curriculum data
            learning_session = session.exec(
                select(LearningSession).where(LearningSession.session_id == session_id)
            ).first()
            if learning_session:
                if topic not in learning_session.topic_history:
                    learning_session.topic_history.append(topic)
                learning_session.level_history = request.level
                learning_session.total_curricula_generated += 1
                learning_session.last_active_at = datetime.utcnow()
                session.add(learning_session)
                session.commit()

            # Record concepts that are now being learned in this curriculum
            if curriculum_payload and curriculum_payload.get("weeks"):
                for week in curriculum_payload.get("weeks", []):
                    for day in week.get("days", []):
                        concept = day.get("concept", "").strip()
                        if concept:
                            concepts_in_curriculum.add(concept)

            if all_selected_videos:
                logger.info("[PERF] Skipping selected-video persistence in hot path (%d videos)", len(all_selected_videos))

            response_payload = _json_safe({"curriculum_id": row.id, **curriculum_payload})
            response_payload["social_proof"] = _curriculum_social_proof(session, row.id)

        if concepts_in_curriculum:
            _record_mastered_concepts(session_id, topic, list(concepts_in_curriculum), request.level)

        redis_cache.set_json(cache_key, response_payload, settings.curriculum_cache_ttl_seconds)
        _store_curriculum_job(job_id, status="completed", curriculum_id=row.id, result_json={**response_payload, "progress_message": "Curriculum ready"})
        track_event("curriculum_generated", {"goal": request.goal, "level": request.level, "curriculum_id": str(row.id), "cached": False})
        logger.info(f"[PERF] ===== JOB {job_id} COMPLETED IN {time.perf_counter() - job_start:.2f}s =====")
    except HTTPException as exc:
        logger.exception(f"[JOB] Curriculum job {job_id} failed after {time.perf_counter() - job_start:.2f}s with HTTPException: {exc}")
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail.strip() else USER_FACING_FAILURE_MESSAGE
        _store_curriculum_job(job_id, status="failed", error_message=detail)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[JOB] Curriculum job {job_id} failed after {time.perf_counter() - job_start:.2f}s with exception: {exc}")
        _store_curriculum_job(job_id, status="failed", error_message=USER_FACING_FAILURE_MESSAGE)


@router.post("/generate", response_model=GenerateCurriculumJobResponse)
def generate_curriculum(
    request: GenerateCurriculumRequest,
    background_tasks: BackgroundTasks,
    http_request: FastAPIRequest,
) -> GenerateCurriculumJobResponse:
    logger.warning("Generating curriculum for level=%s goal=%s", request.level, request.goal)
    try:
        identity = _request_identity(http_request, request.session_id)
        hourly_limit = max(1, int(settings.generation_hourly_limit_count))
        _enforce_generation_limits(http_request, request.session_id)
        remaining_requests = _remaining_generation_requests(identity, hourly_limit)

        with get_session() as session:
            existing = (
                session.exec(
                    select(Curriculum)
                    .where(Curriculum.goal_raw == request.goal)
                    .where(Curriculum.level == request.level)
                    .order_by(Curriculum.created_at.desc())
                )
                .first()
            )
            if existing:
                existing_version = str(existing.curriculum_json.get("generator_version", ""))
                if existing_version in {"v5-groq"}:
                    payload = _normalize_curriculum_estimates({"curriculum_id": existing.id, **existing.curriculum_json}, request.hours_per_day)
                    logger.warning("Curriculum raw-goal hit for topic=%s", existing.topic)
                    return GenerateCurriculumJobResponse(
                        job_id=existing.id,
                        status="completed",
                        curriculum_id=existing.id,
                        topic=existing.topic,
                        duration_days=payload.get("duration_days", existing.duration_days),
                        estimated_total_hours=payload.get("estimated_total_hours"),
                        estimated_video_hours=payload.get("estimated_video_hours"),
                        estimated_practice_hours=payload.get("estimated_practice_hours"),
                        estimated_review_hours=payload.get("estimated_review_hours"),
                        estimated_buffer_hours=payload.get("estimated_buffer_hours"),
                        warning=payload.get("warning"),
                        weeks=payload.get("weeks"),
                        can_expand=bool(payload.get("can_expand", False)),
                        poll_url=f"/api/v1/curriculum/{existing.id}",
                        remaining_requests=remaining_requests,
                        hourly_limit=hourly_limit,
                    )

        parsed = goal_parser.parse_goal(request.goal, request.level)
        if not isinstance(parsed, dict) or not parsed.get("topic"):
            logger.error("Goal parser returned invalid parse for goal=%s parsed=%s", request.goal, parsed)
            raise HTTPException(status_code=502, detail=USER_FACING_FAILURE_MESSAGE)
        topic = parsed["topic"]
        duration_weeks = int(parsed.get("duration_weeks", 3))
        cache_key = _curriculum_cache_key(topic, request.level, duration_weeks, request.hours_per_day)

        cached = redis_cache.get_json(cache_key)
        if cached and str(cached.get("generator_version", "")) in {"v5-groq"}:
            logger.warning("Curriculum cache hit for topic=%s", topic)
            curriculum_id = uuid.UUID(str(cached["curriculum_id"]))
            return GenerateCurriculumJobResponse(
                job_id=curriculum_id,
                status="completed",
                curriculum_id=curriculum_id,
                topic=cached.get("topic"),
                duration_days=cached.get("duration_days"),
                warning=cached.get("warning"),
                weeks=cached.get("weeks"),
                can_expand=bool(cached.get("can_expand", False)),
                poll_url=f"/api/v1/curriculum/{curriculum_id}",
                remaining_requests=remaining_requests,
                hourly_limit=hourly_limit,
            )

        with get_session() as session:
            existing = session.exec(select(Curriculum).where(Curriculum.cache_key == cache_key)).first()
            if existing:
                existing_version = str(existing.curriculum_json.get("generator_version", ""))
                if existing_version in {"v5-groq"}:
                    payload = {"curriculum_id": existing.id, **existing.curriculum_json}
                    redis_cache.set_json(cache_key, payload, settings.curriculum_cache_ttl_seconds)
                    logger.warning("Curriculum DB hit for topic=%s", topic)
                    existing_job = session.exec(select(CurriculumJob).where(CurriculumJob.cache_key == cache_key)).first()
                    if existing_job:
                        logger.warning("Curriculum job reuse for topic=%s status=%s job_id=%s", topic, existing_job.status, existing_job.id)
                        if existing_job.status == "failed":
                            logger.warning("[JOB REUSE] Ignoring failed job %s and creating a fresh run", existing_job.id)
                        elif existing_job.status not in ("completed", "failed"):
                            logger.info(f"[JOB REUSE] Re-triggering background task for non-terminal job {existing_job.id} status={existing_job.status}")
                            background_tasks.add_task(_run_generation_job, existing_job.id, request.model_dump())
                        else:
                            resp = _job_response(existing_job)
                            logger.info(f"[JOB REUSE] Returning job response: status={resp.status} curriculum_id={resp.curriculum_id}")
                            return resp

                    return GenerateCurriculumJobResponse(
                        job_id=existing.id,
                        status="completed",
                        curriculum_id=existing.id,
                        topic=existing.topic,
                        duration_days=existing.duration_days,
                        warning=existing.curriculum_json.get("warning"),
                        weeks=existing.curriculum_json.get("weeks"),
                        poll_url=f"/api/v1/curriculum/{existing.id}",
                        remaining_requests=remaining_requests,
                        hourly_limit=hourly_limit,
                    )

        with get_session() as session:
            # Check if job already exists with same cache_key (idempotent)
            existing_job = session.exec(
                select(CurriculumJob).where(CurriculumJob.cache_key == cache_key)
            ).first()
            if existing_job:
                logger.warning("Curriculum job exists for cache_key=%s, reusing job_id=%s status=%s", cache_key, existing_job.id, existing_job.status)
                # If job is not in a terminal state, re-trigger the background task
                # in case the previous task timed out or was never scheduled.
                if existing_job.status not in ("completed", "failed"):
                    logger.info("Re-triggering background task for non-terminal job %s", existing_job.id)
                    background_tasks.add_task(_run_generation_job, existing_job.id, request.model_dump())
                    return _job_response(existing_job)

                if existing_job.status == "failed":
                    logger.warning("Ignoring failed job %s for cache_key=%s and queueing a fresh job", existing_job.id, cache_key)
                    # cache_key is unique, so reset and reuse the failed job row.
                    existing_job.goal_raw = request.goal
                    existing_job.level = request.level
                    existing_job.hours_per_day = request.hours_per_day
                    existing_job.status = "queued"
                    existing_job.curriculum_id = None
                    existing_job.result_json = None
                    existing_job.error_message = None
                    existing_job.updated_at = datetime.utcnow()
                    session.commit()
                    session.refresh(existing_job)

                    background_tasks.add_task(_run_generation_job, existing_job.id, request.model_dump())
                    logger.warning("Curriculum job queued job_id=%s", existing_job.id)
                    return GenerateCurriculumJobResponse(
                        job_id=existing_job.id,
                        status="queued",
                        poll_url=f"/api/v1/curriculum/jobs/{existing_job.id}",
                        remaining_requests=remaining_requests,
                        hourly_limit=hourly_limit,
                    )

                return _job_response(existing_job)

            job = CurriculumJob(
                goal_raw=request.goal,
                level=request.level,
                hours_per_day=request.hours_per_day,
                status="queued",
                cache_key=cache_key,
            )
            session.add(job)
            try:
                session.commit()
                session.refresh(job)
            except IntegrityError:
                session.rollback()
                logger.warning(
                    "Race detected inserting curriculum job for cache_key=%s; reusing existing job",
                    cache_key,
                )
                existing_job = session.exec(
                    select(CurriculumJob).where(CurriculumJob.cache_key == cache_key)
                ).first()
                if not existing_job:
                    raise
                if existing_job.status not in ("completed", "failed"):
                    logger.info("Re-triggering background task for raced non-terminal job %s", existing_job.id)
                    background_tasks.add_task(_run_generation_job, existing_job.id, request.model_dump())
                elif existing_job.status == "failed":
                    logger.warning("Resetting raced failed job %s for cache_key=%s", existing_job.id, cache_key)
                    existing_job.goal_raw = request.goal
                    existing_job.level = request.level
                    existing_job.hours_per_day = request.hours_per_day
                    existing_job.status = "queued"
                    existing_job.curriculum_id = None
                    existing_job.result_json = None
                    existing_job.error_message = None
                    existing_job.updated_at = datetime.utcnow()
                    session.commit()
                    session.refresh(existing_job)
                    background_tasks.add_task(_run_generation_job, existing_job.id, request.model_dump())
                return _job_response(existing_job)

        background_tasks.add_task(_run_generation_job, job.id, request.model_dump())
        logger.warning("Curriculum job queued job_id=%s", job.id)
        return GenerateCurriculumJobResponse(
            job_id=job.id,
            status="queued",
            poll_url=f"/api/v1/curriculum/jobs/{job.id}",
            remaining_requests=remaining_requests,
            hourly_limit=hourly_limit,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        logger.warning("Curriculum generation dependency error: %s", exc)
        raise HTTPException(status_code=503, detail=USER_FACING_FAILURE_MESSAGE) from exc


@router.get("/jobs/{job_id}", response_model=GenerateCurriculumJobResponse)
def get_curriculum_job(job_id: str, background_tasks: BackgroundTasks) -> GenerateCurriculumJobResponse:
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid job id") from exc

    try:
        with get_session() as session:
            job = session.exec(select(CurriculumJob).where(CurriculumJob.id == job_uuid)).first()
            if not job:
                curriculum = session.exec(select(Curriculum).where(Curriculum.id == job_uuid)).first()
                if curriculum:
                    payload = {
                        "job_id": curriculum.id,
                        "status": "completed",
                        "curriculum_id": curriculum.id,
                        "topic": curriculum.topic,
                        "duration_days": curriculum.duration_days,
                        "warning": curriculum.curriculum_json.get("warning"),
                        "weeks": curriculum.curriculum_json.get("weeks"),
                        "poll_url": f"/api/v1/curriculum/jobs/{curriculum.id}",
                    }
                    return GenerateCurriculumJobResponse.model_validate(payload)
                raise HTTPException(status_code=404, detail="Job not found")

            if job.status in {"queued", "running"}:
                reference_time = job.updated_at or job.created_at
                age_seconds = (datetime.utcnow() - reference_time).total_seconds()
                if age_seconds >= JOB_STALE_SECONDS:
                    logger.warning(
                        "[JOB] Detected stale job %s status=%s age=%.1fs; re-triggering generation",
                        job.id,
                        job.status,
                        age_seconds,
                    )
                    _store_curriculum_job(job.id, status="queued", error_message=None)
                    background_tasks.add_task(
                        _run_generation_job,
                        job.id,
                        {
                            "goal": job.goal_raw,
                            "level": job.level,
                            "hours_per_day": job.hours_per_day,
                        },
                    )

                    refreshed = session.exec(select(CurriculumJob).where(CurriculumJob.id == job_uuid)).first()
                    if refreshed:
                        job = refreshed

            return _job_response(job)
    except SQLAlchemyError as exc:
        logger.warning("Job status lookup failed due to DB pool pressure for %s: %s", job_id, exc)
        raise HTTPException(status_code=503, detail="Database is busy. Please retry in a few seconds.") from exc


@router.get("/{curriculum_id}", response_model=GenerateCurriculumResponse)
def get_curriculum(curriculum_id: str) -> GenerateCurriculumResponse:
    try:
        curriculum_uuid = uuid.UUID(curriculum_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid curriculum id") from exc

    with get_session() as session:
        row = session.exec(select(Curriculum).where(Curriculum.id == curriculum_uuid)).first()
        if not row:
            raise HTTPException(status_code=404, detail="Curriculum not found")

        social_proof = _curriculum_social_proof(session, curriculum_uuid)
        payload = {
            "curriculum_id": row.id,
            **row.curriculum_json,
            "social_proof": social_proof,
        }
        return GenerateCurriculumResponse.model_validate(payload)


@router.get("/{curriculum_id}/playlists")
def get_curriculum_playlists(curriculum_id: str) -> dict[str, Any]:
    """Export curriculum videos as structured playlists by week/topic.
    
    Returns playlists that can be used to:
    - Create YouTube playlists
    - Export as JSON for sharing
    - Import into other learning platforms
    """
    try:
        curriculum_uuid = uuid.UUID(curriculum_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid curriculum id") from exc

    with get_session() as session:
        row = session.exec(select(Curriculum).where(Curriculum.id == curriculum_uuid)).first()
        if not row:
            raise HTTPException(status_code=404, detail="Curriculum not found")

        curriculum_data = row.curriculum_json
        weeks = curriculum_data.get("weeks", [])
        topic = curriculum_data.get("topic", "Curriculum")
        level = curriculum_data.get("level", "beginner")
        
        playlists = {
            "curriculum_id": str(curriculum_uuid),
            "topic": topic,
            "level": level,
            "duration_days": curriculum_data.get("duration_days", 0),
            "total_videos": 0,
            "videos": [],
            "playlists_by_week": []
        }
        
        video_count = 0
        for week in weeks:
            week_num = week.get("week", 0)
            week_theme = week.get("theme", f"Week {week_num}")
            week_playlist = {
                "week": week_num,
                "theme": week_theme,
                "videos": []
            }
            
            days = week.get("days", [])
            for day in days:
                day_num = day.get("day", 0)
                concept = day.get("concept", "")
                
                # Primary video
                video = day.get("video", {})
                if video and video.get("youtube_id"):
                    video_entry = {
                        "day": day_num,
                        "concept": concept,
                        "video_id": video.get("youtube_id"),
                        "title": video.get("title", ""),
                        "channel": video.get("channel", ""),
                        "duration_minutes": int(video.get("duration_seconds", 0) / 60),
                        "score": video.get("score", 0),
                        "position": "primary"
                    }
                    playlists["videos"].append(video_entry)
                    week_playlist["videos"].append(video_entry)
                    video_count += 1
                
                # Alternative video
                alt_video = day.get("alternative_video", {})
                if alt_video and alt_video.get("youtube_id"):
                    alt_entry = {
                        "day": day_num,
                        "concept": concept,
                        "video_id": alt_video.get("youtube_id"),
                        "title": alt_video.get("title", ""),
                        "channel": alt_video.get("channel", ""),
                        "duration_minutes": int(alt_video.get("duration_seconds", 0) / 60),
                        "score": alt_video.get("score", 0),
                        "position": "alternative"
                    }
                    playlists["videos"].append(alt_entry)
                    week_playlist["videos"].append(alt_entry)
                    video_count += 1
            
            if week_playlist["videos"]:
                playlists["playlists_by_week"].append(week_playlist)
        
        playlists["total_videos"] = video_count
        
        # Add YouTube playlist creation URLs for import
        primary_video_ids = [v["video_id"] for v in playlists["videos"] if v["position"] == "primary"]
        if primary_video_ids:
            youtube_playlist_url = f"https://www.youtube.com/watch_videos?video_ids={','.join(primary_video_ids[:50])}"
            playlists["youtube_import_url"] = youtube_playlist_url
        
        return playlists


@router.delete("/{curriculum_id}", response_model=dict)
def delete_curriculum(curriculum_id: str, session_id: str | None = None) -> dict:
    """Delete a curriculum if the caller owns the session stored on the curriculum.

    Ownership check: if the persisted curriculum has a `session_id` value, the caller
    must supply the same `session_id` (query param) to delete it. If the curriculum
    has no session_id, allow deletion.
    """
    try:
        curriculum_uuid = uuid.UUID(curriculum_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid curriculum id") from exc

    try:
        with get_session() as session:
            row = session.exec(select(Curriculum).where(Curriculum.id == curriculum_uuid)).first()
            if not row:
                raise HTTPException(status_code=404, detail="Curriculum not found")

            persisted_session = None
            try:
                persisted_session = (row.curriculum_json or {}).get("session_id")
            except Exception:
                persisted_session = None

            # If a session is associated, require the same session_id to delete
            if persisted_session:
                if not session_id or str(session_id) != str(persisted_session):
                    raise HTTPException(status_code=403, detail="Not authorized to delete this curriculum")

            # Delete related progress rows
            try:
                session.exec(delete(CurriculumProgress).where(CurriculumProgress.curriculum_id == curriculum_uuid))
            except Exception:
                logger.exception("Failed to delete curriculum progress rows")

            # Remove any job rows that reference this curriculum
            try:
                session.exec(delete(CurriculumJob).where(CurriculumJob.curriculum_id == curriculum_uuid))
            except Exception:
                logger.exception("Failed to delete related curriculum jobs")

            # Remove the curriculum row
            try:
                session.exec(delete(Curriculum).where(Curriculum.id == curriculum_uuid))
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Failed to delete curriculum row")
                raise HTTPException(status_code=500, detail="Failed to delete curriculum")

            # Remove cache entry if present
            try:
                if row.cache_key:
                    # Use the RedisCache helper to delete safely (it handles errors)
                    redis_cache.delete(row.cache_key)
            except Exception:
                logger.exception("Failed to clear redis cache for deleted curriculum")

        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error deleting curriculum: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete curriculum") from exc


@router.post("/{curriculum_id}/expand", response_model=GenerateCurriculumResponse)
def expand_curriculum(curriculum_id: str) -> GenerateCurriculumResponse:
    try:
        curriculum_uuid = uuid.UUID(curriculum_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid curriculum id") from exc

    with get_session() as session:
        row = session.exec(select(Curriculum).where(Curriculum.id == curriculum_uuid)).first()
        if not row:
            raise HTTPException(status_code=404, detail="Curriculum not found")

        current_payload = dict(row.curriculum_json or {})
        existing_weeks = list(current_payload.get("weeks", []))
        progression_concepts = current_payload.get("progression_concepts")

        if not isinstance(progression_concepts, list) or not progression_concepts:
            graph = dependency_graph_service.build_graph(row.topic, row.level, max(2, len(existing_weeks) + 1))
            progression_concepts = graph.get("concepts", [])

        if not isinstance(progression_concepts, list) or not progression_concepts:
            raise HTTPException(status_code=409, detail="No further content available yet")

        current_week_count = len(existing_weeks)
        next_start = current_week_count * CONCEPTS_PER_WEEK
        next_concepts = progression_concepts[next_start : next_start + CONCEPTS_PER_WEEK]
        if not next_concepts:
            raise HTTPException(status_code=409, detail="No further content available yet")

        last_existing_concept = None
        for week in reversed(existing_weeks):
            days = week.get("days", [])
            if days:
                last_existing_concept = days[-1].get("concept")
                if last_existing_concept:
                    break

        next_concepts = _chain_progression_concepts(next_concepts, last_existing_concept)

        session_id = current_payload.get("session_id")
        hours_per_day = float(current_payload.get("hours_per_day", 1.0))
        request = GenerateCurriculumRequest(
            goal=row.goal_raw,
            level=row.level,
            hours_per_day=hours_per_day,
            session_id=session_id,
        )

        learning_session, _ = _get_or_create_learning_session(session_id)
        existing_video_ids = _selected_video_ids_from_weeks(existing_weeks)
        assembled, quota_warning, _ = _generate_curriculum_slice(
            request=request,
            topic=row.topic,
            concepts=next_concepts,
            learning_session=learning_session,
            existing_video_ids=existing_video_ids,
        )

        new_weeks = assembled.get("weeks", [])
        for week in new_weeks:
            week["week"] = current_week_count + int(week.get("week", 0))

        updated_weeks = existing_weeks + new_weeks

        total_cumulative_seconds = 0
        for week in updated_weeks:
            for day in week.get("days", []):
                total_cumulative_seconds += day.get("video", {}).get("duration_seconds", 0)

        if total_cumulative_seconds > 0:
            actual_hours = total_cumulative_seconds / 3600.0
            hours_per_day_safe = max(hours_per_day, 0.25)
            actual_duration_days = math.ceil(actual_hours / hours_per_day_safe)
        else:
            actual_duration_days = row.duration_days

        total_videos = sum(len(week.get("days", [])) for week in updated_weeks)
        updated_payload = {
            **current_payload,
            "topic": row.topic,
            "level": row.level,
            "hours_per_day": hours_per_day,
            "duration_days": actual_duration_days,
            "description": _generate_curriculum_description(row.topic, row.level, updated_weeks, total_videos),
            "warning": quota_warning or current_payload.get("warning"),
            "weeks": updated_weeks,
            "progression_concepts": progression_concepts,
            "can_expand": len(progression_concepts) > len(updated_weeks) * CONCEPTS_PER_WEEK,
        }

        row.duration_days = actual_duration_days
        row.curriculum_json = updated_payload
        session.add(row)
        session.commit()

    if row.cache_key:
        redis_cache.set_json(row.cache_key, _json_safe(updated_payload), settings.curriculum_cache_ttl_seconds)

    payload = {"curriculum_id": row.id, **updated_payload}
    return GenerateCurriculumResponse.model_validate(payload)


@router.post("/{curriculum_id}/day/{day_number}/complete", response_model=DayCompletionResponse)
def mark_day_complete(curriculum_id: str, day_number: int) -> DayCompletionResponse:
    try:
        curriculum_uuid = uuid.UUID(curriculum_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid curriculum id") from exc

    with get_session() as session:
        # Verify curriculum exists
        curriculum = session.exec(select(Curriculum).where(Curriculum.id == curriculum_uuid)).first()
        if not curriculum:
            raise HTTPException(status_code=404, detail="Curriculum not found")

        # Check if day is already marked complete
        existing = session.exec(
            select(CurriculumProgress)
            .where(CurriculumProgress.curriculum_id == curriculum_uuid)
            .where(CurriculumProgress.day_number == day_number)
        ).first()

        if existing:
            return DayCompletionResponse(
                curriculum_id=curriculum_uuid,
                day_number=day_number,
                completed=True,
                completed_at=existing.completed_at,
            )

        # Create new completion record
        progress = CurriculumProgress(
            curriculum_id=curriculum_uuid,
            day_number=day_number,
        )
        session.add(progress)
        session.commit()
        session.refresh(progress)
        _invalidate_top_curricula_cache()

        return DayCompletionResponse(
            curriculum_id=curriculum_uuid,
            day_number=day_number,
            completed=True,
            completed_at=progress.completed_at,
        )


@router.get("/{curriculum_id}/progress")
def get_curriculum_progress(curriculum_id: str) -> dict[str, Any]:
    try:
        curriculum_uuid = uuid.UUID(curriculum_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid curriculum id") from exc

    with get_session() as session:
        curriculum = session.exec(select(Curriculum).where(Curriculum.id == curriculum_uuid)).first()
        if not curriculum:
            raise HTTPException(status_code=404, detail="Curriculum not found")

        completed_days = session.exec(
            select(CurriculumProgress).where(CurriculumProgress.curriculum_id == curriculum_uuid)
        ).all()

        completed_day_numbers = {day.day_number for day in completed_days}
        total_days = curriculum.duration_days

        return {
            "curriculum_id": curriculum_uuid,
            "total_days": total_days,
            "completed_day_numbers": sorted(list(completed_day_numbers)),
            "days_completed": len(completed_day_numbers),
            "progress_percentage": int((len(completed_day_numbers) / total_days * 100)) if total_days > 0 else 0,
        }


@router.post("/content/interaction", response_model=dict[str, Any])
def record_content_interaction(request_body: dict[str, Any]) -> dict[str, Any]:
    """Record user interaction with specific content (view, complete, skip, etc.)."""
    from backend.models.moat_features import ContentInteractionRequest, ContentInteractionResponse
    
    try:
        request = ContentInteractionRequest.model_validate(request_body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid request: {str(exc)}") from exc
    
    try:
        logger.info(f"Recording interaction: {request.interaction_type} on {request.source}:{request.content_id} for concept {request.concept}")

        session_id = request_body.get("session_id") or request_body.get("sessionId") or ""
        curriculum_id = request_body.get("curriculum_id") or request_body.get("curriculumId")

        # Persist the interaction
        try:
            cid = uuid.UUID(str(curriculum_id)) if curriculum_id else None
        except Exception:
            cid = None

        if cid:
            _record_content_interaction(
                session_id=session_id,
                curriculum_id=cid,
                content_id=request.content_id,
                source=request.source,
                concept=request.concept,
                interaction_type=request.interaction_type,
            )

        # Also update LearningSession viewed content list if session exists
        try:
            if session_id:
                with get_session() as session:
                    ls = session.exec(select(LearningSession).where(LearningSession.session_id == session_id)).first()
                    if ls:
                        if request.content_id not in (ls.viewed_content_ids or []):
                            ls.viewed_content_ids = (ls.viewed_content_ids or []) + [request.content_id]
                            session.add(ls)
                            session.commit()
        except Exception:
            logger.exception("Failed to update learning session viewed content")

        return {
            "success": True,
            "message": f"Recorded {request.interaction_type} interaction for content {request.content_id}",
        }
    except Exception as exc:
        logger.exception(f"Failed to record interaction: {exc}")
        raise HTTPException(status_code=500, detail="Failed to record interaction") from exc


@router.post("/{curriculum_id}/feedback", response_model=dict[str, Any])
def submit_curriculum_feedback(
    curriculum_id: str,
    feedback: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Submit feedback on curriculum and optionally trigger regeneration."""
    from backend.models.moat_features import CurriculumFeedback, LearningSession as SessionModel
    
    try:
        curriculum_uuid = uuid.UUID(curriculum_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid curriculum id") from exc
    
    try:
        with get_session() as session:
            curriculum = session.exec(select(Curriculum).where(Curriculum.id == curriculum_uuid)).first()
            if not curriculum:
                raise HTTPException(status_code=404, detail="Curriculum not found")
            
            # Save feedback to database
            fb = CurriculumFeedback(
                curriculum_id=curriculum_uuid,
                session_id=feedback.get("session_id", ""),
                difficulty_rating=feedback.get("difficulty_rating", 0),
                pacing_rating=feedback.get("pacing_rating", 0),
                content_quality_rating=feedback.get("content_quality_rating", 0),
                relevance_rating=feedback.get("relevance_rating", 0),
                concepts_to_deepen=feedback.get("concepts_to_deepen", []),
                concepts_to_skip=feedback.get("concepts_to_skip", []),
                additional_notes=feedback.get("additional_notes", ""),
            )
            session.add(fb)
            session.commit()
            _invalidate_top_curricula_cache()
            
            # Analyze feedback for regeneration
            analysis = _analyze_feedback_for_regeneration(feedback)
            
            response = {
                "success": True,
                "feedback_recorded": True,
                "analysis": analysis,
                "regenerate": analysis.get("regenerate", False),
            }
            
            # If regeneration is needed, trigger it
            if analysis.get("regenerate") and curriculum.curriculum_json:
                original_goal = curriculum.goal_raw
                original_level = curriculum.level
                original_hours = 1.0
                
                # Extract original request parameters
                if curriculum.curriculum_json:
                    original_hours = curriculum.curriculum_json.get("hours_per_day", 1.0)
                
                # Build modified request
                modified_request = GenerateCurriculumRequest(
                    goal=original_goal,
                    level=original_level,
                    hours_per_day=original_hours,
                    session_id=feedback.get("session_id"),
                )
                modified_request = _regenerate_with_feedback(modified_request, analysis)
                
                logger.info(f"Triggering curriculum regeneration based on feedback: {analysis.get('reason')}")
                response["regeneration_triggered"] = True
                response["regeneration_reason"] = "; ".join(analysis.get("reason", []))
                # TODO: Queue background job to regenerate
        
        return response
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to process feedback: {exc}")
        raise HTTPException(status_code=500, detail="Failed to process feedback") from exc
