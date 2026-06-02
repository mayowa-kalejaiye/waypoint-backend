"""Moat-deepening feature models for data accumulation and personalization."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from enum import Enum

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class ConceptType(str, Enum):
    """Types of concepts for content routing."""
    FOUNDATIONAL = "foundational"  # Theory, basics — videos best
    REFERENCE = "reference"  # APIs, patterns, syntax — docs best
    PRACTICAL = "practical"  # Build, apply — GitHub/projects best
    THEORETICAL = "theoretical"  # Research, deep learning — papers best
    MIXED = "mixed"  # Covers multiple areas


# UPGRADE 1: Persistent Concept Graph Database
class ConceptNode(SQLModel, table=True):
    """Persistent validated concept graph nodes that improve over time."""

    __tablename__ = "concept_nodes"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    topic: str = Field(max_length=200, index=True)
    concept: str = Field(max_length=200, index=True)
    level: str = Field(max_length=50, index=True)
    prerequisites: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    concept_type: str = Field(max_length=50, default=ConceptType.MIXED.value)  # foundational, reference, practical, theoretical
    estimated_hours: float = Field(default=1.0)  # How long to learn this concept
    times_validated: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Composite unique constraint: (topic, concept, level)


class ConceptGraphFeedback(SQLModel, table=True):
    """User feedback on concept graph correctness and ordering."""

    __tablename__ = "concept_graph_feedback"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    topic: str = Field(max_length=200, index=True)
    concept: str = Field(max_length=200, index=True)
    feedback_type: str = Field(max_length=50)  # prerequisite_missing, wrong_order, correct
    user_session_id: str = Field(max_length=100)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# UPGRADE 2: Video Performance Tracking
class VideoInteraction(SQLModel, table=True):
    """Track user engagement with videos."""

    __tablename__ = "video_interactions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    curriculum_id: uuid.UUID = Field(foreign_key="curriculums.id", index=True)
    youtube_id: str = Field(max_length=20, index=True)
    day_number: int
    interaction_type: str = Field(max_length=50)  # opened, completed, skipped, alternative_selected
    session_id: str = Field(max_length=100, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class VideoPerformanceScore(SQLModel, table=True):
    """Aggregate performance metrics for videos based on user interactions."""

    __tablename__ = "video_performance_scores"

    youtube_id: str = Field(max_length=20, primary_key=True)
    completion_rate: float = Field(default=0.0)
    skip_rate: float = Field(default=0.0)
    alternative_selection_rate: float = Field(default=0.0)
    total_interactions: int = Field(default=0)
    performance_score: float = Field(default=0.5)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# UPGRADE 3: Transcript Intelligence Index
class TranscriptIntelligence(SQLModel, table=True):
    """Cached transcript analysis to avoid re-analyzing the same videos."""

    __tablename__ = "transcript_intelligence"

    youtube_id: str = Field(max_length=20, primary_key=True)
    reading_level: float = Field(default=0.0)
    mentions_prerequisites: bool = Field(default=False)
    has_code_examples: bool = Field(default=False)
    content_density: float = Field(default=0.0)
    key_concepts: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    beginner_score: float = Field(default=0.5)
    depth_score: float = Field(default=0.5)
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)
    analysis_version: str = Field(max_length=10, default="v1")


# UPGRADE 4: Session-Based Personalization
class LearningSession(SQLModel, table=True):
    """Track learning sessions to personalize future curricula for returning users."""

    __tablename__ = "learning_sessions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: str = Field(max_length=100, unique=True, index=True)
    topic_history: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    level_history: str = Field(max_length=50, default="beginner")  # Most recent level
    avg_completion_rate: float = Field(default=0.0)
    preferred_video_duration_seconds: int = Field(default=900)  # 15 minutes
    total_curricula_generated: int = Field(default=0)
    viewed_content_ids: list[str] = Field(sa_column=Column(JSON), default_factory=list)  # All youtube_id/paper_id/repo_id seen
    completed_concepts: list[str] = Field(sa_column=Column(JSON), default_factory=list)  # Concepts marked as complete
    source_preferences: dict[str, float] = Field(sa_column=Column(JSON), default_factory=dict)  # User's source preferences (youtube: 0.9, docs: 0.7, etc.)
    learning_pace: float = Field(default=1.0)  # 0.5 = slow learner, 1.0 = standard, 1.5 = fast learner
    preferred_sources: list[str] = Field(sa_column=Column(JSON), default_factory=list)  # ["youtube", "docs"] etc based on user interactions
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_active_at: datetime = Field(default_factory=datetime.utcnow)


class ConceptMastery(SQLModel, table=True):
    """Track which concepts have been mastered by a session to prevent duplicate surfacing."""

    __tablename__ = "concept_mastery"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: str = Field(max_length=100, index=True)
    topic: str = Field(max_length=200, index=True)
    concept: str = Field(max_length=200, index=True)
    level_learned: str = Field(max_length=50, default="beginner")  # Level at which learned
    learned_at: datetime = Field(default_factory=datetime.utcnow)
    confidence: float = Field(default=0.5)  # 0.0-1.0: how well the user knows this concept


class ContentInteraction(SQLModel, table=True):
    """Track user interactions with individual videos, papers, docs, or repos."""

    __tablename__ = "content_interactions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: str = Field(max_length=100, index=True)
    content_id: str = Field(max_length=100, index=True)  # youtube_id, paper_id, repo_id, or doc_url
    source: str = Field(max_length=50)  # youtube, arxiv, github, docs
    curriculum_id: uuid.UUID = Field(foreign_key="curriculums.id", index=True)
    concept: str = Field(max_length=200)  # Which concept this was for
    interaction_type: str = Field(max_length=50, default="viewed")  # viewed, started, completed, skipped, marked_difficult
    duration_seconds: int = Field(default=0)  # How long user spent on it
    satisfaction_score: int = Field(default=0)  # User feedback: -1 (bad), 0 (neutral), 1 (good)
    interacted_at: datetime = Field(default_factory=datetime.utcnow)


# Request/Response models for new endpoints
class ConceptFeedbackRequest(SQLModel):
    topic: str
    concept: str
    feedback_type: str  # prerequisite_missing, wrong_order, correct
    session_id: str


class VideoInteractionRequest(SQLModel):
    curriculum_id: uuid.UUID
    youtube_id: str
    day_number: int
    interaction_type: str  # opened, completed, skipped, alternative_selected
    session_id: str


class InteractionResponse(SQLModel):
    success: bool
    message: str | None = None


class GenerateCurriculumRequestWithSession(SQLModel):
    """Updated curriculum generation request that optionally accepts session_id."""

    goal: str
    level: str = "beginner"
    hours_per_day: float = 1.0
    session_id: str | None = None


class GenerateCurriculumResponseWithSession(SQLModel):
    """Updated response that includes session_id for returning users."""

    curriculum_id: uuid.UUID
    topic: str
    duration_days: int
    warning: str | None = None
    weeks: list[Any]  # list[WeekOut] - using Any to avoid circular import
    session_id: str  # Always return session_id for future requests


class ContentInteractionRequest(SQLModel):
    """Request to record user interaction with specific content."""
    curriculum_id: uuid.UUID
    content_id: str  # youtube_id, paper_id, repo_id, or doc_url
    source: str  # youtube, arxiv, github, docs
    concept: str
    interaction_type: str = "viewed"  # viewed, started, completed, skipped, marked_difficult
    duration_seconds: int = 0
    satisfaction_score: int = 0  # -1 (bad), 0 (neutral), 1 (good)


class ContentInteractionResponse(SQLModel):
    """Response after recording interaction."""
    success: bool
    message: str | None = None


class CurriculumFeedback(SQLModel, table=True):
    """User feedback on curriculum quality for adaptive regeneration."""
    
    __tablename__ = "curriculum_feedback"
    
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    curriculum_id: uuid.UUID = Field(foreign_key="curriculums.id", index=True)
    session_id: str = Field(max_length=100, index=True)
    difficulty_rating: int = Field(default=0)  # 1-5: too easy to too hard
    pacing_rating: int = Field(default=0)  # 1-5: too fast to too slow
    content_quality_rating: int = Field(default=0)  # 1-5: poor to excellent
    relevance_rating: int = Field(default=0)  # 1-5: irrelevant to perfect match
    preferred_source_feedback: dict[str, float] = Field(sa_column=Column(JSON), default_factory=dict)  # {source: preference_delta}
    concepts_to_deepen: list[str] = Field(sa_column=Column(JSON), default_factory=list)  # Concepts needing more depth
    concepts_to_skip: list[str] = Field(sa_column=Column(JSON), default_factory=list)  # Concepts to skip next time
    additional_notes: str = Field(max_length=500, default="")
    submitted_at: datetime = Field(default_factory=datetime.utcnow)


class ProjectSuggestion(SQLModel, table=True):
    """Hands-on project suggestions linked to concepts for practical learning."""
    
    __tablename__ = "project_suggestions"
    
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    concept: str = Field(max_length=200, index=True)
    difficulty_level: str = Field(max_length=50, default="beginner")  # beginner, intermediate, advanced
    project_name: str = Field(max_length=200)
    project_description: str = Field(max_length=500)
    github_repo_url: str = Field(max_length=500)  # Link to starter template
    estimated_hours: float = Field(default=2.0)
    skills_practiced: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    prerequisites: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    popularity: int = Field(default=0)  # How many times suggested
