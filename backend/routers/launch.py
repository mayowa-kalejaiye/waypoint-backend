"""Endpoints for launch waitlist capture and analytics events."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from backend.db.database import get_session
from backend.models.launch import (
    LaunchAnalyticsEvent,
    LaunchEventRequest,
    LaunchResponse,
    LaunchWaitlistEntry,
    WaitlistSignupRequest,
)

router = APIRouter(prefix="/api/v1/launch", tags=["launch"])

analytics_log_path = Path(__file__).resolve().parents[1] / "analytics.log"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@router.post("/waitlist", response_model=LaunchResponse)
def signup_waitlist(request: WaitlistSignupRequest) -> LaunchResponse:
    email = request.email.strip().lower()
    name = request.name.strip()
    source = request.source.strip() or "launch_waitlist"
    session_id = request.session_id.strip()
    page_path = request.page_path.strip()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    with get_session() as session:
        existing = session.exec(
            select(LaunchWaitlistEntry).where(LaunchWaitlistEntry.email == email)
        ).first()
        if existing is None:
            entry = LaunchWaitlistEntry(
                email=email,
                name=name,
                source=source,
                session_id=session_id,
                properties={"page_path": page_path, **(request.properties or {})},
            )
            session.add(entry)
        else:
            existing.name = name or existing.name
            existing.source = source or existing.source
            existing.session_id = session_id or existing.session_id
            existing.properties = {
                **(existing.properties or {}),
                "page_path": page_path,
                **(request.properties or {}),
            }
        session.commit()
    return LaunchResponse(success=True, message="You're on the waitlist.")


@router.post("/events", response_model=LaunchResponse)
def record_launch_event(request: LaunchEventRequest) -> LaunchResponse:
    event_name = request.event_name.strip()
    session_id = request.session_id.strip()
    page_path = request.page_path.strip()
    if not event_name:
        raise HTTPException(status_code=400, detail="event_name is required.")
    with get_session() as session:
        event = LaunchAnalyticsEvent(
            event_name=event_name,
            session_id=session_id,
            page_path=page_path,
            properties=request.properties or {},
        )
        session.add(event)
        session.commit()
    return LaunchResponse(success=True, message="Event recorded.")


@router.get("/analytics")
def get_analytics_log() -> dict[str, Any]:
    """Return the analytics log contents as parsed JSON lines."""
    if not analytics_log_path.exists():
        raise HTTPException(status_code=404, detail="Analytics log not found.")
    events: list[dict[str, Any]] = []
    with analytics_log_path.open("r", encoding="utf-8") as file_handle:
        for line in file_handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"raw": line})
    return {"events": events, "count": len(events)}
