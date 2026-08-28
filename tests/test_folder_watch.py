"""Folder watcher behavior: debounce coalescing, patterns, actions."""

import time
from pathlib import Path

import pytest

from galvanize.config import Trigger
from galvanize.daemon import Daemon


class Collector:
    def __init__(self):
        self.events = []

    def __call__(self, evt):
        self.events.append(evt)

    def wait(self, n=1, timeout=6.0):
        deadline = time.time() + timeout
        while time.time() < deadline and len(self.events) < n:
            time.sleep(0.05)
        return len(self.events) >= n


def make_daemon(tmp_path, collector, **src):
    watch = tmp_path / "watch"
    watch.mkdir(exist_ok=True)
    source = {"type": "folder", "path": str(watch), "stabilize_s": 0.4}
    source.update(src)
    t = Trigger(name="drops", source=source, wake={"kind": "shell", "command": "echo"})
    d = Daemon()
    d._make_emit = lambda name: collector  # bypass dispatch, observe events
    d._rebuild_watchers({"drops": t})
    return d, watch


def test_created_file_fires_once_with_quick_writes(tmp_path):
    c = Collector()
    d, watch = make_daemon(tmp_path, c)
    try:
        p = watch / "part.brp"
        p.write_bytes(b"1")          # partial download beat
        time.sleep(0.1)
        p.write_bytes(b"12345678")   # final content
        assert c.wait(1, 8)
        time.sleep(0.8)              # let any extra flush land
        created = [e for e in c.events if e.type == "file.created"]
        modified = [e for e in c.events if e.type == "file.modified"]
        assert created or modified   # watchdog's created/modified mix varies
        assert len(c.events) == 1, f"expected coalesced single event, got {c.events}"
        assert c.events[0].payload["name"] == "part.brp"
    finally:
        d._watcher.stop()


def test_pattern_filter(tmp_path):
    c = Collector()
    d, watch = make_daemon(tmp_path, c, patterns=["*.step"])
    try:
        (watch / "ignore.txt").write_text("x")
        time.sleep(1.2)
        (watch / "model.step").write_text("y")
        assert c.wait(1, 8)
        assert all(e.payload["name"] == "model.step" for e in c.events)
    finally:
        d._watcher.stop()
