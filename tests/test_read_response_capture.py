"""Tests for the multi-turn response-capture fix.

The bug: in a continuing conversation, ``_read_response`` read ``.last``
on the fast path — but ``.last`` is the PREVIOUS turn's bubble (or the
panel-open auto-summary) until the new response renders, so captured
replies were stale. Fix: read the bubble at index ``prev_count`` (the
NEW one), falling back to ``.last`` only when no new bubble appears.
"""

from __future__ import annotations

from typing import Any

import pytest

from advtest_ext.adapters.google_docs.ui_mode.invoker import (
    _count_responses,
    _read_response,
)
from advtest_ext.adapters.google_docs.ui_mode.selectors import SELECTORS


class _Loc:
    def __init__(self, texts: list[str], idx: int | None = None) -> None:
        self._texts = texts
        self._idx = idx

    async def count(self) -> int:
        return len(self._texts)

    def nth(self, i: int) -> _Loc:
        return _Loc(self._texts, idx=i)

    @property
    def last(self) -> _Loc:
        return _Loc(self._texts, idx=len(self._texts) - 1)

    async def wait_for(self, *, state: str, timeout: int) -> None:
        idx = self._idx if self._idx is not None else 0
        if idx >= len(self._texts):
            raise TimeoutError("bubble not present yet")

    async def inner_text(self, **kwargs: Any) -> str:
        idx = self._idx if self._idx is not None else len(self._texts) - 1
        return self._texts[idx]


class _LoadingLoc:
    async def wait_for(self, *, state: str, timeout: int) -> None:
        # Force the fast path: the loading indicator "never appears".
        raise TimeoutError("no loading indicator")


class _Page:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def locator(self, selector: str) -> Any:
        if selector == SELECTORS["side_panel_loading_indicator"]:
            return _LoadingLoc()
        return _Loc(self._texts)


@pytest.mark.asyncio
async def test_count_responses() -> None:
    assert await _count_responses(_Page(["a", "b", "c"])) == 3


@pytest.mark.asyncio
async def test_reads_only_new_blocks_excludes_old_bubbles() -> None:
    # One OLD bubble existed before submit (prev_count=1); this turn's
    # reply is index 1. The old bubble (index 0) must be EXCLUDED.
    page = _Page(["OLD/stale bubble from a prior turn", "NEW response for this turn"])
    text = await _read_response(page, timeout_ms=1000, prev_count=1)
    assert text == "NEW response for this turn"


@pytest.mark.asyncio
async def test_joins_all_new_blocks_of_one_long_reply() -> None:
    # A long structured reply renders as SEVERAL bubble blocks; every
    # new block (index >= prev_count) is joined into the full text, so a
    # long reply isn't truncated to its first block.
    page = _Page(["prior turn reply", "Heading + intro", "section two", "section three"])
    text = await _read_response(page, timeout_ms=1000, prev_count=1)
    assert "Heading + intro" in text
    assert "section two" in text
    assert "section three" in text


@pytest.mark.asyncio
async def test_reads_correct_bubble_on_later_turn() -> None:
    # Two prior bubbles existed before submit (prev_count=2); the new
    # one is index 2.
    page = _Page(["turn1 reply", "turn2 reply", "turn3 NEW reply"])
    text = await _read_response(page, timeout_ms=1000, prev_count=2)
    assert text == "turn3 NEW reply"


@pytest.mark.asyncio
async def test_falls_back_to_last_when_no_new_bubble() -> None:
    # prev_count overshoots (no new bubble rendered) → fall back to .last.
    page = _Page(["only bubble"])
    text = await _read_response(page, timeout_ms=1000, prev_count=5)
    assert text == "only bubble"
