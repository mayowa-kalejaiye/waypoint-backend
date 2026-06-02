from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def track_event(event_name: str, properties: dict[str, Any] | None = None) -> None:
    """Append a simple JSONL analytics event for lightweight launch tracking."""
    payload = {
        "event": event_name,
        "time": datetime.utcnow().isoformat(),
        "props": properties or {},
    }

    try:
        analytics_path = Path("analytics.log")
        analytics_path.parent.mkdir(parents=True, exist_ok=True)
        with analytics_path.open("a", encoding="utf-8") as file_handle:
            file_handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to write analytics event %s: %s", event_name, exc)
