from __future__ import annotations

import logging
from typing import Any
import requests

from backend.cache.redis_client import redis_cache
from backend.config import get_settings
from backend.services.domain_classifier import classify_domain
from backend.services.content_fetcher import ContentSource

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class DocumentationFetcher(ContentSource):
    """Fetch technical documentation from common sources like ReadTheDocs, official docs, etc."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def source_name(self) -> str:
        return "Documentation"

    def fetch_candidates(
        self,
        concepts: list[str],
        level: str = "beginner",
        context: str = "",
    ) -> tuple[list[dict[str, Any]], str | None]:
        """
        Fetch documentation pages. 
        Uses a simple heuristic: search for docs related to the topic/concepts.
        """
        
        domain = classify_domain(context or " ".join(concepts))
        all_docs: dict[str, dict[str, Any]] = {}
        quota_warning: str | None = None

        limited_concepts = concepts[:3]
        logger.info(f"[Docs] Fetching documentation for concepts={limited_concepts}, level={level}")

        for concept in limited_concepts:
            if len(all_docs) >= 10:
                break

            try:
                # Simple search strategy: look for official docs via google or simple query
                # For now, we'll create a curated list of common doc sources
                # In production, you'd use a search API or crawl known doc sites
                
                doc_suggestions = self._get_doc_suggestions(concept, level, domain)
                for doc in doc_suggestions:
                    doc_id = doc["doc_id"]
                    if doc_id not in all_docs:
                        all_docs[doc_id] = doc
                    if len(all_docs) >= 10:
                        break

                logger.debug(f"[Docs] Found {len(doc_suggestions)} doc suggestions for '{concept}'")
            except Exception as e:
                logger.warning(f"[Docs] Failed to fetch docs for concept '{concept}': {e}")
                continue

        candidates = list(all_docs.values())
        logger.info(f"[Docs] Total documentation items collected: {len(candidates)}")
        return candidates, quota_warning

    def _get_doc_suggestions(self, concept: str, level: str, domain: str) -> list[dict[str, Any]]:
        """Generate doc suggestions based on concept and level."""
        suggestions: list[dict[str, Any]] = []
        
        # Map common concepts to documentation sources
        concept_lower = concept.lower()

        if domain == "creative_arts":
            for doc_id, title, description, channel in [
                ("https://www.wikihow.com/", "WikiHow", "Practical step-by-step guides", "WikiHow"),
                ("https://www.almanac.com/gardening", "The Old Farmer's Almanac Gardening", "Gardening guidance and seasonal advice", "The Old Farmer's Almanac"),
                ("https://www.masterclass.com/articles", "MasterClass Articles", "Technique and process explainers", "MasterClass"),
            ]:
                suggestions.append({
                    "doc_id": doc_id,
                    "youtube_id": f"docs:{doc_id.split('//', 1)[-1].replace('/', '_').replace('.', '_')}",
                    "title": title,
                    "description": f"{description} for {concept}",
                    "channel": channel,
                    "duration_seconds": 0,
                    "view_count": 0,
                    "like_count": 0,
                    "published_at": "2024-01-01T00:00:00Z",
                    "thumbnail": "",
                    "source": "docs",
                    "url": doc_id,
                })
            return suggestions

        if domain == "business_professional":
            for doc_id, title, description, channel in [
                ("https://hbr.org/", "Harvard Business Review", "Leadership, management, and communication guidance", "Harvard Business Review"),
                ("https://www.toastmasters.org/", "Toastmasters", "Public speaking and presentation practice", "Toastmasters"),
                ("https://www.ted.com/topics/public+speaking", "TED Talks", "Presentation examples and talks", "TED"),
            ]:
                suggestions.append({
                    "doc_id": doc_id,
                    "youtube_id": f"docs:{doc_id.split('//', 1)[-1].replace('/', '_').replace('.', '_')}",
                    "title": title,
                    "description": f"{description} for {concept}",
                    "channel": channel,
                    "duration_seconds": 0,
                    "view_count": 0,
                    "like_count": 0,
                    "published_at": "2024-01-01T00:00:00Z",
                    "thumbnail": "",
                    "source": "docs",
                    "url": doc_id,
                })
            return suggestions
        
        # Python docs
        if "python" in concept_lower or "programming" in concept_lower:
            suggestions.append({
                "doc_id": "https://docs.python.org/3/",
                "youtube_id": "docs:python_official",
                "title": "Python Official Documentation",
                "description": "Official Python standard library and language reference",
                "channel": "Python.org",
                "duration_seconds": 0,
                "view_count": 0,
                "like_count": 0,
                "published_at": "2024-01-01T00:00:00Z",
                "thumbnail": "",
                "source": "docs",
                "url": "https://docs.python.org/3/",
            })
        
        # JavaScript/TypeScript docs
        if "javascript" in concept_lower or "typescript" in concept_lower or "web" in concept_lower:
            suggestions.append({
                "doc_id": "https://developer.mozilla.org/en-US/docs/",
                "youtube_id": "docs:mdn_web_docs",
                "title": "MDN Web Docs",
                "description": "Mozilla Developer Network—comprehensive web development documentation",
                "channel": "Mozilla",
                "duration_seconds": 0,
                "view_count": 0,
                "like_count": 0,
                "published_at": "2024-01-01T00:00:00Z",
                "thumbnail": "",
                "source": "docs",
                "url": "https://developer.mozilla.org/en-US/docs/",
            })
        
        # Django docs
        if "django" in concept_lower or "web framework" in concept_lower:
            suggestions.append({
                "doc_id": "https://docs.djangoproject.com/",
                "youtube_id": "docs:django_official",
                "title": "Django Official Documentation",
                "description": "The Web framework for perfectionists with deadlines",
                "channel": "Django Software Foundation",
                "duration_seconds": 0,
                "view_count": 0,
                "like_count": 0,
                "published_at": "2024-01-01T00:00:00Z",
                "thumbnail": "",
                "source": "docs",
                "url": "https://docs.djangoproject.com/",
            })
        
        # React docs
        if "react" in concept_lower or "frontend" in concept_lower:
            suggestions.append({
                "doc_id": "https://react.dev/",
                "youtube_id": "docs:react_official",
                "title": "React Official Documentation",
                "description": "A JavaScript library for building user interfaces",
                "channel": "Meta",
                "duration_seconds": 0,
                "view_count": 0,
                "like_count": 0,
                "published_at": "2024-01-01T00:00:00Z",
                "thumbnail": "",
                "source": "docs",
                "url": "https://react.dev/",
            })
        
        # Kubernetes docs
        if "kubernetes" in concept_lower or "k8s" in concept_lower:
            suggestions.append({
                "doc_id": "https://kubernetes.io/docs/",
                "youtube_id": "docs:k8s_official",
                "title": "Kubernetes Official Documentation",
                "description": "Production-grade container orchestration",
                "channel": "Cloud Native Computing Foundation",
                "duration_seconds": 0,
                "view_count": 0,
                "like_count": 0,
                "published_at": "2024-01-01T00:00:00Z",
                "thumbnail": "",
                "source": "docs",
                "url": "https://kubernetes.io/docs/",
            })
        
        # Docker docs
        if "docker" in concept_lower or "container" in concept_lower:
            suggestions.append({
                "doc_id": "https://docs.docker.com/",
                "youtube_id": "docs:docker_official",
                "title": "Docker Official Documentation",
                "description": "Containerize your application with Docker",
                "channel": "Docker",
                "duration_seconds": 0,
                "view_count": 0,
                "like_count": 0,
                "published_at": "2024-01-01T00:00:00Z",
                "thumbnail": "",
                "source": "docs",
                "url": "https://docs.docker.com/",
            })
        
        return suggestions


docs_fetcher = DocumentationFetcher()
