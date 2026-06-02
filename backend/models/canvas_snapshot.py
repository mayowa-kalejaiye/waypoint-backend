from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class CanvasSnapshot(SQLModel, table=True):
    """Persisted canvas state for shareable learning maps."""

    __tablename__ = "canvas_snapshots"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    share_token: str = Field(max_length=64, unique=True, index=True)
    session_id: str | None = Field(default=None, max_length=100, index=True)
    title: str | None = Field(default=None, max_length=200)
    payload: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
    access_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
