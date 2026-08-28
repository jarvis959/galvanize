"""Dispatchers: translate an event into "start a fresh agent session".

wake.kind == "hermes":  POST into the Hermes webhook lane (HMAC V2).
wake.kind == "shell":   run a command template ({prompt}/{payload}
                        placeholders, GALVANIZE_* env) — the escape hatch
                        that covers claude -p / codex exec / anything else.

Recursion guard: every spawn carries GALVANIZE_SPAWN=1 so scripts and
hooks can refuse to re-trigger themselves.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Dict, Tuple

from . import hermes as hermes_mod
from . import state
from .events import Event
from .template import render


def dispatch(trigger, event: Event, prompt: str) -> Tuple[bool, str]:
    """Dispatch one event for *trigger*. Returns (ok, detail)."""
    kind = trigger.wake_kind
    try:
        if kind == "hermes":
            ok, detail = _wake_hermes(trigger, event, prompt)
        elif kind == "shell":
            ok, detail = _wake_shell(trigger, event, prompt)
        else:
            ok, detail = False, f"unknown wake.kind '{kind}'"
    except hermes_mod.HermesNotConfigured as e:
        ok, detail = False, str(e)
    except Exception as e:  # dispatcher must never crash the bus
        ok, detail = False, f"{type(e).__name__}: {e}"
    state.record_fire(trigger.name, ok=ok, detail=detail)
    return ok, detail


def _wake_hermes(trigger, event: Event, prompt: str) -> Tuple[bool, str]:
    status, resp = hermes_mod.post_event(
        trigger.name,
        event,
        prompt=prompt,
        secret=trigger.wake.get("secret") or None,
    )
    if status in (200, 202):
        sess = resp.get("delivery_id") or resp.get("status", "ok")
        return True, f"hermes session spawned ({resp.get('status', '?')}, id {sess})"
    return False, f"hermes POST {status}: {json.dumps(resp)[:200]}"


def _wake_shell(trigger, event: Event, prompt: str) -> Tuple[bool, str]:
    command = str(trigger.wake.get("command", ""))
    body = json.dumps(event.to_body(), ensure_ascii=False)
    rendered = command.replace("{payload}", body.replace("'", "'\\''"))
    # {prompt} injected via env so quoting can't break the command line.
    env = dict(os.environ)
    env["GALVANIZE_SPAWN"] = "1"
    env["GALVANIZE_TRIGGER"] = trigger.name
    env["GALVANIZE_PROMPT"] = prompt
    # Pass the template to the platform shell as a raw STRING with shell=True:
    # argv-list form gets quote-mangled by cmd.exe on Windows.
    if "{" in command and "prompt}" in command:
        # literal {prompt} in the template -> shell-expand from env.
        # Quoted on POSIX so a prompt with spaces survives word-splitting
        # (""$VAR"" inside an existing quoted region is still one word).
        token = "%GALVANIZE_PROMPT%" if os.name == "nt" else '"$GALVANIZE_PROMPT"'
        rendered = rendered.replace("{prompt}", token)
    timeout = float(trigger.wake.get("timeout_s", 300) or 300)
    cwd = str(trigger.wake.get("workdir", "") or "") or None
    try:
        proc = subprocess.run(
            rendered, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",   # never crash on non-UTF8 agent output
            timeout=timeout, cwd=cwd, shell=True,
            stdin=subprocess.DEVNULL,   # CLI agents must never wait on stdin
        )
    except subprocess.TimeoutExpired:
        return False, f"shell wake timed out after {timeout}s"
    out = (proc.stdout or "")[-400:]
    err = (proc.stderr or "")[-400:]
    if proc.returncode == 0:
        return True, f"exit 0: {out.strip() or 'ok'}"
    return False, f"exit {proc.returncode}: {err.strip() or out.strip()}"
