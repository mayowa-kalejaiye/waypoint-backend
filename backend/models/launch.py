"""Launch-phase engagement and waitlist models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class LaunchWaitlistEntry(SQLModel, table=True):
    """Captured launch waitlist contact."""

    __tablename__ = "launch_waitlist_entries"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(max_length=320, index=True, unique=True)
    name: str = Field(default="", max_length=120)
    source: str = Field(default="launch_waitlist", max_length=80, index=True)
    session_id: str = Field(default="", max_length=100, index=True)
    properties: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LaunchAnalyticsEvent(SQLModel, table=True):
    """Anonymous launch analytics event capture."""

    __tablename__ = "launch_analytics_events"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    event_name: str = Field(max_length=120, index=True)
    session_id: str = Field(default="", max_length=100, index=True)
    page_path: str = Field(default="", max_length=200, index=True)
    properties: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WaitlistSignupRequest(SQLModel):
    email: str
    name: str = ""
    source: str = "launch_waitlist"
    session_id: str = ""
    page_path: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class LaunchEventRequest(SQLModel):
    event_name: str
    session_id: str = ""
    page_path: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class LaunchResponse(SQLModel):
    success: bool
    message: str | None = None


class LaunchUsageBucket(SQLModel):
    label: str
    count: int


class LaunchUsageSummary(SQLModel):
    total_events: int
    total_waitlist_signups: int
    unique_sessions: int
    top_events: list[LaunchUsageBucket]
    top_pages: list[LaunchUsageBucket]
