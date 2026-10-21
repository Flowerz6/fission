"""The response read retries once before declaring selector drift.

A read timeout against the live Gemini side panel is far more often a slow /
throttled panel than an actual DOM rename, so ``_read_response`` tries once
more (with a longer window) and only raises ``SidePanelUnavailable`` if that
also fails. These tests drive the google_docs invoker directly with a fake
whose response wait fails a configurable number of times, then succeeds.
"""

from __future__ import annotations

from typing import Any

import pytest

from advtest_ext.adapters._ui_mode_common import SidePanelUnavailable, response_read
from advtest_ext.adapters.google_docs.ui_mode import invoker as inv
from advtest_ext.adapters.google_docs.ui_mode.selectors import SELECTORS

_LOADING = SELECTORS["side_panel_loading_indicator"]
_RESPONSE = SELECTORS["side_panel_response"]


class _Loc:
    def __init__(self, page: _TransientPage, sel: str) -> None:
        self._page = page
        self._sel = sel

    async def wait_for(self, *, state: str, timeout: int) -> None:
        # Loading indicator never shows -> fast path (skips the hidden-wait).
        if self._sel == _LOADING:
            raise TimeoutError("indicator never mounted")
        if self._sel == _RESPONSE:
            self._page.resp_wait_calls += 1
            if self._page.resp_wait_calls <= self._page.response_fail_count:
                raise TimeoutError("bubble not visible yet (transient)")

    def nth(self, _i: int) -> _Loc:
        return self

    @property
    def last(self) -> _Loc:
        return self

    async def count(self) -> int:
        # The bubble "exists" only once the wait has succeeded.
        return 1 if self._page.resp_wait_calls > self._page.response_fail_count else 0

    async def inner_text(self, **_kw: Any) -> str:
        return "recovered reply"


class _TransientPage:
    def __init__(self, response_fail_count: int) -> None:
        self.response_fail_count = response_fail_count
        self.resp_wait_calls = 0

    def locator(self, sel: str) -> _Loc:
        return _Loc(self, sel)


@pytest.fixture(autouse=True)
def _fast_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(response_read, "_SETTLE_MAX_ROUNDS", 3)
    monkeypatch.setattr(response_read, "_SETTLE_POLL_S", 0)


@pytest.mark.asyncio
async def test_retry_recovers_a_transient_read_timeout() -> None:
    # First attempt (nth + last waits) fails; the retry's wait succeeds.
    page = _TransientPage(response_fail_count=2)
    text = await inv._read_response(page, timeout_ms=50, prev_count=0)
    assert text == "recovered reply"
    assert page.resp_wait_calls == 3  # 2 failed (nth, last), then 1 succeeded


@pytest.mark.asyncio
async def test_persistent_failure_still_raises_after_retry() -> None:
    # Every wait fails -> both attempts exhausted -> drift surfaced.
    page = _TransientPage(response_fail_count=99)
    with pytest.raises(SidePanelUnavailable, match="Selector drift suspected"):
        await inv._read_response(page, timeout_ms=50, prev_count=0)
    # 2 attempts x (nth + last) waits.
    assert page.resp_wait_calls == 4
