"""Self-healing selectors: when a side-panel selector drifts, capture the live
DOM, have an LLM propose a replacement, and **validate it against the real page
deterministically** before accepting. The LLM proposes; the live DOM decides.

Flow (driven by ``scripts/heal_selectors.py``):

1. :func:`find_drifted` — which base selectors no longer match the open page.
2. :func:`capture_candidates` — pull the relevant elements out of the DOM.
3. :func:`propose_fixes` — one LLM call → a ``{key: selector}`` proposal.
4. :func:`validate_fix` — accept only if the proposal matches on the live page
   (and, for singletons, matches *exactly one* element). This is the guardrail
   against a plausible-but-wrong LLM selector.

Everything here is dependency-injected (``page`` is any object exposing
``locator(sel).count()`` / ``evaluate(js)``; ``provider`` is any
:class:`~advtest_ext.providers.base.Provider`), so the logic is unit-tested
with fakes — no Playwright, no browser, no real LLM.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from advtest_ext.providers.base import ChatMessage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from advtest_ext.providers.base import Provider

# Selectors expected to resolve to exactly ONE element (an entry button, the
# panel container, the input, …). The response bubble is many-per-conversation
# and the invoker takes ``.last``, so it's excluded from the uniqueness rule.
_SINGLETON_KEYS = frozenset(
    {
        "side_panel_button",
        "side_panel_container",
        "side_panel_input",
        "side_panel_send",
        "side_panel_close",
        "side_panel_new_conversation",
    }
)

# Runs in the page: the elements a selector author (human or LLM) needs to see.
_PROBE_JS = r"""
() => {
  const q = (sel) => Array.from(document.querySelectorAll(sel));
  const desc = (el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role'),
    ariaLabel: el.getAttribute('aria-label'),
    contenteditable: el.getAttribute('contenteditable'),
    className: (el.className && el.className.toString ? el.className.toString() : '').slice(0, 160),
    text: (el.innerText || '').trim().slice(0, 80),
  });
  const labelled = q('[aria-label]')
    .filter(el => /gemini|send|new conversation|close|ask/i.test(el.getAttribute('aria-label') || ''))
    .map(desc);
  const complementary = q('[role="complementary"]').map(desc);
  const editables = q('[contenteditable="true"]').map(desc);
  const sidekick = Array.from(new Set(
    q('[class*="appsElementsSidekick"]').map(el => (el.className || '').toString())
  )).slice(0, 40);
  return { labelled, complementary, editables, sidekickClasses: sidekick };
}
"""

_HEAL_SYSTEM = """\
You repair broken CSS/ARIA selectors for the Google Workspace Gemini side panel.
You are given the selector keys that no longer match, a one-line INTENT for each,
and a CANDIDATES dump of the live DOM (labelled elements, complementary regions,
contenteditable fields, and the shared appsElementsSidekick* classes).

For each broken key, choose the SINGLE best selector from the real elements in the
candidates. Prefer stable ARIA/role + accessible-name matchers
(e.g. [role="complementary"][aria-label="Gemini"], button[aria-label*="Send" i])
over brittle generated class names. Scope panel-internal selectors under the
container. Do NOT invent attributes that aren't in the candidates.

Output ONLY a JSON object mapping each broken key to its selector string, nothing else.
"""


@dataclass
class HealReport:
    """Outcome of one heal pass."""

    drifted: list[str] = field(default_factory=list)
    fixes: dict[str, str] = field(default_factory=dict)  # accepted (validated on page)
    rejected: dict[str, str] = field(default_factory=dict)  # proposal that failed validation

    @property
    def healed_all(self) -> bool:
        return bool(self.drifted) and not self.rejected


async def _count(page: Any, selector: str) -> int:
    """Number of elements ``selector`` matches on ``page`` (0 on any error —
    a selector that raises is, for our purposes, a selector that doesn't match)."""
    try:
        return int(await page.locator(selector).count())
    except Exception:
        return 0


async def find_drifted(page: Any, selectors: Mapping[str, str]) -> list[str]:
    """The keys whose current selector matches nothing on the open page."""
    drifted: list[str] = []
    for key, selector in selectors.items():
        if await _count(page, selector) == 0:
            drifted.append(key)
    return drifted


async def capture_candidates(page: Any) -> dict[str, Any]:
    """Pull the candidate elements out of the live DOM (see ``_PROBE_JS``)."""
    result: dict[str, Any] = await page.evaluate(_PROBE_JS)
    return result


def _extract_json(text: str) -> dict[str, Any]:
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    blob = fence.group(1) if fence else text
    start, end = blob.find("{"), blob.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in heal proposal: {text[:200]!r}")
    parsed: dict[str, Any] = json.loads(blob[start : end + 1])
    return parsed


async def propose_fixes(
    provider: Provider,
    *,
    drifted: Sequence[str],
    intents: Mapping[str, str],
    candidates: Mapping[str, Any],
) -> dict[str, str]:
    """One LLM call → proposed selectors for the drifted keys. Returns only
    string proposals for keys that were actually requested."""
    user = (
        "BROKEN KEYS (key: intent):\n"
        + "\n".join(f"- {k}: {intents.get(k, '(no intent given)')}" for k in drifted)
        + "\n\nCANDIDATES (live DOM):\n"
        + json.dumps(candidates, ensure_ascii=False)[:12000]
    )
    resp = await provider.complete(
        [
            ChatMessage(role="system", content=_HEAL_SYSTEM),
            ChatMessage(role="user", content=user),
        ],
        temperature=0.0,
        max_tokens=800,
    )
    proposed = _extract_json(resp.text)
    wanted = set(drifted)
    return {k: v for k, v in proposed.items() if k in wanted and isinstance(v, str) and v.strip()}


async def validate_fix(page: Any, key: str, selector: str) -> bool:
    """Accept ``selector`` only if it resolves on the live page — and, for a
    singleton key, resolves to *exactly one* element (so a proposal that
    over-matches the whole panel is rejected)."""
    count = await _count(page, selector)
    if count == 0:
        return False
    if key in _SINGLETON_KEYS and count != 1:
        return False
    return True


async def heal(
    page: Any,
    provider: Provider,
    *,
    selectors: Mapping[str, str],
    intents: Mapping[str, str],
) -> HealReport:
    """Full pass: find drift, propose, validate on the live page. Accepted
    fixes go in ``report.fixes`` (write them to the overlay); proposals that
    failed validation go in ``report.rejected`` for the human to inspect."""
    drifted = await find_drifted(page, selectors)
    if not drifted:
        return HealReport()
    candidates = await capture_candidates(page)
    proposed = await propose_fixes(
        provider, drifted=drifted, intents=intents, candidates=candidates
    )
    report = HealReport(drifted=drifted)
    for key in drifted:
        selector = proposed.get(key)
        if selector and await validate_fix(page, key, selector):
            report.fixes[key] = selector
        else:
            report.rejected[key] = selector or "(no proposal)"
    return report


__all__ = [
    "HealReport",
    "capture_candidates",
    "find_drifted",
    "heal",
    "propose_fixes",
    "validate_fix",
]
