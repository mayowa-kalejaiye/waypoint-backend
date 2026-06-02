"""Endpoints for concept graph feedback and validation."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select
from sqlmodel import Session

from backend.db.database import get_session
from backend.models.moat_features import (
    ConceptFeedbackRequest,
    ConceptGraphFeedback,
    ConceptNode,
    InteractionResponse,
)

router = APIRouter(prefix="/api/v1", tags=["feedback"])


@router.post("/feedback/concept", response_model=InteractionResponse)
async def submit_concept_feedback(request: ConceptFeedbackRequest) -> InteractionResponse:
    """Submit feedback on concept graph correctness and ordering.
    
    feedback_type can be:
    - 'correct': This concept order/prerequisite is correct
    - 'prerequisite_missing': A prerequisite was missing for this concept
    - 'wrong_order': This concept should come earlier/later
    """
    with get_session() as session:
        # Create feedback record
        feedback = ConceptGraphFeedback(
            topic=request.topic,
            concept=request.concept,
            feedback_type=request.feedback_type,
            user_session_id=request.session_id,
        )
        session.add(feedback)

        # Increment validation count for the concept node
        stmt = select(ConceptNode).where(
            (ConceptNode.topic == request.topic)
            & (ConceptNode.concept == request.concept)
        )
        node = session.exec(stmt).first()
        if node:
            node.times_validated += 1
            session.add(node)

        session.commit()

    return InteractionResponse(success=True, message=f"Feedback recorded for {request.concept}")


@router.get("/concepts/{topic}/{level}", response_model=dict)
async def get_concept_graph(topic: str, level: str) -> dict:
    """Get the cached concept graph for a topic/level if it has 5+ validated nodes.
    
    Otherwise returns empty dict indicating LLM generation should proceed.
    """
    with get_session() as session:
        stmt = select(ConceptNode).where(
            (ConceptNode.topic == topic) & (ConceptNode.level == level)
        )
        nodes = session.exec(stmt).all()

        # Only return cached graph if we have at least 5 validated nodes
        if len(nodes) < 5:
            return {}

        # Build graph structure from nodes
        graph = {}
        for node in nodes:
            graph[node.concept] = {
                "prerequisites": node.prerequisites,
                "times_validated": node.times_validated,
            }

        return graph


@router.post("/concepts/persist", response_model=InteractionResponse)
async def persist_concept_graph(topic: str, level: str, graph: dict) -> InteractionResponse:
    """Persist a generated concept graph to the database.
    
    Called after LLM generates a new graph to cache it for future use.
    Graph format: {concept: {prerequisites: [str], ...}, ...}
    """
    with get_session() as session:
        saved_count = 0

        for concept, data in graph.items():
            prerequisites = data.get("prerequisites", [])

            if not concept:
                continue

            # Check if node exists
            stmt = select(ConceptNode).where(
                (ConceptNode.topic == topic)
                & (ConceptNode.concept == concept)
                & (ConceptNode.level == level)
            )
            node = session.exec(stmt).first()

            if node:
                node.prerequisites = prerequisites
                node.times_validated += 1
                session.add(node)
            else:
                node = ConceptNode(
                    topic=topic,
                    concept=concept,
                    level=level,
                    prerequisites=prerequisites,
                    times_validated=1,
                )
                session.add(node)

            saved_count += 1

        session.commit()

    return InteractionResponse(
        success=True,
        message=f"Persisted {saved_count} concept nodes for {topic}",
    )
