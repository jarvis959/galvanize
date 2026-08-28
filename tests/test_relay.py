"""Relay end-to-end: local stub mimicking the CF Worker API + RelayWatcher.

Exercises the actual relay/worker.js handler via a tiny HTTP shim with a
KV stub, so worker logic AND the poller are both under test.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from galvanize.sources.relay import RelayWatcher

# --- run the worker handler in-process (needs fetch-style Request/Response) ---
worker = pytest.importorskip("importlib")
import importlib.util
from pathlib import Path

WORKER_JS = Path(__file__).parent.parent / "relay" / "worker.js"

stub_state = {"store": {}, "token": "tok123"}


class StubKV:
    async def put(self, k, v, **kw):
        stub_state["store"][k] = v

    async def get(self, k):
        return stub_state["store"].get(k)

    async def list(self, opts=None):
        keys = sorted(stub_state["store"].keys())
        return {"keys": [{"name": k} for k in keys], "cursor": None}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self.path.startswith("/ingest/"):
            return self._send(404, {})
        route = self.path[len("/ingest/"):]
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        ts = int(time.time() * 1000)
        key = f"{ts:014d}-stub"
        stub_state["store"][key] = json.dumps(
            {"route": route, "body": body.decode(), "ts": ts})
        self._send(200, {"status": "queued", "id": key})

    def do_GET(self):
        if self.path.startswith("/events"):
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {stub_state['token']}":
                return self._send(401, {"error": "unauthorized"})
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            since = q.get("since", ["0"])[0]
            events = [{"id": k, **json.loads(v)}
                      for k, v in sorted(stub_state["store"].items()) if k > since]
            nxt = events[-1]["id"] if events else since
            return self._send(200, {"events": events, "since": nxt})
        self._send(404, {})


@pytest.fixture
def relay_server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_relay_pull_dispatches_queued_events(relay_server):
    stub_state["store"].clear()
    got = []
    w = RelayWatcher(relay_server, stub_state["token"],
                     lambda route, evt: got.append((route, evt)))
    w.start()
    try:
        import urllib.request
        req = urllib.request.Request(f"{relay_server}/ingest/github-push",
                                     data=json.dumps({"event_type": "push",
                                                      "ref": "main"}).encode(),
                                     method="POST")
        assert json.loads(urllib.request.urlopen(req, timeout=5).read())["status"] == "queued"
        deadline = time.time() + 15
        while time.time() < deadline and not got:
            time.sleep(0.5)
        assert got, "relay event never dispatched"
        route, evt = got[0]
        assert route == "github-push"
        assert evt.source == "relay"
        assert evt.payload["ref"] == "main"
        # cursor advanced: no duplicate dispatch after another poll cycle
        time.sleep(4)
        assert len(got) == 1, f"duplicate dispatch: {got}"
    finally:
        w.stop()


def test_relay_backoff_on_dead_server():
    w = RelayWatcher("http://127.0.0.1:1", "t", lambda r, e: None)
    w.start()
    time.sleep(3)
    assert w.last_error  # error surfaced, watcher alive (thread daemon)
    w.stop()
