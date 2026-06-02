from __future__ import annotations

import logging
from typing import Any
import requests

from backend.cache.redis_client import redis_cache
from backend.config import get_settings
from backend.services.content_fetcher import ContentSource
from backend.services.domain_classifier import classify_domain

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class GitHubRepoFetcher(ContentSource):
    """Fetch GitHub repositories and documentation for technical topics."""

    GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"

    def __init__(self) -> None:
        self._settings = get_settings()

    def source_name(self) -> str:
        return "GitHub Repos"

    def fetch_candidates(
        self,
        concepts: list[str],
        level: str = "beginner",
        context: str = "",
    ) -> tuple[list[dict[str, Any]], str | None]:
        """
        Fetch GitHub repositories for technical topics.
        Prioritizes well-documented, starred projects.
        """
        
        domain = classify_domain(context or " ".join(concepts))
        if domain != "tech_development":
            logger.info("[GitHub] Skipping repo fetch for non-tech domain=%s", domain)
            return [], None

        all_repos: dict[str, dict[str, Any]] = {}
        quota_warning: str | None = None

        limited_concepts = concepts[:3]
        logger.info(f"[GitHub] Fetching repos for concepts={limited_concepts}, level={level}")

        for concept in limited_concepts:
            if len(all_repos) >= 12:  # Cap at 12 repos
                break

            try:
                # Search for popular repos matching concept
                query = f"{concept} stars:>50 is:public"
                headers = {}
                if self._settings.github_api_token:
                    headers["Authorization"] = f"token {self._settings.github_api_token}"
                
                response = requests.get(
                    self.GITHUB_SEARCH_URL,
                    params={"q": query, "sort": "stars", "order": "desc", "per_page": 10},
                    headers=headers,
                    timeout=self._settings.youtube_request_timeout_seconds,
                )
                
                if response.status_code == 403:
                    quota_warning = "GitHub API rate limit exceeded"
                    logger.warning("GitHub API rate limited")
                    continue
                    
                response.raise_for_status()
                data = response.json()

                for repo in data.get("items", []):
                    repo_numeric_id = repo.get("id")
                    repo_full_name = repo.get("full_name", "")
                    if not repo_numeric_id or repo_numeric_id in all_repos:
                        continue
                    if not repo_full_name:
                        continue
                    
                    all_repos[str(repo_numeric_id)] = {
                        "repo_id": repo_full_name,
                        "youtube_id": f"github:{repo_numeric_id}",  # Normalized for dedup
                        "title": repo.get("name", ""),
                        "description": repo.get("description", "")[:500],
                        "channel": repo.get("owner", {}).get("login", "GitHub"),
                        "duration_seconds": 0,  # Repos don't have duration
                        "view_count": repo.get("stargazers_count", 0),
                        "like_count": repo.get("forks_count", 0),
                        "published_at": repo.get("created_at", "2024-01-01T00:00:00Z"),
                        "thumbnail": "",
                        "source": "github",
                        "url": repo.get("html_url", ""),
                    }
                    
                    if len(all_repos) >= 12:
                        break

                logger.info(f"[GitHub] Found {len(all_repos)} repos for concept '{concept}'")
            except Exception as e:
                logger.warning(f"[GitHub] Failed to fetch repos for concept '{concept}': {e}")
                continue

        candidates = list(all_repos.values())
        logger.info(f"[GitHub] Total repos collected: {len(candidates)}")
        return candidates, quota_warning


github_fetcher = GitHubRepoFetcher()
