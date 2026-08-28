import time
from pathlib import Path

from galvanize import template
from galvanize.events import Event


def test_template_dot_notation():
    assert template.render("{a.b}!", {"a": {"b": "x"}}) == "x!"


def test_template_missing_key_left_literal():
    assert template.render("{a.zzz}", {"a": {}}) == "{a.zzz}"


def test_template_raw_dump():
    out = template.render("{__raw__}", {"a": 1})
    assert '"a": 1' in out


def test_template_empty_dumps_payload():
    out = template.render("", {"k": "v"})
    assert '"k": "v"' in out


def test_event_roundtrip():
    e = Event("t", "folder", "file.created", {"path": "/x"})
    e2 = Event.from_body(e.to_body())
    assert e2.trigger_name == "t" and e2.payload == {"path": "/x"} and e2.event_id == e.event_id
