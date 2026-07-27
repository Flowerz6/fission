"""Assemble the complete Gemini response text from an intercepted
Workspace ``streamGenerate`` capture.

The Phase-2 traffic interceptor (``interception.py``) writes one JSONL
line per captured HTTP response. For ``streamGenerate`` the
``response_body`` is Google's nested-array RPC format; the response prose
lives inside, several layers down and inter-mixed with internal structure
strings.

This is the source of truth for what Gemini actually said. It exists
because Playwright's ``inner_text`` read of the side panel is captured
while the response is *still streaming* and is therefore frequently
truncated (we've measured 81 chars stored vs 1764 in the stream). Both
the ui_mode invokers (for the persisted ``raw_response``) and the
dashboard (for the trace detail page) use this so no view shows a
truncated answer.

Best-effort and format-defensive — the RPC shape is undocumented and may
change; we return ``""`` rather than raise on a body we can't parse.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Minimum chars + presence of whitespace → treat as prose. Filters out
# internal IDs ("c2b1-..."), enum strings ("UNSPECIFIED"), and short
# tokens that would otherwise contaminate the "longest fragment" heuristic.
_MIN_PROSE_LEN = 20


def assemble_full_response(jsonl_path: Path | str) -> str:
    """Return the longest prose-shaped text fragment from every
    ``streamGenerate`` response captured in ``jsonl_path``.

    For Gemini's streamed responses each near-final cumulative snapshot is
    a long prose blob and the internal structure strings are short, so the
    longest fragment is reliably the complete assembled response. Returns
    ``""`` if the file is missing, empty, has no streamGenerate responses,
    or no fragment passes the prose filter. Never raises — bad JSON is
    skipped silently.
    """
    path = Path(jsonl_path)
    if not path.exists():
        return ""

    fragments: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "streamGenerate" not in entry.get("url", ""):
            continue
        body = entry.get("response_body", "")
        if not body:
            continue
        _walk_body(body, fragments)

    if not fragments:
        return ""
    return max(fragments, key=len)


def _walk_body(body: str, out: list[str]) -> None:
    """Parse ``body`` as JSON, fall back to NDJSON, then walk every value
    collecting prose-shaped strings into ``out``."""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        for chunk_line in body.splitlines():
            chunk_line = chunk_line.strip()
            if not chunk_line:
                continue
            try:
                chunk = json.loads(chunk_line)
            except json.JSONDecodeError:
                continue
            _collect_strings(chunk, out)
        return
    _collect_strings(parsed, out)


def _collect_strings(node: Any, out: list[str]) -> None:
    """Walk a parsed JSON value; append every string that looks like prose
    (contains whitespace, longer than ``_MIN_PROSE_LEN``)."""
    if isinstance(node, str):
        if len(node) >= _MIN_PROSE_LEN and (" " in node or "\n" in node):
            out.append(node)
        return
    if isinstance(node, list):
        for item in node:
            _collect_strings(item, out)
        return
    if isinstance(node, dict):
        for value in node.values():
            _collect_strings(value, out)


__all__ = ["assemble_full_response"]
