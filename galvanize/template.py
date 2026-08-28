"""Prompt template rendering.

Templates use dot-notation into the event payload: {pull_request.title},
{__raw__} dumps the whole payload as JSON. This mirrors the placeholder
syntax Hermes webhook routes use, so users learn one syntax. Rendering
happens inside galvanize (not on the Hermes route) so the exact same
prompt works for the shell dispatcher — the Hermes route just receives
the finished prompt as {prompt}.
"""

from __future__ import annotations

import json
import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_.]+)\}")


def render(template: str, payload: dict, *, raw_limit: int = 4000) -> str:
    """Render *template* against *payload*. Empty template dumps the payload."""
    if not template:
        truncated = json.dumps(payload, indent=2, ensure_ascii=False)[:raw_limit]
        return f"Event payload:\n\n```json\n{truncated}\n```"

    def _resolve(match: re.Match) -> str:
        key = match.group(1)
        if key == "__raw__":
            return json.dumps(payload, indent=2, ensure_ascii=False)[:raw_limit]
        value: Any = payload
        for part in key.split("."):
            if isinstance(value, dict):
                if part not in value:
                    return match.group(0)
                value = value[part]
            else:
                return match.group(0)
        if isinstance(value, (dict, list)):
            return json.dumps(value, indent=2, ensure_ascii=False)[:2000]
        return str(value)

    return _PLACEHOLDER.sub(_resolve, template)
