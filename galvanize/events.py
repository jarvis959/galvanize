"""Normalized event object shared by all sources and dispatchers."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Event:
    trigger_name: str
    source: str            # "emit" | "folder" | "webhook"
    type: str              # "file.created", "manual.test", service event name...
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    dedupe_key: Optional[str] = None

    def to_body(self) -> Dict[str, Any]:
        """JSON body shape used for POSTs (also stored/echoed by emit)."""
        return {
            "event_type": "galvanize",
            "trigger_name": self.trigger_name,
            "source": self.source,
            "type": self.type,
            "payload": self.payload,
            "ts": self.ts,
            "event_id": self.event_id,
        }

    @classmethod
    def from_body(cls, body: Dict[str, Any]) -> "Event":
        return cls(
            trigger_name=str(body.get("trigger_name", "unknown")),
            source=str(body.get("source", "unknown")),
            type=str(body.get("type", "event")),
            payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
            ts=float(body.get("ts") or time.time()),
            event_id=str(body.get("event_id") or uuid.uuid4().hex),
        )
