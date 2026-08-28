"""Cloud relay pull source (companion to relay/worker.js).

The daemon long-polls the user's Cloudflare Worker; each queued event is
dispatched through the emit trigger named by the ingest route. No inbound
ports, works behind any NAT, survives laptop sleep (worker holds the queue).

Auth: serve token reused — the relay's RELAY_TOKEN is stored in the OS
keyring under relay:token by `add webhook --relay` (the same value the
worker secret holds).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from ..events import Event

logger = logging.getLogger("galvanize.relay")

POLL_TIMEOUT_S = 45      # worker long-poll isn't implemented; short poll is fine
MAX_BACKOFF_S = 300


class RelayWatcher:
    """One worker URL; dispatches every queued event to its emit trigger."""

    def __init__(self, url: str, token: str,
                 emit_by_route: Callable[[str, Event], None]) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.emit_by_route = emit_by_route
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._since = "0"
        self.last_error: Optional[str] = None
        self.connected = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="gz-relay")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _poll(self) -> Optional[dict]:
        q = urllib.parse.urlencode({"since": self._since, "limit": 100})
        req = urllib.request.Request(f"{self.url}/events?{q}",
                                     headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=POLL_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _loop(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            try:
                body = self._poll()
                self.connected = True
                self.last_error = None
                backoff = 2.0
                if not body:
                    continue
                events = body.get("events") or []
                for ev in events:
                    route = str(ev.get("route") or "")
                    payload = ev.get("body")
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except Exception:
                            payload = {"raw": payload}
                    if not route:
                        continue
                    self.emit_by_route(
                        route,
                        Event(trigger_name=route, source="relay",
                              type=str((payload or {}).get("event_type", "relay.event")),
                              payload=payload or {}))
                if body.get("since"):
                    self._since = str(body["since"])
                if not events:
                    self._stop.wait(2.0)
            except Exception as e:
                self.connected = False
                self.last_error = f"{type(e).__name__}: {e}"
                logger.warning("relay: %s (retry in %.0fs)", self.last_error, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
