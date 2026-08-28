"""Integration: galvanize POST -> REAL Hermes WebhookAdapter.

Runs ONLY inside the Hermes venv (needs gateway imports). Skips elsewhere.
Boots the actual gateway/platforms/webhook.py adapter on an ephemeral port,
points galvanize's HERMES_HOME at a temp dir, and asserts the full lane
accepts our signed POST with a 202 (fresh-session dispatch) — proving the
HMAC scheme, subscriptions file, prompt rendering, and event filter against
the real code, not a mock.
"""

import asyncio
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

yaml = pytest.importorskip("yaml")

try:
    from gateway.config import PlatformConfig
    from gateway.platforms.webhook import WebhookAdapter
    GATEWAY = True
except Exception:
    GATEWAY = False

pytestmark = pytest.mark.skipif(
    not GATEWAY, reason="run inside the Hermes venv: venv/Scripts/python -m pytest"
)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def live_lane(tmp_path, monkeypatch):
    """A real WebhookAdapter serving a galvanize-managed route on 127.0.0.1."""
    from galvanize import hermes as H

    hhome = tmp_path / "hermes"
    (hhome / "plugins").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(hhome))

    port = _free_port()
    cfg = {"platforms": {"webhook": {"enabled": True, "extra": {"port": port, "host": "127.0.0.1"}}}}
    (hhome / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    # galvanize's own route registration, straight into the temp HERMES_HOME
    url = H.register_route("cad-drops", "integration-secret", deliver="log")
    assert url == f"http://127.0.0.1:{port}/webhooks/cad-drops"

    pc = PlatformConfig(enabled=True, extra={"port": port, "host": "127.0.0.1"})
    adapter = WebhookAdapter(pc)

    # Keep the adapter's event loop alive on a dedicated thread (same shape
    # as the real gateway process); asyncio.run() would close it under aiohttp.
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _serve():
        asyncio.set_event_loop(loop)

        async def _boot():
            ok = await adapter.connect()
            ready.set()
            if not ok:
                return
            while True:
                await asyncio.sleep(0.1)

        try:
            loop.run_until_complete(_boot())
        except RuntimeError:
            pass

    th = threading.Thread(target=_serve, daemon=True)
    th.start()
    assert ready.wait(10), "adapter failed to bind"
    try:
        yield adapter, port
    finally:
        async def _stop():
            await adapter.disconnect()
        try:
            asyncio.run_coroutine_threadsafe(_stop(), loop).result(timeout=10)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)


def test_galvanize_emit_accepted_by_real_lane(live_lane):
    from galvanize import manage
    from galvanize.config import Trigger, upsert_trigger

    adapter, port = live_lane
    t = Trigger(name="cad-drops", source={"type": "emit"},
                wake={"kind": "hermes", "deliver": "log"},
                prompt="A CAD file {name} arrived. Do the thing.")
    upsert_trigger(t)

    r = manage.emit("cad-drops", {"name": "bracket_v3.step"})
    assert r["ok"], r

    # The lane registered a one-shot delivery for our POST => session spawn
    # path was reached with the galvanize-rendered prompt.
    entries = list(adapter._delivery_info.items())
    assert entries, "adapter never registered a delivery -> lane did not accept"
    chat_id, deliv = entries[0]
    assert chat_id.startswith("webhook:cad-drops:")
    assert deliv["deliver"] == "log"


def test_bad_signature_rejected_by_real_lane(live_lane):
    adapter, port = live_lane
    body = json.dumps({"event_type": "galvanize", "trigger_name": "cad-drops",
                       "type": "x", "payload": {}, "prompt": "p"}).encode()
    import time
    ts = int(time.time())
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/webhooks/cad-drops", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Webhook-Timestamp": str(ts),
                 "X-Webhook-Signature-V2": "deadbeef" * 8},
    )
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 401


def test_unknown_route_404(live_lane):
    adapter, port = live_lane
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/webhooks/nope", data=b"{}", method="POST",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 404
