from __future__ import annotations

import re
import hashlib
import time
from typing import Any

from backend.services.llm_client import llm_service


class GoalParser:
    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_timestamps: dict[str, float] = {}
        self._cache_ttl_seconds = 3600  # 1 hour cache
    
    def _get_cache_key(self, goal: str, level: str) -> str:
        """Generate a cache key for goal + level."""
        source = f"{goal}:{level}"
        return hashlib.sha256(source.encode()).hexdigest()[:16]
    
    def _get_cached(self, cache_key: str) -> dict[str, Any] | None:
        """Get cached result if it exists and hasn't expired."""
        if cache_key not in self._cache:
            return None
        
        age = time.time() - self._cache_timestamps.get(cache_key, 0)
        if age > self._cache_ttl_seconds:
            del self._cache[cache_key]
            return None
        
        return self._cache[cache_key]
    
    def _set_cache(self, cache_key: str, result: dict[str, Any]) -> None:
        """Cache a parsed goal result."""
        self._cache[cache_key] = result
        self._cache_timestamps[cache_key] = time.time()
    
    def parse_goal(self, goal: str, level: str) -> dict[str, Any]:
        """Parse a learning goal with Groq-backed structured JSON and caching."""
        cache_key = self._get_cache_key(goal, level)
        
        # Check cache first
        cached_result = self._get_cached(cache_key)
        if cached_result:
            return cached_result
        
        prompt = f"""
You are a curriculum planner.
Extract a learning goal into a JSON object with keys:
- topic (string)
- duration_weeks (integer)
- level (string)
- focus_areas (array of strings)

Input goal: {goal}
User level: {level}
If duration is missing in the goal, default to 3 weeks.
"""
        # Use Groq for all parsing so the curriculum topic stays aligned with the model output.
        result = llm_service.structured_json(prompt, max_retries=2)

        if not isinstance(result, dict) or not result.get("topic"):
            raise RuntimeError("Groq returned malformed parse without topic")
        
        result["level"] = result.get("level") or level
        result["duration_weeks"] = int(result.get("duration_weeks") or 3)
        if not result.get("focus_areas"):
            result["focus_areas"] = [result.get("topic", goal)]
        
        self._set_cache(cache_key, result)
        return result


goal_parser = GoalParser()
