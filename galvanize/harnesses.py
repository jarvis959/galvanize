"""Register galvanize MCP into detected harnesses (exactly-one-surface).

init calls register_all(): for each harness found on PATH/disk, write the
galvanize MCP entry into its config — unless that harness already has the
native plugin surface (surfaces.json), or the user already has galvanize
MCP registered (idempotent update).

Harnesses handled: Claude Code (~/.claude.json mcpServers), Codex
(~/.codex/config.toml [mcp_servers.galvanize]), DSH (~/.dsh — MCP config
varies by version; we write surfaces.json intent + print instructions
rather than guessing a schema we haven't verified). Hermes is NOT here —
Hermes gets the native plugin (Tier 1), and doctor flags both-active.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from .serve import read_surfaces, surfaces_path, write_surfaces


def _mcp_command() -> List[str]:
    """Command vector that launches `galvanize mcp` from any harness."""
    exe = Path(sys.executable)
    cand = exe.parent / "galvanize.exe"
    if cand.exists():
        return [str(cand), "mcp"]
    # Any python-shaped interpreter (python.exe, python, python3.11 under
    # pipx/uv venvs on Linux/macOS) -> module form with the absolute path,
    # which beats depending on the shim being on the harness's PATH.
    if exe.name.lower().startswith("python"):
        return [str(exe), "-m", "galvanize.mcp"]
    return ["galvanize", "mcp"]


def claude_home() -> Path:
    return Path.home() / ".claude.json"


def codex_home() -> Path:
    return Path.home() / ".codex"


def detect_harnesses() -> Dict[str, bool]:
    import shutil
    return {
        "claude": (claude_home().exists() or bool(shutil.which("claude"))),
        "codex": (codex_home().is_dir() or bool(shutil.which("codex"))),
        "dsh": (Path.home() / ".dsh").is_dir() or bool(shutil.which("dsh")),
    }


def register_claude() -> Tuple[bool, str]:
    p = claude_home()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        servers = data.setdefault("mcpServers", {})
        already = "galvanize" in servers
        servers["galvanize"] = {"command": _mcp_command()[0],
                                "args": _mcp_command()[1:]}
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True, ("updated" if already else "Claude Code: MCP registered")
    except Exception as e:
        return False, f"Claude Code MCP failed: {e}"


def register_codex() -> Tuple[bool, str]:
    """Codex config.toml: append/replace [mcp_servers.galvanize] block."""
    cfg = codex_home() / "config.toml"
    marker = "[mcp_servers.galvanize]"
    try:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        cmd = _mcp_command()
        # forward slashes + quotes: TOML treats backslashes as escapes (\Users
        # -> invalid unicode escape), and Codex on Windows reads both forms.
        cmd = [c.replace("\\", "/") for c in cmd]
        # default_tools_approval_mode is REQUIRED, not politeness: without it
        # Codex >=0.150 holds every tool "pending optional" and silently omits
        # them from the model's catalog (verified live, codex 0.150.1).
        # Galvanize's tools are local trigger management; pre-declaring
        # approval is the PLAN §4.1 "permission mode pre-declared, never
        # auto-elevated" stance applied to Codex.
        block = (marker + "\n"
                 + f"command = \"{cmd[0]}\"\n"
                 + "args = [" + ", ".join(f"\"{a}\"" for a in cmd[1:]) + "]\n"
                 + "startup_timeout_sec = 20\n"
                 + 'default_tools_approval_mode = "approve"\n')
        if marker in text:
            # replace existing block (to next [section] or EOF)
            start = text.index(marker)
            rest = text[start + len(marker):]
            import re
            m = re.search(r"\n\[", rest)
            end = start + len(marker) + (m.start() + 1 if m else len(rest))
            text = text[:start] + block + text[end:]
            action = "updated"
        else:
            text = text.rstrip() + ("\n\n" if text.strip() else "") + block
            action = "registered"
        cfg.write_text(text, encoding="utf-8")
        return True, f"Codex CLI: MCP {action} ({cfg})"
    except Exception as e:
        return False, f"Codex MCP failed: {e}"


def note_dsh() -> Tuple[bool, str]:
    surf = read_surfaces()
    if surf.get("dsh") == "plugin":
        return True, "DSH: native plugin active — MCP skipped (exactly-one-surface)"
    surf["dsh"] = "mcp"
    write_surfaces(surf)
    cmd = _mcp_command()
    return True, ("DSH: run in the DSH profile:  dsh mcp add --command '"
                  + " ".join(cmd) + "' (surface recorded as mcp)")


def register_all(print_lines=True) -> List[str]:
    out: List[str] = []
    found = detect_harnesses()
    surf = read_surfaces()
    if found["claude"] and surf.get("claude") != "plugin":
        ok, msg = register_claude()
        if ok:
            surf = read_surfaces(); surf["claude"] = "mcp"; write_surfaces(surf)
        out.append(("✔ " if ok else "⚠ ") + msg)
    if found["codex"] and surf.get("codex") != "plugin":
        ok, msg = register_codex()
        if ok:
            surf = read_surfaces(); surf["codex"] = "mcp"; write_surfaces(surf)
        out.append(("✔ " if ok else "⚠ ") + msg)
    if found["dsh"]:
        ok, msg = note_dsh()
        out.append(("✔ " if ok else "⚠ ") + msg)
    if not any(found.values()):
        out.append("  (no other agent harnesses detected — MCP available via "
                   "`galvanize mcp` when one is installed)")
    return out
