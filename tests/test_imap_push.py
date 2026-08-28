"""GreenMail-backed IMAP push tests. Skips if Docker isn't reachable.

Covers the reliability contract's testable halves:
  - push path: injected mail -> IDLE event within seconds
  - first-run no-replay: anchor at current max, pre-existing mail silent
  - filters: subject filter drops non-matching mail
  - reconnect catch-up: watcher stopped, mail injected, watcher restarted
    -> mail delivered once, then not again
  - duplicate-delivery dedupe via the bus dedupe_key (synthetic double)
"""

import socket
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

import pytest

from galvanize.config import Trigger, upsert_trigger, load_triggers
from galvanize.daemon import Daemon
from galvanize.bus import TriggerBus
from galvanize.events import Event

IMAP_PORT = 3143
SMTP_PORT = 3025
USER = "trigger@example.com"
PASSWORD = "***"


def _docker_alive() -> bool:
    import subprocess
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _greenmail_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", IMAP_PORT), timeout=1):
            return True
    except OSError:
        return False


def _ensure_greenmail():
    if not _docker_alive():
        pytest.skip("docker daemon not reachable")
    if _greenmail_up():
        return
    import subprocess
    comp = Path(__file__).parent / "greenmail" / "docker-compose.yml"
    subprocess.run(["docker", "compose", "-f", str(comp), "up", "-d"],
                   check=True, capture_output=True, timeout=300)
    deadline = time.time() + 90
    while time.time() < deadline:
        if _greenmail_up():
            time.sleep(2)
            return
        time.sleep(2)
    pytest.skip("greenmail did not come up")


@pytest.fixture(scope="module")
def mailbox():
    _ensure_greenmail()


def _send_mail(subject, body="hello", frm="user@example.com", to=USER):
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP("127.0.0.1", SMTP_PORT, timeout=15) as s:
        s.send_message(msg)


def _imap_trigger(name="mail-watch", *, dedupe_key="", **src_over):
    src = {"type": "imap", "host": "127.0.0.1", "port": IMAP_PORT, "ssl": False,
           "user": USER, "password": PASSWORD, "folder": "INBOX"}
    src.update(src_over)
    t = Trigger(name=name, source=src, dedupe_key=dedupe_key,
                wake={"kind": "shell", "command": "echo ok"})
    upsert_trigger(t)
    return t


def _run_daemon_collector(monkeypatch):
    """Daemon instance with emit hooked to a collector (no real dispatch)."""
    events = []
    d = Daemon()
    d._make_emit = lambda name: (lambda evt: events.append(evt))
    return d, events


def test_push_mail_fires_within_seconds(mailbox, monkeypatch):
    t = _imap_trigger("push-test")
    d, events = _run_daemon_collector(monkeypatch)
    d._rebuild_watchers({"push-test": t})
    try:
        time.sleep(4)                      # connect + anchor phase
        _send_mail("push me now")
        deadline = time.time() + 30
        while time.time() < deadline and not events:
            time.sleep(0.5)
        assert events, "no IDLE event within 30s"
        assert events[0].type == "mail.received"
        assert "push me now" in events[0].payload["subject"]
        assert events[0].payload["from"] == "user@example.com"
    finally:
        for w, _ in d._imap_watchers.values():
            w.stop()


def test_first_run_does_not_replay(mailbox, monkeypatch):
    _send_mail("old mail before anchor")
    time.sleep(2)
    t = _imap_trigger("replay-test")
    d, events = _run_daemon_collector(monkeypatch)
    d._rebuild_watchers({"replay-test": t})
    try:
        time.sleep(6)                      # anchor phase + margin
        assert not events, f"pre-existing mail replayed: {events}"
    finally:
        for w, _ in d._imap_watchers.values():
            w.stop()


def test_subject_filter(mailbox, monkeypatch):
    t = _imap_trigger("filter-test", filter_subject="KEEP")
    d, events = _run_daemon_collector(monkeypatch)
    d._rebuild_watchers({"filter-test": t})
    try:
        time.sleep(4)
        _send_mail("trash this one")
        _send_mail("please KEEP this one")
        deadline = time.time() + 30
        while time.time() < deadline and len(events) < 1:
            time.sleep(0.5)
        time.sleep(2)
        assert all("KEEP" in e.payload["subject"] for e in events), events
        assert len(events) == 1
    finally:
        for w, _ in d._imap_watchers.values():
            w.stop()


def test_catchup_after_gap(mailbox, monkeypatch):
    t = _imap_trigger("gap-test")
    d, events = _run_daemon_collector(monkeypatch)
    d._rebuild_watchers({"gap-test": t})
    time.sleep(4)
    for w, _ in d._imap_watchers.values():
        w.stop()                            # simulate downtime
    time.sleep(1)
    _send_mail("arrived while down")
    time.sleep(2)
    d2, events2 = _run_daemon_collector(monkeypatch)
    d2._rebuild_watchers({"gap-test": load_triggers()["gap-test"]})
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not events2:
            time.sleep(0.5)
        assert events2, "mail from the gap was not caught up"
        assert len(events2) == 1
    finally:
        for w, _ in d2._imap_watchers.values():
            w.stop()


def test_bus_dedupe_collapses_duplicate_mail_event():
    t = _imap_trigger("dedupe-test", dedupe_key="mail:{uid}")
    bus = TriggerBus()
    e1 = Event("dedupe-test", "imap", "mail.received", {"uid": 42, "subject": "x"})
    e2 = Event("dedupe-test", "imap", "mail.received", {"uid": 42, "subject": "x"})
    ok1, d1 = bus.handle(t, e1)
    ok2, d2 = bus.handle(t, e2)
    assert "skipped" not in d1
    assert "dedupe" in d2
