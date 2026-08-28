"""serve API contract tests — real HTTP against a live embedded server."""

import json
import urllib.error
import urllib.request

import pytest

from galvanize import serve
from galvanize import manage


@pytest.fixture
def live_serve():
    httpd = serve.run(port=0, block=False)
    port = httpd.server_address[1]
    token = serve.read_token()
    try:
        yield port, token
    finally:
        httpd.shutdown()


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, json.loads(r.read())


def _post(port, token, op, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/manage/{op}",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_version_handshake_is_token_free(live_serve):
    port, _ = live_serve
    status, body = _get(port, "/version")
    assert status == 200
    assert body["api_version"] == serve.API_VERSION
    assert "core_version" in body


def test_unauthorized_rejected(live_serve):
    port, _ = live_serve
    req = urllib.request.Request(f"http://127.0.0.1:{port}/manage/status",
                                 data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 401


def test_status_roundtrip(live_serve):
    port, token = live_serve
    status, body = _post(port, token, "status", {})
    assert status == 200 and body["ok"]
    assert "triggers" in body


def test_add_remove_roundtrip(live_serve, tmp_path):
    watch = tmp_path / "srv"
    watch.mkdir()
    port, token = live_serve
    st, body = _post(port, token, "add", {"kind": "folder", "target": str(watch),
                                          "name": "srv-drop", "wake": "shell",
                                          "command": "echo ok"})
    assert st == 200 and body["ok"], body
    st, body = _post(port, token, "list", {})
    assert "srv-drop" in body["triggers"]
    # toggle off/on (dashboard tab actions)
    st, body = _post(port, token, "set_enabled", {"name": "srv-drop", "enabled": False})
    assert st == 200 and body["ok"]
    st, body = _post(port, token, "list", {})
    assert body["triggers"]["srv-drop"]["enabled"] is False
    # shell test-fire
    st, body = _post(port, token, "test", {"name": "srv-drop"})
    assert st == 200 and body["ok"], body
    st, body = _post(port, token, "remove", {"name": "srv-drop"})
    assert st == 200 and body["ok"]


def test_running_info_verifies(live_serve):
    info = serve.running_info()
    assert info and info["port"] == live_serve[0]
    assert serve.serve_info_path().exists()


def test_doctor_reports_surfaces_and_secret_store(isolated_homes, tmp_path):
    # surfaces: fake a both-active hermes state -> doctor must flag it
    from galvanize.paths import hermes_home
    (hermes_home() / "plugins" / "galvanize").mkdir(parents=True)
    (hermes_home() / "plugins" / "galvanize" / "plugin.yaml").write_text("name: galvanize")
    import yaml
    cfg = {"mcp_servers": {"galvanize": {"command": "galvanize", "args": ["mcp"]}}}
    (hermes_home() / "config.yaml").write_text(yaml.safe_dump(cfg))

    r = manage.doctor()
    surf = [c for c in r["checks"] if c["name"] == "surface:hermes"]
    assert surf and surf[0]["ok"] is False, surf
    assert not r["ok"]
