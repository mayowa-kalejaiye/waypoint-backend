from __future__ import annotations

import math
import re
from typing import Any

from sqlmodel import select
from youtube_transcript_api import YouTubeTranscriptApi

from backend.db.database import get_session
from backend.models.video import Video


class TranscriptAnalyzer:
    def _store_transcript(self, youtube_id: str, transcript_text: str) -> None:
        if not transcript_text.strip():
            return

        with get_session() as session:
            row = session.exec(select(Video).where(Video.youtube_id == youtube_id)).first()
            if not row:
                return

            row.transcript_cached = True
            row.transcript_text = transcript_text
            session.add(row)
            session.commit()

    def _normalize_transcript_segments(self, transcript: list[dict[str, Any]]) -> str:
        return " ".join(segment.get("text", "").strip() for segment in transcript if segment.get("text"))

    def get_or_fetch_transcript(self, youtube_id: str) -> dict[str, Any]:
        with get_session() as session:
            existing = session.exec(select(Video).where(Video.youtube_id == youtube_id)).first()
            if existing and existing.transcript_text:
                return {"available": True, "transcript_text": existing.transcript_text}

        try:
            transcript: list[dict[str, Any]] | None = None

            try:
                transcript = YouTubeTranscriptApi.get_transcript(youtube_id, languages=["en", "en-US", "en-GB"])
            except TypeError:
                transcript = YouTubeTranscriptApi.get_transcript(youtube_id)
            except Exception:
                transcript = None

            if transcript is None:
                try:
                    transcript_list = YouTubeTranscriptApi.list_transcripts(youtube_id)
                    for language_code in ("en", "en-US", "en-GB"):
                        try:
                            transcript = transcript_list.find_transcript([language_code]).fetch()
                            break
                        except Exception:
                            continue
                    if transcript is None:
                        try:
                            transcript = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"]).fetch()
                        except Exception:
                            transcript = None
                except Exception:
                    transcript = None

            if transcript:
                transcript_text = self._normalize_transcript_segments(transcript)
                if transcript_text.strip():
                    self._store_transcript(youtube_id, transcript_text)
                    return {"available": True, "transcript_text": transcript_text}

            return {"available": False, "transcript_text": ""}
        except Exception:
            return {"available": False, "transcript_text": ""}

    def _reading_level(self, text: str) -> float:
        words = re.findall(r"\w+", text)
        sentences = re.split(r"[.!?]+", text)
        if not words or not sentences:
            return 0.0
        avg_word_len = sum(len(w) for w in words) / len(words)
        avg_sentence_len = len(words) / max(len([s for s in sentences if s.strip()]), 1)
        return round((avg_word_len * 0.6) + (avg_sentence_len * 0.4), 3)

    def analyze(self, video: dict[str, Any], concept: str, prerequisites: list[str], allow_fetch: bool = True) -> dict[str, Any]:
        if allow_fetch:
            transcript_payload = self.get_or_fetch_transcript(video["youtube_id"])
            text = transcript_payload.get("transcript_text", "")
            transcript_available = transcript_payload.get("available", False)

            if not transcript_available or not text.strip():
                text = f"{video.get('title', '')} {video.get('description', '')}"
        else:
            text = f"{video.get('title', '')} {video.get('description', '')}"
            transcript_available = False

        words = re.findall(r"\w+", text.lower())
        word_count = len(words)
        duration_minutes = max(video.get("duration_seconds", 0) / 60.0, 1)
        wpm = word_count / duration_minutes

        prereq_hits = any(pr.lower() in text.lower() for pr in prerequisites)
        has_code_examples = bool(
            re.search(r"\b(code|function|component|class|const|let|import|return)\b", text, re.I)
        )

        # Heuristic overlap score against concept tokens.
        concept_tokens = set(re.findall(r"\w+", concept.lower()))
        token_hits = sum(1 for token in words if token in concept_tokens)
        relevance_overlap = token_hits / max(math.sqrt(len(words) + 1), 1)

        return {
            "transcript_available": transcript_available,
            "transcript_text": text if transcript_available else "",
            "reading_level": self._reading_level(text),
            "mentions_prerequisites": prereq_hits,
            "has_code_examples": has_code_examples,
            "content_density": round(wpm, 2),
            "relevance_overlap": relevance_overlap,
            "transcript_word_count": word_count,
        }


transcript_analyzer = TranscriptAnalyzer()
