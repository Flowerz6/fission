"""Shared JSON-extraction helpers for attacker LLM outputs.

PAIR, TAP, and Crescendo all prompt the attacker LLM for a JSON
object with ``prompt`` + ``reasoning`` (and ``cover_story`` for
indirect-injection variants). Real LLMs sometimes wrap the JSON in
code fences or add a preamble, so the parser is tolerant:

  1. Strip a leading/trailing ``` code fence if present.
  2. Try parsing the whole stripped text as JSON.
  3. Otherwise scan for every balanced ``{...}`` block (string-aware
     so embedded ``{}`` inside string values don't break the brace
     counter) and try each in order.
  4. Fall back to the raw input as ``prompt`` so the round can
     continue rather than tearing down.

Previously each attack defined its own parser; the naive
``\\{.*\\}`` regex used by TAP and Crescendo grabbed from the first
``{`` to the last ``}`` and failed on preambles + JSON or when the
JSON itself embedded further ``{}`` inside string-valued fields
(tool-call mimicry, system-tag mimicry). This module replaces all
three with the string-aware brace-balanced scanner originally
written for PAIR.
"""

from __future__ import annotations

import json
import re
from typing import Literal, overload

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\n(.*?)\n```\s*$", re.DOTALL)

# String-aware match for a single JSON string field's value, used to
# recover fields when the surrounding object is malformed (see
# ``_recover_field``). Captures the escaped body between the quotes.
_FIELD_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _recover_field(text: str, field: str) -> str:
    r"""Pull one JSON string field out of malformed output.

    Real attacker LLMs occasionally emit a broken object — e.g. they
    close it early before ``prompt``, leaving
    ``{"reasoning": "..."}, "prompt": "..."}`` — which ``json.loads``
    rejects and the brace scanner can't recover (the first balanced
    block has no ``prompt``). Rather than ship the raw JSON wrapper
    downstream as the prompt, scan for the field with a string-aware
    regex and JSON-unescape the captured value."""
    pat = _FIELD_RE_CACHE.get(field)
    if pat is None:
        pat = re.compile(rf'"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)"', re.DOTALL)
        _FIELD_RE_CACHE[field] = pat
    m = pat.search(text)
    if not m:
        return ""
    try:
        return str(json.loads(f'"{m.group(1)}"')).strip()
    except json.JSONDecodeError:
        return m.group(1).strip()


def strip_code_fence(text: str) -> str:
    """Strip a single leading/trailing ``` fence (with optional
    language hint) if present. Sonnet sometimes wraps its JSON
    output in a fenced code block; the brace-balanced scanner below
    would otherwise miss the JSON body when the surrounding fence
    breaks ``json.loads`` on the whole text."""
    m = _CODE_FENCE_RE.match(text.strip())
    return m.group(1) if m else text


def iter_balanced_json_blocks(text: str) -> list[str]:
    """Yield every balanced ``{...}`` substring of ``text`` whose
    braces match outside of any string literal.

    String-aware so ``"prompt": "<tool>{...}</tool>"`` doesn't
    confuse the brace counter.
    """
    blocks: list[str] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        escape = False
        for j in range(i, n):
            ch = text[j]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        blocks.append(text[i : j + 1])
                        i = j + 1
                        break
        else:
            break
    return blocks


@overload
def parse_attacker_json(
    attacker_output: str,
    *,
    include_cover_story: Literal[False] = False,
) -> tuple[str, str]: ...


@overload
def parse_attacker_json(
    attacker_output: str,
    *,
    include_cover_story: Literal[True],
) -> tuple[str, str, str]: ...


def parse_attacker_json(
    attacker_output: str,
    *,
    include_cover_story: bool = False,
) -> tuple[str, str] | tuple[str, str, str]:
    """Pull ``(prompt, reasoning)`` (or ``(prompt, reasoning,
    cover_story)`` when ``include_cover_story=True``) out of the
    attacker LLM's output.

    ``cover_story`` is the per-round Doc-body text the indirect-
    injection layer downstream uses as the carrier for the hidden
    payload. Missing field → empty string; the delivery layer falls
    back to its static default cover.

    Returns ``("", "")`` / ``("", "", "")`` only if the input was
    empty; otherwise returns the raw input as ``prompt`` with empty
    reasoning (and empty cover_story) when no JSON parses.
    """
    raw = attacker_output.strip()
    text = strip_code_fence(raw)
    candidates: list[str] = [text]
    candidates.extend(iter_balanced_json_blocks(text))
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            prompt = str(data.get("prompt", "")).strip()
            reasoning = str(data.get("reasoning", "")).strip()
            if prompt:
                if include_cover_story:
                    cover = str(data.get("cover_story", "")).strip()
                    return prompt, reasoning, cover
                return prompt, reasoning
    # No well-formed object yielded a ``prompt``. Recover the fields
    # directly with a string-aware regex (handles objects closed early
    # before ``prompt``) so the JSON wrapper never ships as the prompt.
    prompt = _recover_field(text, "prompt")
    if prompt:
        reasoning = _recover_field(text, "reasoning")
        if include_cover_story:
            return prompt, reasoning, _recover_field(text, "cover_story")
        return prompt, reasoning
    if include_cover_story:
        return raw, "", ""
    return raw, ""
