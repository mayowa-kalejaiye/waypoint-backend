from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
import logging

from backend.services.domain_classifier import classify_domain

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


POETRY_TERMS = (
    "poetry",
    "poem",
    "poems",
    "poet",
    "poets",
    "sonnet",
    "haiku",
    "literary analysis",
    "verse",
)


def _is_poetry_request(text: str) -> bool:
    lowered = (text or "").lower()
    return any(term in lowered for term in POETRY_TERMS)


def _is_poetry_candidate(candidate: dict[str, Any]) -> bool:
    text = " ".join(
        str(candidate.get(field, ""))
        for field in ("title", "description", "channel", "source")
    ).lower()
    return any(term in text for term in POETRY_TERMS)


def _filter_poetry_candidates(
    candidates: list[dict[str, Any]],
    concepts: list[str],
    context: str,
) -> list[dict[str, Any]]:
    request_text = " ".join([context, *concepts])
    if _is_poetry_request(request_text):
        return candidates
    return [candidate for candidate in candidates if not _is_poetry_candidate(candidate)]


class ContentSource(ABC):
    """Base class for content sources."""

    @abstractmethod
    def fetch_candidates(
        self,
        concepts: list[str],
        level: str = "beginner",
        context: str = "",
    ) -> tuple[list[dict[str, Any]], str | None]:
        """
        Fetch candidate content for concepts.
        
        Returns: (list of candidates with youtube_id/paper_id/doc_id, optional warning)
        Each candidate should have: title, description, duration_seconds, source, url, etc.
        """
        pass

    @abstractmethod
    def source_name(self) -> str:
        """Return the name of this content source."""
        pass


class MultiSourceContentFetcher:
    """Coordinator that fetches from multiple sources based on topic/level."""

    def __init__(self) -> None:
        # Import here to avoid circular imports
        from backend.services.youtube_fetcher import youtube_fetcher
        from backend.services.arxiv_fetcher import arxiv_fetcher
        from backend.services.docs_fetcher import docs_fetcher
        from backend.services.github_fetcher import github_fetcher
        
        self.sources: list[ContentSource] = [youtube_fetcher, docs_fetcher, github_fetcher, arxiv_fetcher]
        self._settings = None
        logger.info(f"Initialized MultiSourceContentFetcher with {len(self.sources)} sources")

    def add_source(self, source: ContentSource) -> None:
        """Register a new content source."""
        self.sources.append(source)
        logger.info(f"Registered content source: {source.source_name()}")

    def fetch_candidates(
        self,
        concepts: list[str],
        level: str = "beginner",
        context: str = "",
    ) -> tuple[list[dict[str, Any]], str | None]:
        """
        Fetch candidates from all sources and combine results.
        
        Prioritizes sources based on topic applicability.
        """
        all_candidates: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []

        domain = classify_domain(context or " ".join(concepts))
        active_sources = []
        for source in self.sources:
            if domain != "tech_development" and source.source_name() == "GitHub Repos":
                continue
            active_sources.append(source)

        for source in active_sources:
            try:
                logger.info(f"Fetching from {source.source_name()} for concepts={concepts[:2]}")
                candidates, warning = source.fetch_candidates(
                    concepts,
                    level=level,
                    context=context,
                )
                
                if warning:
                    warnings.append(f"{source.source_name()}: {warning}")
                
                # Deduplicate by unique identifier (youtube_id, paper_id, doc_id, etc.)
                for candidate in candidates:
                    unique_id = candidate.get("youtube_id") or candidate.get("paper_id") or candidate.get("doc_id")
                    if unique_id and unique_id not in all_candidates:
                        all_candidates[unique_id] = candidate
                    
                logger.info(f"{source.source_name()} contributed {len(candidates)} candidates")
            except Exception as e:
                logger.warning(f"Failed to fetch from {source.source_name()}: {e}")
                warnings.append(f"{source.source_name()} failed: {str(e)}")
                continue

        combined = list(all_candidates.values())
        combined = _filter_poetry_candidates(combined, concepts, context)
        combined_warning = " | ".join(warnings) if warnings else None
        logger.info(f"Total candidates from all sources: {len(combined)}")
        return combined, combined_warning


content_fetcher = MultiSourceContentFetcher()


def fetch_creative_arts_resources(search_term: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Domain-specific fetcher for creative arts topics using YouTube + practical guides."""
    from backend.services.youtube_fetcher import youtube_fetcher
    from backend.services.docs_fetcher import docs_fetcher

    videos = youtube_fetcher.search_query(f"{search_term} step by step", max_results=max_results)
    docs, _ = docs_fetcher.fetch_candidates([search_term], level="beginner", context=search_term)
    return (videos + docs)[:max_results]


def fetch_business_professional_resources(search_term: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Domain-specific fetcher for business/professional topics using videos + trusted references."""
    from backend.services.youtube_fetcher import youtube_fetcher
    from backend.services.docs_fetcher import docs_fetcher

    videos = youtube_fetcher.search_query(f"{search_term} case study", max_results=max_results)
    docs, _ = docs_fetcher.fetch_candidates([search_term], level="intermediate", context=search_term)
    return (videos + docs)[:max_results]


def fetch_tech_resources(search_term: str, max_results: int = 5) -> list[dict[str, Any]]:
    from backend.services.github_fetcher import github_fetcher
    from backend.services.docs_fetcher import docs_fetcher

    repos, _ = github_fetcher.fetch_candidates([search_term], level="intermediate", context=search_term)
    docs, _ = docs_fetcher.fetch_candidates([search_term], level="intermediate", context=search_term)
    return (repos + docs)[:max_results]


def fetch_generic_resources(search_term: str, max_results: int = 5) -> list[dict[str, Any]]:
    from backend.services.youtube_fetcher import youtube_fetcher

    return youtube_fetcher.search_query(search_term, max_results=max_results)


def fetch_resources_for_domain(domain: str, search_term: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Route resource fetching to domain-appropriate handlers."""
    fetcher_map = {
        "tech_development": fetch_tech_resources,
        "creative_arts": fetch_creative_arts_resources,
        "business_professional": fetch_business_professional_resources,
    }
    fetcher = fetcher_map.get(domain, fetch_generic_resources)
    return fetcher(search_term, max_results)
