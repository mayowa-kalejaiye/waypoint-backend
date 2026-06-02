from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, Text, JSON
from sqlmodel import Field, SQLModel


class Video(SQLModel, table=True):
    __tablename__ = "videos"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    youtube_id: str = Field(index=True, unique=True, max_length=20)
    title: str
    channel_name: str = Field(max_length=200)
    duration_seconds: int
    view_count: int
    like_count: int | None = None
    published_at: datetime
    transcript_cached: bool = False
    transcript_text: str | None = Field(default=None, sa_column=Column(Text))
    score_cache: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
