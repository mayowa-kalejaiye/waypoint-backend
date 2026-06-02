from __future__ import annotations

import datetime as dt
import isodate
from typing import Any

import requests

from backend.cache.redis_client import redis_cache
from backend.config import get_settings


import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class YouTubeQuotaExceededError(RuntimeError):
    pass


class YouTubeFetcher:
    """YouTube content source."""
    
    SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
    VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

    def source_name(self) -> str:
        return "YouTube"

    def __init__(self) -> None:
        self._settings = get_settings()

    def generate_queries(self, concept: str, level: str = "beginner") -> list[str]:
        # Prioritize exact-phrase matching, then broaden with level-specific variants.
        concept = concept.strip()
        base = [f'"{concept}"', concept]
        if level.lower() == "beginner":
            base.append(f"{concept} for beginners")
        elif level.lower() == "advanced":
            base.append(f"{concept} advanced")
        # Preserve order while removing duplicates.
        seen: set[str] = set()
        unique_queries: list[str] = []
        for query in base:
            normalized = query.lower().strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_queries.append(query)
        return unique_queries

    def _search_video_ids(self, query: str) -> list[str]:
        if not self._settings.youtube_data_api_key:
            return []

        response = requests.get(
            self.SEARCH_URL,
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": self._settings.youtube_search_max_results,
                "key": self._settings.youtube_data_api_key,
            },
            timeout=self._settings.youtube_request_timeout_seconds,
        )
        payload = response.json()
        if response.status_code == 403 and "quota" in str(payload).lower():
            raise YouTubeQuotaExceededError("YouTube API quota exceeded")
        response.raise_for_status()
        return [item["id"]["videoId"] for item in payload.get("items", []) if item.get("id", {}).get("videoId")]

    def _video_details(self, video_ids: list[str]) -> list[dict[str, Any]]:
        if not video_ids or not self._settings.youtube_data_api_key:
            return []

        response = requests.get(
            self.VIDEOS_URL,
            params={
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(video_ids),
                "key": self._settings.youtube_data_api_key,
                "maxResults": 50,
            },
            timeout=self._settings.youtube_request_timeout_seconds,
        )
        payload = response.json()
        if response.status_code == 403 and "quota" in str(payload).lower():
            raise YouTubeQuotaExceededError("YouTube API quota exceeded")
        response.raise_for_status()

        videos: list[dict[str, Any]] = []
        for item in payload.get("items", []):
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            duration_iso = item.get("contentDetails", {}).get("duration", "PT0S")
            duration_seconds = int(isodate.parse_duration(duration_iso).total_seconds())
            videos.append(
                {
                    "youtube_id": item.get("id"),
                    "title": snippet.get("title", ""),
                    "description": snippet.get("description", ""),
                    "channel": snippet.get("channelTitle", ""),
                    "duration_seconds": duration_seconds,
                    "view_count": int(stats.get("viewCount", 0)),
                    "like_count": int(stats.get("likeCount", 0)) if stats.get("likeCount") else 0,
                    "published_at": snippet.get("publishedAt", dt.datetime.utcnow().isoformat()),
                    "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url"),
                    "transcript_available": False,
                    "transcript_text": "",
                }
            )

        # Fast transcript enrichment for top videos only (keeps latency low).
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
            transcript_api_available = True
        except Exception:
            transcript_api_available = False

        if transcript_api_available and videos:
            fetch_limit = min(3, len(videos))
            for idx in range(fetch_limit):
                vid = videos[idx]
                vid_id = str(vid.get("youtube_id") or "")
                if not vid_id:
                    continue
                try:
                    transcript_list = YouTubeTranscriptApi.get_transcript(vid_id, languages=["en"])  # may raise
                    text = " ".join(segment.get("text", "") for segment in transcript_list)
                    vid["transcript_available"] = bool(text.strip())
                    vid["transcript_text"] = text
                except Exception:
                    # non-fatal; leave transcript flags False/empty
                    continue

        return videos

    def search_query(self, query: str, max_results: int | None = None) -> list[dict[str, Any]]:
        """Search YouTube with an explicit query and return hydrated video records."""
        if not self._settings.youtube_data_api_key:
            logger.warning("[YT] Skipping search for '%s' because YOUTUBE_DATA_API_KEY is not configured", query)
            return []

        limit = max_results if max_results is not None else self._settings.youtube_search_max_results
        limit = max(1, min(int(limit), 20))
        video_ids = self._search_video_ids(query)[:limit]
        return self._video_details(video_ids)

    def fetch_candidates(self, concepts: list[str], level: str = "beginner", context: str = "") -> tuple[list[dict[str, Any]], str | None]:
        all_candidates: dict[str, dict[str, Any]] = {}
        quota_warning: str | None = None
        logger.info(f"[YT] Fetching candidates for concepts={concepts}, level={level}")

        limited_concepts = concepts[:4]

        for concept in limited_concepts:
            if len(all_candidates) >= self._settings.max_video_candidates:
                break

            for query in self.generate_queries(concept, level):
                if len(all_candidates) >= self._settings.max_video_candidates:
                    break

                cache_key = f"youtube:query:{query}".lower()
                logger.debug(f"[YT] Query: {query}")
                cached = redis_cache.get_json(cache_key)
                if cached:
                    logger.debug(f"[YT] Cache hit for '{query}': {len(cached)} results")
                    for item in cached:
                        all_candidates[item["youtube_id"]] = item
                        if len(all_candidates) >= self._settings.max_video_candidates:
                            break
                    continue

                try:
                    remaining = max(self._settings.max_video_candidates - len(all_candidates), 1)
                    search_limit = min(self._settings.youtube_search_max_results, remaining)
                    ids = self._search_video_ids(query)[:search_limit]
                    logger.debug(f"[YT] Got {len(ids)} video IDs for '{query}'")
                    details = self._video_details(ids)
                    logger.info(f"[YT] Got {len(details)} video details for '{query}'")
                    redis_cache.set_json(
                        cache_key, details, ttl_seconds=self._settings.video_cache_ttl_seconds
                    )
                    for item in details:
                        all_candidates[item["youtube_id"]] = item
                        if len(all_candidates) >= self._settings.max_video_candidates:
                            break
                except YouTubeQuotaExceededError:
                    quota_warning = "YouTube API quota exceeded. Using cached results only."
                except Exception as e:
                    logger.warning(f"[YT] Query '{query}' failed: {e}")
                    continue

        concept_terms = {
            term
            for concept in concepts
            for term in concept.lower().split()
            if len(term) > 2
        }
        generic_terms = {
            "basics", "basic", "workflow", "example", "examples", "pattern", "patterns",
            "practice", "review", "guide", "intro", "introduction", "advanced", "mastery",
            "tutorial", "course", "lesson", "tips", "how", "to", "for",
        }
        anchor_terms = concept_terms - generic_terms

        def _relevance(video: dict[str, Any]) -> tuple[int, int, int, int]:
            text = f"{video.get('title', '')} {video.get('description', '')}".lower()
            anchor_hits = sum(1 for term in anchor_terms if term in text)
            concept_hits = sum(1 for term in concept_terms if term in text)
            title_anchor_hits = sum(1 for term in anchor_terms if term in str(video.get("title", "")).lower())
            exact_phrase = 1 if any(concept.lower() in str(video.get("title", "")).lower() for concept in concepts) else 0
            return (exact_phrase, title_anchor_hits, anchor_hits, concept_hits)

        candidates = sorted(
            all_candidates.values(),
            key=lambda v: (
                _relevance(v)[0],
                _relevance(v)[1],
                _relevance(v)[2],
                _relevance(v)[3],
                v.get("view_count", 0),
                v.get("like_count", 0),
            ),
            reverse=True,
        )
        logger.info(f"[YT] Total candidates collected: {len(candidates)}")
        return candidates, quota_warning


youtube_fetcher = YouTubeFetcher()
