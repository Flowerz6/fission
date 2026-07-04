"""Self-healing selectors: overlay load/merge/write + the propose/validate
heal loop. All with fakes — no Playwright, no browser, no real LLM.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from advtest_ext.adapters._ui_mode_common.selector_heal import (
    find_drifted,
    heal,
    propose_fixes,
    validate_fix,
)
from advtest_ext.adapters._ui_mode_common.selector_overlay import (
    load_overlay,
    resolve_selectors,
    write_overlay,
)

# ---- overlay -------------------------------------------------------------


def test_resolve_selectors_no_overlay_returns_base(tmp_path: Path) -> None:
    base = {"a": "sel-a", "b": "sel-b"}
    assert resolve_selectors(base, "google_docs", path=tmp_path / "missing.json") == base


def test_write_then_resolve_merges_only_known_keys(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.json"
    write_overlay("google_docs", {"a": "fixed-a", "stray": "nope"}, path=overlay)
    base = {"a": "sel-a", "b": "sel-b"}
    merged = resolve_selectors(base, "google_docs", path=overlay)
    assert merged == {"a": "fixed-a", "b": "sel-b"}  # 'a' fixed, 'stray' ignored


def test_write_overlay_is_additive_across_apps(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.json"
    write_overlay("google_docs", {"a": "x"}, path=overlay)
    write_overlay("google_sheets", {"a": "y"}, path=overlay)
    doc = json.loads(overlay.read_text())
    assert doc == {"google_docs": {"a": "x"}, "google_sheets": {"a": "y"}}


def test_malformed_overlay_degrades_to_base(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert load_overlay("google_docs", path=bad) == {}


# ---- heal loop -----------------------------------------------------------


class _FakePage:
    """Duck-typed page: ``locator(sel).count()`` returns a scripted count, and
    ``evaluate`` returns scripted candidates."""

    def __init__(self, counts: dict[str, int], candidates: dict[str, Any] | None = None) -> None:
        self._counts = counts
        self._candidates = candidates or {}

    def locator(self, selector: str) -> Any:
        async def _count() -> int:
            if selector not in self._counts:
                raise RuntimeError("selector not found")
            return self._counts[selector]

        return SimpleNamespace(count=_count)

    async def evaluate(self, _js: str) -> dict[str, Any]:
        return self._candidates


class _FakeProvider:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def complete(self, messages: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(text=self._reply)


async def test_find_drifted_flags_zero_match_selectors() -> None:
    page = _FakePage({"good": 1, "bad": 0})
    drifted = await find_drifted(page, {"side_panel_input": "good", "side_panel_button": "bad"})
    assert drifted == ["side_panel_button"]


async def test_validate_fix_rejects_non_unique_singleton() -> None:
    page = _FakePage({"one": 1, "many": 3, "none": 0})
    assert await validate_fix(page, "side_panel_button", "one") is True
    assert await validate_fix(page, "side_panel_button", "many") is False  # singleton must be 1
    assert await validate_fix(page, "side_panel_button", "none") is False
    # A non-singleton (response bubble) may match many.
    assert await validate_fix(page, "side_panel_response", "many") is True


async def test_propose_fixes_returns_only_requested_string_keys() -> None:
    provider = _FakeProvider(
        '```json\n{"side_panel_button": "button[aria-label=\\"Ask Gemini\\"]", '
        '"unrelated": "x", "side_panel_input": 5}\n```'
    )
    fixes = await propose_fixes(
        provider,
        drifted=["side_panel_button", "side_panel_input"],
        intents={"side_panel_button": "opens the panel"},
        candidates={"labelled": []},
    )
    assert fixes == {"side_panel_button": 'button[aria-label="Ask Gemini"]'}  # non-str dropped


async def test_heal_accepts_validated_and_rejects_unvalidated() -> None:
    # base 'button' selector is drifted (0). LLM proposes 'fixed-btn' (matches 1,
    # accepted) and 'fixed-x' for a key whose proposal matches 0 (rejected).
    page = _FakePage(
        counts={"old-btn": 0, "old-x": 0, "fixed-btn": 1, "fixed-x": 0, "ok-input": 1},
        candidates={"labelled": [{"ariaLabel": "Ask Gemini"}]},
    )
    provider = _FakeProvider('{"side_panel_button": "fixed-btn", "side_panel_close": "fixed-x"}')
    report = await heal(
        page,
        provider,
        selectors={
            "side_panel_button": "old-btn",
            "side_panel_close": "old-x",
            "side_panel_input": "ok-input",
        },
        intents={},
    )
    assert set(report.drifted) == {"side_panel_button", "side_panel_close"}
    assert report.fixes == {"side_panel_button": "fixed-btn"}
    assert "side_panel_close" in report.rejected
    assert report.healed_all is False


async def test_heal_noop_when_nothing_drifted() -> None:
    page = _FakePage({"a": 1, "b": 1})
    report = await heal(page, _FakeProvider("{}"), selectors={"x": "a", "y": "b"}, intents={})
    assert report.drifted == [] and report.fixes == {}
