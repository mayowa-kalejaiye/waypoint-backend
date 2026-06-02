"""Endpoints for video interaction tracking and performance scoring."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks
from sqlalchemy import select
from sqlmodel import Session

from backend.db.database import get_session
from backend.models.moat_features import (
    InteractionResponse,
    VideoInteraction,
    VideoInteractionRequest,
    VideoPerformanceScore,
)

router = APIRouter(prefix="/api/v1", tags=["interactions"])


def _recalculate_video_performance(youtube_id: str) -> None:
    """Background task: Recalculate video performance scores based on interactions."""
    with get_session() as session:
        # Query all interactions for this video
        stmt = select(VideoInteraction).where(VideoInteraction.youtube_id == youtube_id)
        interactions = session.exec(stmt).all()

        if not interactions:
            return

        total = len(interactions)
        completed = sum(1 for i in interactions if i.interaction_type == "completed")
        skipped = sum(1 for i in interactions if i.interaction_type == "skipped")
        alternative = sum(1 for i in interactions if i.interaction_type == "alternative_selected")

        completion_rate = completed / total if total > 0 else 0.0
        skip_rate = skipped / total if total > 0 else 0.0
        alternative_selection_rate = alternative / total if total > 0 else 0.0

        # Calculate performance score
        # Base: 0.5, adjusted by engagement
        performance_score = 0.5 + (completion_rate * 0.3) - (skip_rate * 0.3)
        performance_score = max(0.0, min(1.0, performance_score))

        # Update or create performance score record
        stmt = select(VideoPerformanceScore).where(
            VideoPerformanceScore.youtube_id == youtube_id
        )
        perf = session.exec(stmt).first()

        if perf:
            perf.completion_rate = completion_rate
            perf.skip_rate = skip_rate
            perf.alternative_selection_rate = alternative_selection_rate
            perf.total_interactions = total
            perf.performance_score = performance_score
        else:
            perf = VideoPerformanceScore(
                youtube_id=youtube_id,
                completion_rate=completion_rate,
                skip_rate=skip_rate,
                alternative_selection_rate=alternative_selection_rate,
                total_interactions=total,
                performance_score=performance_score,
            )
            session.add(perf)

        session.commit()


@router.post("/interactions", response_model=InteractionResponse)
async def track_video_interaction(
    request: VideoInteractionRequest,
    background_tasks: BackgroundTasks,
) -> InteractionResponse:
    """Track user interaction with a video (opened, completed, skipped, etc).
    
    Returns immediately while performance score recalculation happens in the background.
    """
    with get_session() as session:
        # Create interaction record
        interaction = VideoInteraction(
            curriculum_id=request.curriculum_id,
            youtube_id=request.youtube_id,
            day_number=request.day_number,
            interaction_type=request.interaction_type,
            session_id=request.session_id,
        )
        session.add(interaction)
        session.commit()

    # Queue background task to recalculate performance scores
    background_tasks.add_task(_recalculate_video_performance, request.youtube_id)

    return InteractionResponse(success=True, message=f"Interaction tracked for {request.youtube_id}")


@router.get("/interactions/{youtube_id}/performance", response_model=VideoPerformanceScore | None)
async def get_video_performance(youtube_id: str) -> VideoPerformanceScore | None:
    """Get performance metrics for a specific video."""
    with get_session() as session:
        stmt = select(VideoPerformanceScore).where(VideoPerformanceScore.youtube_id == youtube_id)
        return session.exec(stmt).first()


@router.post("/content_interactions")
async def track_content_interaction(request: "ContentInteractionRequest"):
    """Record a generic content interaction (views, exports, etc.)"""
    from backend.models.moat_features import ContentInteraction
    from backend.models.moat_features import ContentInteractionRequest as CIR

    # Validate/request mapping
    if not request:
        return {"success": False, "message": "Missing payload"}

    with get_session() as session:
        ci = ContentInteraction(
            session_id=request.session_id if hasattr(request, 'session_id') else "anon",
            content_id=request.content_id,
            source=request.source,
            curriculum_id=request.curriculum_id,
            concept=request.concept,
            interaction_type=request.interaction_type,
            duration_seconds=getattr(request, "duration_seconds", 0),
            satisfaction_score=getattr(request, "satisfaction_score", 0),
        )
        session.add(ci)
        session.commit()

    return {"success": True, "message": "Recorded"}
