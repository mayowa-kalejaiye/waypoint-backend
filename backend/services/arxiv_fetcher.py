from __future__ import annotations

import logging
from typing import Any
import requests
import xml.etree.ElementTree as ET

from backend.cache.redis_client import redis_cache
from backend.config import get_settings
from backend.services.content_fetcher import ContentSource

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ArxivPaperFetcher(ContentSource):
    """Fetch academic papers from ArXiv, useful for advanced/intermediate levels."""

    ARXIV_SEARCH_URL = "http://export.arxiv.org/api/query"

    def __init__(self) -> None:
        self._settings = get_settings()

    def source_name(self) -> str:
        return "ArXiv Papers"

    def fetch_candidates(
        self,
        concepts: list[str],
        level: str = "beginner",
        context: str = "",
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch papers from ArXiv. Only useful for intermediate/advanced levels."""
        
        # Skip ArXiv for beginner level—too technical
        if level.lower() == "beginner":
            logger.debug("Skipping ArXiv for beginner level")
            return [], None

        all_papers: dict[str, dict[str, Any]] = {}
        quota_warning: str | None = None

        # Fetch papers for first 2 concepts only
        limited_concepts = concepts[:2]
        logger.info(f"[ArXiv] Fetching papers for concepts={limited_concepts}, level={level}")

        for concept in limited_concepts:
            if len(all_papers) >= 15:  # Cap at 15 papers
                break

            try:
                # Search for papers matching concept
                query = f'all:"{concept}"'
                response = requests.get(
                    self.ARXIV_SEARCH_URL,
                    params={"search_query": query, "start": 0, "max_results": 10, "sortBy": "relevanceRank"},
                    timeout=self._settings.youtube_request_timeout_seconds,
                )
                response.raise_for_status()

                # Parse XML response
                root = ET.fromstring(response.content)
                # Handle ArXiv's namespace
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                
                for entry in root.findall("atom:entry", ns):
                    paper_id_elem = entry.find("atom:id", ns)
                    title_elem = entry.find("atom:title", ns)
                    summary_elem = entry.find("atom:summary", ns)
                    published_elem = entry.find("atom:published", ns)
                    author_elem = entry.find("atom:author/atom:name", ns)
                    
                    if paper_id_elem is None or title_elem is None:
                        continue
                    
                    paper_id = paper_id_elem.text.split("/abs/")[-1] if paper_id_elem.text else None
                    if not paper_id or paper_id in all_papers:
                        continue
                    
                    all_papers[paper_id] = {
                        "paper_id": paper_id,
                        "youtube_id": f"arxiv:{paper_id}",  # Normalized for dedup
                        "title": title_elem.text or "Untitled",
                        "description": (summary_elem.text or "")[:500],
                        "channel": author_elem.text if author_elem is not None else "ArXiv Authors",
                        "duration_seconds": 0,  # Papers don't have duration
                        "view_count": 0,
                        "like_count": 0,
                        "published_at": published_elem.text if published_elem is not None else "2024-01-01T00:00:00Z",
                        "thumbnail": "",
                        "source": "arxiv",
                        "url": f"https://arxiv.org/abs/{paper_id}",
                    }
                    
                    if len(all_papers) >= 15:
                        break

                logger.info(f"[ArXiv] Got {len(all_papers)} papers for concept '{concept}'")
            except Exception as e:
                logger.warning(f"[ArXiv] Failed to fetch papers for concept '{concept}': {e}")
                quota_warning = f"ArXiv search failed: {str(e)}"
                continue

        candidates = list(all_papers.values())
        logger.info(f"[ArXiv] Total papers collected: {len(candidates)}")
        return candidates, quota_warning


arxiv_fetcher = ArxivPaperFetcher()
