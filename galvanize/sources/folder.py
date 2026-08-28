"""Folder-watch source (watchdog).

Cross-platform native events (ReadDirectoryChangesW / inotify / FSEvents).
Stabilization: a path fires only after ``stabilize_s`` seconds with no
further events for it, so partial downloads and multi-file writes coalesce
into one wake. Actions and glob patterns are filtered per trigger.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Set

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ..events import Event

logger = logging.getLogger("galvanize.folder")

_ACTION_MAP = {
    "created": "created",
    "modified": "modified",
    "moved": "moved",
    "deleted": "deleted",
}


class _DebouncedHandler(FileSystemEventHandler):
    """Buffer raw events per path; emit one Event once a path goes quiet."""

    def __init__(
        self,
        trigger_name: str,
        patterns: list,
        actions: Set[str],
        stabilize_s: float,
        emit: Callable[[Event], None],
    ) -> None:
        super().__init__()
        self.trigger_name = trigger_name
        self.patterns = list(patterns or [])
        self.actions = actions
        self.stabilize_s = max(0.05, stabilize_s)
        self.emit = emit
        self._pending: Dict[str, tuple] = {}  # path -> (action, last_ts)
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def _matches(self, path: str) -> bool:
        if not self.patterns:
            return True
        name = Path(path).name
        return any(fnmatch.fnmatch(name, p) for p in self.patterns)

    def on_any_event(self, event) -> None:
        if getattr(event, "is_directory", False):
            return
        action = _ACTION_MAP.get(event.event_type)
        if action is None or action not in self.actions:
            return
        # For moves, watch the destination (the file that now exists).
        path = str(getattr(event, "dest_path", None) or event.src_path)
        if not self._matches(path):
            return
        with self._lock:
            self._pending[path] = (action, time.time())
            if self._timer is None:
                self._timer = threading.Timer(self.stabilize_s, self._flush)
                self._timer.daemon = True
                self._timer.start()

    def _flush(self) -> None:
        fire: list = []
        with self._lock:
            now = time.time()
            ready = [
                (p, a) for p, (a, ts) in self._pending.items()
                if now - ts >= self.stabilize_s
            ]
            for p, _ in ready:
                del self._pending[p]
            fire = ready
            if self._pending:
                self._timer = threading.Timer(self.stabilize_s, self._flush)
                self._timer.daemon = True
                self._timer.start()
            else:
                self._timer = None
        for path, action in fire:
            logger.debug("folder event %s %s", action, path)
            self.emit(Event(
                trigger_name=self.trigger_name,
                source="folder",
                type=f"file.{action}",
                payload={"path": path, "action": action, "name": Path(path).name,
                         "file": Path(path).name},
            ))


class FolderWatcher:
    """Owns one watchdog Observer; sources for all folder triggers live on it."""

    def __init__(self) -> None:
        self._observer = Observer()
        self._handlers: Dict[str, _DebouncedHandler] = {}
        self._started = False

    def add(self, trigger, emit: Callable[[Event], None]) -> None:
        src = trigger.source
        path = Path(str(src.get("path", ""))).expanduser()
        if not path.is_dir():
            raise FileNotFoundError(f"watch path missing: {path}")
        actions = {str(a).lower() for a in (src.get("actions") or ["created", "modified"])}
        handler = _DebouncedHandler(
            trigger_name=trigger.name,
            patterns=src.get("patterns") or [],
            actions=actions,
            stabilize_s=float(src.get("stabilize_s", 2.0) or 2.0),
            emit=emit,
        )
        self._observer.schedule(
            handler, str(path), recursive=bool(src.get("recursive", True))
        )
        self._handlers[trigger.name] = handler
        if self._started:
            raise RuntimeError("start() after add only")

    def start(self) -> None:
        self._observer.daemon = True
        self._observer.start()
        self._started = True

    def stop(self) -> None:
        self._observer.stop()
