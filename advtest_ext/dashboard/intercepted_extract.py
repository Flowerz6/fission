"""Extract the assembled response text from an intercepted
Workspace Gemini ``streamGenerate`` capture.

The Phase 2 traffic interceptor writes one JSONL line per
captured response (see
``adapters/_ui_mode_common/interception.py``). For
``streamGenerate`` endpoints the ``response_body`` field is
Google's nested-array RPC format — the rendered side-panel text
lives inside, but several layers down and inter-mixed with
internal structure.

This module exposes one function: :func:`extract_full_response`.
It walks every parsed value in the body, collects every prose-
shaped string fragment, and returns the longest one as the
assembled response. Best-effort and format-defensive — the
RPC shape is undocumented and may change; we'd rather return
"" than raise on a body we can't parse.

Useful for the trace detail page where ``trace.raw_response``
(captured from the rendered side panel) is often truncated
because Playwright reads inner_text while the response is
still streaming. The intercepted body is the full server-side
response and gives us the truth.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Minimum chars + presence of whitespace → treat as prose. Filters
# out internal IDs ("c2b1-..."), enum strings ("UNSPECIFIED"),
# and short tokens that would otherwise contaminate the "longest
# fragment" heuristic.
_MIN_PROSE_LEN = 20


def extract_full_response(jsonl_path: Path | str) -> str:
    """Return the longest prose-shaped text fragment from every
    ``streamGenerate`` response captured in ``jsonl_path``.

    Returns ``""`` if the file is missing, empty, contains no
    streamGenerate responses, or if no fragment passes the prose
    filter. Never raises on a malformed body — bad JSON is
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
        url = entry.get("url", "")
        if "streamGenerate" not in url:
            continue
        body = entry.get("response_body", "")
        if not body:
            continue
        _walk_body(body, fragments)

    if not fragments:
        return ""
    # Longest fragment wins. For Gemini's streamed responses this
    # is reliably the assembled final text — internal structure
    # strings are short, response prose is long.
    return max(fragments, key=len)


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _walk_body(body: str, out: list[str]) -> None:
    """Try to parse ``body`` as JSON, fall back to NDJSON, then walk
    every value collecting prose-shaped strings into ``out``."""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        # Body might be NDJSON (newline-delimited JSON chunks) —
        # parse each non-empty line independently and walk.
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
    """Walk a parsed JSON value; append every string that looks like
    prose (contains whitespace, longer than ``_MIN_PROSE_LEN``)."""
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


__all__ = ["extract_full_response"]
