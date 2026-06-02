from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from backend.db.database import get_session
from backend.models.canvas_snapshot import CanvasSnapshot

router = APIRouter()


class CanvasPayload(BaseModel):
    nodes: list
    links: list[dict] = Field(default_factory=list)
    meta: dict | None = None
    session_id: str | None = None
    title: str | None = None


@router.post("/api/v1/canvas")
def save_canvas(payload: CanvasPayload):
    share_token = uuid.uuid4().hex
    snapshot = CanvasSnapshot(
        share_token=share_token,
        session_id=payload.session_id,
        title=payload.title,
        payload=payload.model_dump(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    try:
        with get_session() as session:
            session.add(snapshot)
            session.commit()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist canvas: {exc}") from exc

    return {"id": share_token, "url": f"/canvas/{share_token}"}


@router.get("/api/v1/canvas/{canvas_id}")
def get_canvas(canvas_id: str):
    with get_session() as session:
        stmt = select(CanvasSnapshot).where(CanvasSnapshot.share_token == canvas_id)
        snapshot = session.exec(stmt).first()
        if not snapshot:
            raise HTTPException(status_code=404, detail="Canvas not found")

        snapshot.access_count += 1
        snapshot.updated_at = datetime.utcnow()
        session.add(snapshot)
        session.commit()
        return snapshot.payload
