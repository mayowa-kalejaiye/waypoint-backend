from __future__ import annotations

from typing import Any

from backend.config import get_settings
from backend.services.groq_client import groq_service


class LLMService:
    def __init__(self) -> None:
        self._settings = get_settings()

    def structured_json(self, prompt: str, max_retries: int | None = None) -> dict[str, Any]:
        retries = max_retries if max_retries is not None else self._settings.llm_max_retries
        return groq_service.structured_json(prompt, max_retries=max(1, retries))


llm_service = LLMService()
