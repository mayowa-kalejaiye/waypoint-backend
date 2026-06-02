from __future__ import annotations

import logging
import re
from typing import List

from fastapi import APIRouter, Query

from backend.services.groq_client import groq_service
from backend.services.youtube_fetcher import YouTubeQuotaExceededError, youtube_fetcher

logger = logging.getLogger(__name__)
router = APIRouter()


def _query_tokens(text: str) -> set[str]:
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "your",
        "you",
        "that",
        "this",
        "learn",
        "learning",
        "video",
        "videos",
        "how",
        "what",
        "best",
    }
    tokens = {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}
    return {token for token in tokens if token not in stop_words}


def _looks_relevant(query: str, candidate: dict) -> bool:
    query_terms = _query_tokens(query)
    if not query_terms:
        return True

    title_terms = _query_tokens(str(candidate.get("title", "")))
    channel_terms = _query_tokens(str(candidate.get("channel", "")))
    url_terms = _query_tokens(str(candidate.get("url", "")))

    overlap = query_terms & (title_terms | channel_terms | url_terms)
    if overlap:
        return True

    preferred_query = str(candidate.get("preferred_youtube_query") or "")
    if preferred_query:
        preferred_terms = _query_tokens(preferred_query)
        return bool(query_terms & preferred_terms)

    return False


@router.get("/api/v1/rank")
def rank(
    query: str = Query(..., min_length=3),
    level: str = Query("beginner"),
    domain: str = Query("tech_development"),
    max_results: int = 12,
) -> dict:
    """Return ranked YouTube video candidates for a free-text learning query.

    Attempts to call Groq to generate per-node preferred queries/ids, then
    fetches video details and ranks by duration, view count, and recency.
    Falls back to direct YouTube search if Groq is not available.
    """
    videos: List[dict] = []

    try:
        plan = groq_service.call_groq(goal=query, level=level, daily_minutes=30, domain=domain)
        per_node = plan.get("youtube_strategy", {}).get("per_node", [])

        collected: List[dict] = []
        for entry in per_node:
            preferred_url = entry.get("preferred_youtube_url")
            preferred_id = entry.get("preferred_youtube_id")
            preferred_title = entry.get("preferred_youtube_title")
            if preferred_url:
                candidate = {
                    "title": preferred_title or query,
                    "youtube_id": preferred_id or "",
                    "url": preferred_url,
                    "channel": entry.get("preferred_youtube_channel") or "Groq",
                    "source": "youtube",
                    "preferred_youtube_query": entry.get("preferred_youtube_query") or "",
                }
                if _looks_relevant(query, candidate):
                    collected.append(candidate)
                continue

            if preferred_id:
                candidate = {
                    "title": preferred_title or query,
                    "youtube_id": preferred_id,
                    "url": f"https://www.youtube.com/watch?v={preferred_id}",
                    "channel": entry.get("preferred_youtube_channel") or "Groq",
                    "source": "youtube",
                    "preferred_youtube_query": entry.get("preferred_youtube_query") or "",
                }
                if _looks_relevant(query, candidate):
                    collected.append(candidate)
                continue

            search_terms: List[str] = []
            preferred_query = str(entry.get("preferred_youtube_query") or "").strip()
            if preferred_query:
                search_terms.append(preferred_query)
            if preferred_title:
                search_terms.append(str(preferred_title).strip())
            for candidate_query in (entry.get("search_queries") or [])[:3]:
                query_value = str(candidate_query).strip()
                if query_value and query_value not in search_terms:
                    search_terms.append(query_value)

            for search_term in search_terms[:5]:
                try:
                    results = youtube_fetcher.search_query(search_term, max_results=1)
                except YouTubeQuotaExceededError as exc:
                    logger.warning("YouTube quota exhausted while searching for %s: %s", search_term, exc)
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.warning("YouTube search failed for %s: %s", search_term, exc)
                    continue

                if not results:
                    continue

                video = results[0]
                candidate = {
                    "title": video.get("title") or preferred_title or query,
                    "youtube_id": video.get("youtube_id") or "",
                    "url": video.get("url") or (f"https://www.youtube.com/watch?v={video.get('youtube_id')}" if video.get("youtube_id") else ""),
                    "channel": video.get("channel") or entry.get("preferred_youtube_channel") or "Groq",
                    "source": "youtube",
                    "preferred_youtube_query": search_term,
                    "duration_seconds": video.get("duration_seconds", 0),
                    "view_count": video.get("view_count", 0),
                    "published_at": video.get("published_at", ""),
                }
                if _looks_relevant(query, candidate):
                    collected.append(candidate)
                    break

        videos = collected
    except Exception as exc:  # graceful fallback when Groq fails or is misconfigured
        logger.warning("Groq ranking unavailable: %s", exc)

    # Scoring: prefer longer videos (>=10m), then higher view_count, then recency
    def score(v: dict) -> tuple[int, int, float]:
        long = 1 if (v.get("duration_seconds", 0) >= 600) else 0
        views = int(v.get("view_count", 0) or 0)
        # recency boost: newer videos get a small boost (published_at iso)
        published = v.get("published_at")
        recency = 0.0
        try:
            from dateutil import parser as _p
            import datetime as _dt

            if published:
                dt = _p.parse(published)
                days = (_dt.datetime.utcnow() - dt).days
                recency = max(0.0, 1.0 - min(days / 365.0, 1.0))
        except Exception:
            recency = 0.0
        return (long, views, recency)

    ranked = sorted(videos, key=score, reverse=True)
    return {"videos": ranked}
