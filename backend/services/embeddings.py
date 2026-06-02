from __future__ import annotations

import logging
from typing import Any, List, Optional

import numpy as np
import openai

from backend.cache.redis_client import redis_cache
from backend.config import get_settings

logger = logging.getLogger(__name__)


class EmbeddingsService:
    def __init__(self) -> None:
        settings = get_settings()
        self._provider = settings.embeddings_provider or "openai"
        self._api_key = settings.embeddings_api_key
        self._model = settings.embeddings_model or "text-embedding-3-small"
        if self._provider == "openai":
            openai.api_key = self._api_key

    def _cache_key(self, prefix: str, key: str) -> str:
        return f"embed:{prefix}:{key}"

    def embed_text(self, text: str, cache_prefix: Optional[str] = None, cache_key: Optional[str] = None) -> List[float]:
        """Return embedding vector for text. If cache_prefix/key provided, try redis cache."""
        if cache_prefix and cache_key:
            existing = redis_cache.get_json(self._cache_key(cache_prefix, cache_key))
            if existing and isinstance(existing, list):
                return existing

        if self._provider != "openai":
            raise RuntimeError("No supported embeddings provider configured")

        # Call OpenAI embeddings API
        resp = openai.Embedding.create(model=self._model, input=text)
        vec = resp["data"][0]["embedding"]

        if cache_prefix and cache_key:
            try:
                redis_cache.set_json(self._cache_key(cache_prefix, cache_key), vec, ttl_seconds=60 * 60 * 24 * 7)
            except Exception:
                logger.debug("Failed to cache embedding")

        return vec

    def similarity(self, a: List[float], b: List[float]) -> float:
        a_np = np.array(a, dtype=float)
        b_np = np.array(b, dtype=float)
        if a_np.size == 0 or b_np.size == 0:
            return 0.0
        denom = (np.linalg.norm(a_np) * np.linalg.norm(b_np))
        if denom == 0:
            return 0.0
        return float(np.dot(a_np, b_np) / denom)


embeddings_service = EmbeddingsService()
