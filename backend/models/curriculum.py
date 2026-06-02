from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class Curriculum(SQLModel, table=True):
    __tablename__ = "curriculums"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    goal_raw: str
    topic: str = Field(max_length=200)
    level: str = Field(max_length=50)
    duration_days: int
    curriculum_json: dict[str, Any] = Field(sa_column=Column(JSON))
    cache_key: str = Field(max_length=100, unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DependencyGraph(SQLModel, table=True):
    __tablename__ = "dependency_graphs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    topic: str = Field(max_length=200, index=True)
    level: str = Field(max_length=50, index=True)
    graph_json: dict[str, Any] = Field(sa_column=Column(JSON))
    expires_at: datetime


class GenerateCurriculumRequest(SQLModel):
    goal: str
    level: str = "beginner"
    hours_per_day: float = 1.0
    session_id: str | None = None  # Optional session ID for personalization
    prefer_long_videos: bool = False


class VideoOut(SQLModel):
    youtube_id: str | None = None  # YouTube ID
    paper_id: str | None = None  # ArXiv paper ID
    repo_id: str | None = None  # GitHub repo ID
    doc_id: str | None = None  # Documentation ID
    source_type: str = "youtube"  # "youtube", "paper", "repo", "doc"
    title: str
    channel: str
    duration_seconds: int
    reading_time_minutes: int | None = None  # For papers, repos, docs
    score: float
    why_this_video: str
    transcript_available: bool
    duration_advice: str | None = None


class DayOut(SQLModel):
    day: int
    concept: str
    video: VideoOut
    alternative_video: VideoOut | None = None


class WeekOut(SQLModel):
    week: int
    theme: str
    days: list[DayOut]


class GenerateCurriculumResponse(SQLModel):
    curriculum_id: uuid.UUID
    topic: str
    duration_days: int
    level: str
    hours_per_day: float
    cached: bool = False
    estimated_total_hours: float | None = None
    estimated_video_hours: float | None = None
    estimated_reading_hours: float | None = None
    estimated_practice_hours: float | None = None
    estimated_review_hours: float | None = None
    estimated_buffer_hours: float | None = None
    description: str  # Dynamic description based on curriculum structure
    warning: str | None = None
    weeks: list[WeekOut]
    session_id: str | None = None  # Session ID for returning users (always returned)
    can_expand: bool = False
    social_proof: dict[str, Any] | None = None
    remaining_requests: int | None = None
    hourly_limit: int | None = None


class CurriculumJob(SQLModel, table=True):
    __tablename__ = "curriculum_jobs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    goal_raw: str
    level: str = Field(max_length=50)
    hours_per_day: float
    status: str = Field(max_length=20, index=True)
    cache_key: str | None = Field(default=None, max_length=100, unique=True, index=True)
    curriculum_id: uuid.UUID | None = Field(default=None, foreign_key="curriculums.id")
    result_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error_message: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class GenerateCurriculumJobResponse(SQLModel):
    job_id: uuid.UUID
    status: str
    curriculum_id: uuid.UUID | None = None
    topic: str | None = None
    progress_message: str | None = None
    duration_days: int | None = None
    estimated_total_hours: float | None = None
    estimated_video_hours: float | None = None
    estimated_reading_hours: float | None = None
    estimated_practice_hours: float | None = None
    estimated_review_hours: float | None = None
    estimated_buffer_hours: float | None = None
    warning: str | None = None
    weeks: list[WeekOut] | None = None
    poll_url: str
    error: str | None = None
    can_expand: bool = False
    remaining_requests: int | None = None
    hourly_limit: int | None = None


class CurriculumProgress(SQLModel, table=True):
    __tablename__ = "curriculum_progress"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    curriculum_id: uuid.UUID = Field(foreign_key="curriculums.id", index=True)
    day_number: int
    completed_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DayCompletionRequest(SQLModel):
    day_number: int


class DayCompletionResponse(SQLModel):
    curriculum_id: uuid.UUID
    day_number: int
    completed: bool
    completed_at: datetime | None = None
