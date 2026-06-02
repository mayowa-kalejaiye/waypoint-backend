from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any

from backend.config import get_settings

logger = logging.getLogger(__name__)


def _estimate_reading_time(content: dict[str, Any]) -> int:
    """Estimate reading time in minutes for non-video content based on word count."""
    word_count = content.get("word_count", 0)
    page_count = content.get("page_count", 0)
    
    # Standard reading speeds
    if word_count > 0:
        # Average reading speed: 200-250 words per minute
        return max(5, math.ceil(word_count / 225))
    elif page_count > 0:
        # Average: 250 words per page = ~1.2 minutes per page
        return max(5, math.ceil(page_count * 1.2))
    
    # Default estimates by source
    source = content.get("source", "").lower()
    if "arxiv" in source or "paper" in source:
        return 30  # Academic papers average 20-40 minutes
    elif "github" in source or "repo" in source:
        return 20  # Repos average 15-30 minutes
    elif "doc" in source:
        return 15  # Docs average 10-20 minutes
    
    return 10  # Fallback


def _get_content_source_type(content: dict[str, Any]) -> tuple[str, str | None]:
    """Determine content source type and extract the primary ID.
    Returns (source_type, primary_id)
    """
    # Prefer explicit non-YouTube IDs first.
    # Some fetchers keep a synthetic youtube_id only for normalization/dedup.
    if content.get("paper_id"):
        return "paper", content.get("paper_id")
    elif content.get("repo_id"):
        repo_id = str(content.get("repo_id") or "")
        if "/" in repo_id:
            return "repo", repo_id
        repo_url = str(content.get("url") or "")
        if "github.com/" in repo_url:
            return "repo", repo_url.split("github.com/", 1)[1].strip("/")
        return "repo", repo_id
    elif content.get("doc_id"):
        doc_id = str(content.get("doc_id") or "")
        if doc_id.startswith("http://") or doc_id.startswith("https://"):
            return "doc", doc_id
        doc_url = str(content.get("url") or "")
        return "doc", doc_url or doc_id
    elif content.get("youtube_id"):
        youtube_id = str(content.get("youtube_id") or "")
        if youtube_id.startswith("github:"):
            return "repo", content.get("repo_id") or youtube_id.replace("github:", "", 1)
        if youtube_id.startswith("arxiv:"):
            return "paper", content.get("paper_id") or youtube_id.replace("arxiv:", "", 1)
        if youtube_id.startswith("docs:"):
            return "doc", content.get("doc_id") or youtube_id.replace("docs:", "", 1)
        return "youtube", content.get("youtube_id")
    
    # Fallback: check source field
    source = content.get("source", "").lower()
    if "arxiv" in source:
        return "paper", content.get("paper_id") or content.get("id")
    elif "github" in source:
        return "repo", content.get("repo_id") or content.get("id")
    elif "doc" in source:
        return "doc", content.get("doc_id") or content.get("id")
    
    return "youtube", content.get("youtube_id") or content.get("id")


class PathAssembler:
    def __init__(self) -> None:
        self._settings = get_settings()

    def _build_video_out(self, content: dict[str, Any] | None, concept: str | None = None) -> dict[str, Any] | None:
        """Build a VideoOut object with proper source type and reading time handling."""
        if not content:
            return None
        
        source_type, primary_id = _get_content_source_type(content)
        lesson_title = str(concept or content.get("title") or "").strip()
        if not lesson_title:
            lesson_title = str(content.get("title") or "").strip()
        
        video_out = {
            "source_type": source_type,
            "title": lesson_title if source_type != "youtube" else content["title"],
            "channel": content["channel"],
            "url": content.get("url"),
            "duration_seconds": content.get("duration_seconds", 0),
            "score": content["score"],
            "why_this_video": content["why_this_video"],
            "transcript_available": content.get("transcript_available", False),
            "duration_advice": content.get("duration_advice"),
        }
        
        # Set the appropriate ID field based on source type
        if source_type == "youtube":
            video_out["youtube_id"] = primary_id
        elif source_type == "paper":
            video_out["paper_id"] = primary_id
        elif source_type == "repo":
            video_out["repo_id"] = primary_id
        elif source_type == "doc":
            video_out["doc_id"] = primary_id
        
        # Add reading time for non-video content
        if source_type != "youtube":
            reading_time = _estimate_reading_time(content)
            video_out["reading_time_minutes"] = reading_time
        
        return video_out

    def assemble(
        self,
        concepts: list[dict[str, Any]],
        scored_by_concept: dict[str, list[dict[str, Any]]],
        hours_per_day: float,
        prefer_long_videos: bool = False,
    ) -> dict[str, Any]:
        """
        Assemble curriculum with variable-length days based on content difficulty.

        Adds light source diversification so users can see repos/docs/papers in
        addition to videos when quality is comparable.
        """
        default_practice_hours = 0.75
        review_hours_per_concept = 0.25
        hours_per_day_safe = max(hours_per_day, 0.25)

        channel_usage: dict[str, int] = defaultdict(int)
        used_video_ids: set[str] = set()
        debug_logs: list[str] = []

        def _unique_id_for(candidate: dict[str, Any]) -> str:
            return str(
                candidate.get("youtube_id")
                or candidate.get("paper_id")
                or candidate.get("repo_id")
                or candidate.get("id")
                or ""
            ).strip()

        source_usage: dict[str, int] = defaultdict(int)

        def _source_type_for(candidate: dict[str, Any]) -> str:
            if candidate.get("paper_id"):
                return "paper"
            if candidate.get("repo_id"):
                return "repo"
            if candidate.get("doc_id"):
                return "doc"
            return "youtube"

        day_entries: list[dict[str, Any]] = []
        day_number = 1

        def _length_multiplier(candidate: dict[str, Any]) -> float:
            """Boost long-form content and mildly de-prioritize very short clips.

            Rules:
            - >= 3600s (1h+): boost (e.g., 1.3x)
            - 1800-3599s (30-60m): slight boost (1.1x)
            - <= 900s (15m or less): reduce (0.7x)
            - otherwise: 1.0
            """
            if not candidate:
                return 1.0
            duration = int(candidate.get("duration_seconds") or 0)
            long_thresh = int(self._settings.long_video_threshold_seconds or 3600)
            mid_thresh = int(self._settings.mid_video_threshold_seconds or 1800)
            short_thresh = int(self._settings.short_video_threshold_seconds or 900)

            # Base multipliers from settings
            long_base = float(self._settings.long_video_multiplier or 1.5)
            mid_base = float(self._settings.mid_video_multiplier or 1.2)
            short_base = float(self._settings.short_video_multiplier or 0.6)

            # Per-request scaling: if the user prefers long videos, boost long/mid and further penalize short
            if prefer_long_videos:
                long_base *= 1.25
                mid_base *= 1.15
                short_base *= 0.85

            if duration >= long_thresh:
                return long_base
            if duration >= mid_thresh:
                return mid_base
            if duration <= short_thresh and duration > 0:
                return short_base
            return 1.0

        def _adjusted_score(candidate: dict[str, Any]) -> float:
            base = float(candidate.get("score", candidate.get("final_score", 0.0)) or 0.0)
            return base * _length_multiplier(candidate)

        for concept_obj in concepts:
            concept = concept_obj["name"] if isinstance(concept_obj, dict) else str(concept_obj)
            candidates = scored_by_concept.get(concept, [])
            if not candidates:
                continue

            youtube_candidates = [candidate for candidate in candidates if _source_type_for(candidate) == "youtube"]

            eligible: list[dict[str, Any]] = []
            for candidate in candidates:
                candidate_id = _unique_id_for(candidate)
                channel = candidate.get("channel", "")
                if candidate_id and candidate_id in used_video_ids:
                    continue
                if channel_usage[channel] >= self._settings.max_channel_repeat:
                    continue
                eligible.append(candidate)

            if not eligible:
                continue

            # Prefer a real YouTube video as the primary pick whenever one exists.
            if youtube_candidates:
                chosen = max(youtube_candidates, key=lambda item: _adjusted_score(item))
            else:
                chosen = eligible[0]
            chosen_score = _adjusted_score(chosen)
            chosen_source = _source_type_for(chosen)

            # If curriculum is too YouTube-heavy, prefer a non-YouTube item when close in score.
            total_selected = max(sum(source_usage.values()), 1)
            youtube_ratio = source_usage.get("youtube", 0) / total_selected
            if youtube_ratio >= 0.7 and chosen_source == "youtube":
                for candidate in eligible[1:]:
                    candidate_source = _source_type_for(candidate)
                    candidate_score = float(candidate.get("score", 0.0))
                    if candidate_source != "youtube" and candidate_score >= (chosen_score * 0.85):
                        chosen = candidate
                        chosen_score = candidate_score
                        chosen_source = candidate_source
                        break

            alternative = None
            for candidate in eligible:
                candidate_id = _unique_id_for(candidate)
                chosen_id = _unique_id_for(chosen)
                if not candidate_id or candidate_id == chosen_id:
                    continue

                candidate_score = _adjusted_score(candidate)
                if candidate_score < (chosen_score * 0.82):
                    continue

                # Prefer alternates from a different source type.
                if _source_type_for(candidate) != chosen_source:
                    alternative = candidate
                    break
                if alternative is None:
                    alternative = candidate

            chosen_id = _unique_id_for(chosen)
            if chosen_id:
                used_video_ids.add(chosen_id)
            channel_usage[chosen.get("channel", "")] += 1
            source_usage[chosen_source] += 1

            if alternative:
                alternative_id = _unique_id_for(alternative)
                if alternative_id and alternative_id not in used_video_ids and alternative_id != chosen_id:
                    used_video_ids.add(alternative_id)
                else:
                    alternative = None

            day_entries.append(
                {
                    "day": day_number,
                    "concept": concept,
                    "video": self._build_video_out(chosen, concept),
                    "alternative_video": self._build_video_out(alternative, concept) if alternative else None,
                }
            )
            day_number += 1

        # Ensure a minimum number of surfaced items so users always get multiple options
        MIN_SURFACED = 4
        if len(day_entries) < MIN_SURFACED:
            # Debug: record current state
            msg = f"day_entries_short: current={len(day_entries)}, MIN_SURFACED={MIN_SURFACED}"
            debug_logs.append(msg)
            logger.debug(msg)
            msg = f"used_video_ids({len(used_video_ids)}): {sorted(list(used_video_ids))}"
            debug_logs.append(msg)
            logger.debug(msg)
            msg = f"source_usage: {dict(source_usage)}"
            debug_logs.append(msg)
            logger.debug(msg)

            # Build a flat list of remaining high-quality candidates across concepts
            pool: list[tuple[dict[str, Any], str]] = []
            for c_name, c_list in scored_by_concept.items():
                for cand in c_list:
                    cand_id = _unique_id_for(cand)
                    if not cand_id:
                        debug_logs.append(f"skip_candidate_no_id: concept={c_name} title={cand.get('title')}")
                        logger.debug("skip_candidate_no_id: concept=%s title=%s", c_name, cand.get('title'))
                        continue
                    if cand_id in used_video_ids:
                        debug_logs.append(f"skip_candidate_used: id={cand_id} title={cand.get('title')} concept={c_name}")
                        logger.debug("skip_candidate_used: id=%s title=%s concept=%s", cand_id, cand.get('title'), c_name)
                        continue
                    pool.append((cand, c_name))

            # Sort by score descending
            pool.sort(key=lambda x: _adjusted_score(x[0]), reverse=True)
            debug_logs.append(f"pool_size_after_filter={len(pool)} top_ids={[ _unique_id_for(p[0]) for p in pool[:6]]}")
            logger.debug("pool_size_after_filter=%d top_ids=%s", len(pool), [ _unique_id_for(p[0]) for p in pool[:6]])

            # If pool is still small, allow reusing candidates from earlier days (for diversity within reason)
            if len(pool) < (MIN_SURFACED - len(day_entries)):
                debug_logs.append(f"pool_too_small: allowing reuse of top candidates from used_video_ids")
                logger.debug("pool_too_small: allowing reuse of top candidates from used_video_ids")
                # Rebuild pool with all candidates, sorted by score, including used ones
                pool_with_reuse: list[tuple[dict[str, Any], str]] = []
                for c_name, c_list in scored_by_concept.items():
                    for cand in c_list:
                        cand_id = _unique_id_for(cand)
                        if not cand_id:
                            continue
                        pool_with_reuse.append((cand, c_name))
                pool_with_reuse.sort(key=lambda x: _adjusted_score(x[0]), reverse=True)
                # Skip candidates we've already used
                pool = [p for p in pool_with_reuse if _unique_id_for(p[0]) not in used_video_ids]

            for cand, c_name in pool:
                if len(day_entries) >= MIN_SURFACED:
                    break
                cand_id = _unique_id_for(cand)
                if not cand_id:
                    continue

                # Prefer diversity: skip if same source repeated many times
                cand_source = _source_type_for(cand)
                if source_usage.get(cand_source, 0) > max(1, len(day_entries) // 2):
                    # allow if score is high
                    if float(cand.get("score", 0.0)) < 0.6:
                        debug_logs.append(f"skip_candidate_diversity: id={cand_id} source={cand_source} score={cand.get('score')}")
                        logger.debug("skip_candidate_diversity: id=%s source=%s score=%s", cand_id, cand_source, cand.get('score'))
                        continue

                used_video_ids.add(cand_id)
                channel_usage[cand.get("channel", "")] += 1
                source_usage[cand_source] += 1

                day_entries.append(
                    {
                        "day": day_number,
                        "concept": c_name,
                        "video": self._build_video_out(cand, c_name),
                        "alternative_video": None,
                    }
                )
                debug_logs.append(f"padded_with: id={cand_id} title={cand.get('title')} concept={c_name} source={cand_source} score={cand.get('score')}")
                logger.debug("padded_with: id=%s title=%s concept=%s source=%s score=%s", cand_id, cand.get('title'), c_name, cand_source, cand.get('score'))
                day_number += 1

        weeks: list[dict[str, Any]] = []
        for index in range(0, len(day_entries), 7):
            week_days = day_entries[index : index + 7]
            week_number = (index // 7) + 1
            theme = week_days[0]["concept"] if week_days else "Core Concepts"
            weeks.append({"week": week_number, "theme": theme, "days": week_days})

        # Calculate total time including both video and reading time
        total_video_seconds = 0
        total_reading_minutes = 0
        
        for day in day_entries:
            video = day.get("video", {})
            if video:
                total_video_seconds += (video.get("duration_seconds") or 0)
                total_reading_minutes += (video.get("reading_time_minutes") or 0)
        
        total_video_hours = total_video_seconds / 3600.0
        total_reading_hours = total_reading_minutes / 60.0
        
        # Content hours = video + reading time
        total_content_hours = total_video_hours + total_reading_hours

        practice_hours = 0.0
        for concept_obj in concepts:
            if isinstance(concept_obj, dict):
                practice_hours += float(concept_obj.get("estimated_hours", default_practice_hours) or default_practice_hours)
            else:
                practice_hours += default_practice_hours

        review_hours = len(concepts) * review_hours_per_concept
        buffer_hours = max(0.1 * (total_content_hours + practice_hours + review_hours), 0.1)
        estimated_total_hours = total_content_hours + practice_hours + review_hours + buffer_hours
        estimated_days = max(math.ceil(estimated_total_hours / hours_per_day_safe), len(day_entries) or 1)

        return {
            "duration_days": estimated_days,
            "estimated_total_hours": round(estimated_total_hours, 2),
            "estimated_video_hours": round(total_video_hours, 2),
            "estimated_reading_hours": round(total_reading_hours, 2),
            "estimated_practice_hours": round(practice_hours, 2),
            "estimated_review_hours": round(review_hours, 2),
            "estimated_buffer_hours": round(buffer_hours, 2),
            "weeks": weeks,
            **({"_debug": debug_logs} if self._settings.app_debug else {}),
        }


path_assembler = PathAssembler()
