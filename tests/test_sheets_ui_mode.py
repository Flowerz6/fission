"""Unit tests for the Sheets multi-turn ui_mode adapter.

Everything that does NOT need a live browser: the multi-turn target
lifecycle (one sheet + one conversation across turns, reset only on turn 1,
teardown respects ownership, browser-mint hosts once), the selector-key
contract, and registration as ``google_sheets_multiturn``. The Playwright
side-panel drive itself needs live selector validation — see
docs/SHEETS_UI_MODE_RUNBOOK.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from advtest_ext.adapters.sheets.ui_mode.multi_turn_target import (
    GeminiSheetsMultiTurnTarget,
)
from advtest_ext.adapters.sheets.ui_mode.selectors import EXPECTED_KEYS, SELECTORS
from advtest_ext.document.document import AbstractDocument
from advtest_ext.document.target import DocumentCapabilities, Response

_EMPTY_DOC = AbstractDocument(id="empty", title="", body_blocks=[])


class _FakeSession:
    def __init__(self) -> None:
        self.page = object()
        self.ensure_page_calls = 0
        self.closed = False

    async def ensure_page(self) -> Any:
        self.ensure_page_calls += 1
        return self.page

    async def close(self) -> None:
        self.closed = True


def _recording_invoke_fn(calls: list[dict[str, Any]]) -> Any:
    async def invoke_fn(**kwargs: Any) -> Response:
        calls.append(kwargs)
        return Response(text=f"reply to: {kwargs['user_prompt']}", raw={})

    return invoke_fn


def _make_target(
    *, calls: list[dict[str, Any]], session: _FakeSession, sheet_id: str | None = None
):
    deleted: list[str] = []
    target = GeminiSheetsMultiTurnTarget(
        output_dir=Path("/tmp/out"),
        sheet_id=sheet_id,
        trace_prefix="test",
        session=session,
        invoke_fn=_recording_invoke_fn(calls),
        sheet_factory=lambda: "SHEET-NEW",
        sheet_deleter=lambda sid: deleted.append(sid),
    )
    return target, deleted


# ----- selector contract + registration -------------------------------


def test_selectors_have_expected_keys() -> None:
    assert set(SELECTORS.keys()) == EXPECTED_KEYS


def test_registered_as_google_sheets_multiturn() -> None:
    from advtest_ext._cli_factories import _resolve_target

    target = _resolve_target("google_sheets_multiturn", {"ui_output_dir": "./out"})
    assert isinstance(target, GeminiSheetsMultiTurnTarget)


def test_unknown_target_option_is_rejected() -> None:
    import pytest
    import typer

    from advtest_ext._cli_factories import _resolve_target

    with pytest.raises(typer.BadParameter):
        _resolve_target("google_sheets_multiturn", {"doc_id": "x"})  # Docs key, not Sheets


# ----- multi-turn lifecycle -------------------------------------------


def test_capabilities_declare_multi_turn() -> None:
    t = GeminiSheetsMultiTurnTarget(output_dir="/tmp", session=_FakeSession())
    assert t.capabilities() == DocumentCapabilities(multi_turn=True)


async def test_render_is_idempotent_one_sheet_per_run() -> None:
    target, _ = _make_target(calls=[], session=_FakeSession())
    ctx1 = await target.render_document(_EMPTY_DOC)
    ctx2 = await target.render_document(_EMPTY_DOC)
    assert ctx1 is ctx2
    assert ctx1.rendered["spreadsheet_id"] == "SHEET-NEW"


async def test_first_turn_resets_then_continues() -> None:
    calls: list[dict[str, Any]] = []
    target, _ = _make_target(calls=calls, session=_FakeSession())
    ctx = await target.render_document(_EMPTY_DOC)
    await target.invoke(ctx, "turn 1")
    await target.invoke(ctx, "turn 2")
    assert [c["reset_conversation"] for c in calls] == [True, False]
    # The driver is handed the spreadsheet id, not a doc id.
    assert all(c["spreadsheet_id"] == "SHEET-NEW" for c in calls)
    assert calls[0]["user_prompt"] == "turn 1"
    assert [c["trace_id"] for c in calls] == ["test-c1-t1", "test-c1-t2"]


async def test_reset_uses_a_new_capture_namespace() -> None:
    calls: list[dict[str, Any]] = []
    target, _ = _make_target(calls=calls, session=_FakeSession(), sheet_id="SHEET-PINNED")
    ctx = await target.render_document(_EMPTY_DOC)
    await target.invoke(ctx, "node 1")
    target.reset()
    ctx = await target.render_document(_EMPTY_DOC)
    await target.invoke(ctx, "node 2")

    assert [c["trace_id"] for c in calls] == ["test-c1-t1", "test-c2-t1"]
    assert len({c["trace_id"] for c in calls}) == 2


async def test_teardown_deletes_owned_sheet_and_close_leaves_injected_session() -> None:
    session = _FakeSession()
    target, deleted = _make_target(calls=[], session=session)
    ctx = await target.render_document(_EMPTY_DOC)
    await target.teardown(ctx)
    assert deleted == ["SHEET-NEW"]
    # An injected session belongs to the caller — close() must NOT close it.
    await target.close()
    assert session.closed is False


async def test_supplied_sheet_id_is_used_and_never_deleted() -> None:
    session = _FakeSession()
    target, deleted = _make_target(calls=[], session=session, sheet_id="SHEET-PINNED")
    ctx = await target.render_document(_EMPTY_DOC)
    assert ctx.rendered["spreadsheet_id"] == "SHEET-PINNED"
    await target.teardown(ctx)
    await target.close()
    assert deleted == []  # a caller-supplied sheet is never deleted


async def test_sheet_ids_rotate_one_per_node() -> None:
    session = _FakeSession()
    target = GeminiSheetsMultiTurnTarget(
        output_dir=Path("/tmp/out"),
        sheet_ids=["S1", "S2"],
        session=session,
        invoke_fn=_recording_invoke_fn([]),
    )
    ctx_a = await target.render_document(_EMPTY_DOC)
    assert ctx_a.rendered["spreadsheet_id"] == "S1"
    target.reset()  # next node
    ctx_b = await target.render_document(_EMPTY_DOC)
    assert ctx_b.rendered["spreadsheet_id"] == "S2"


class _MintPage:
    """Fake browser page whose URL becomes a spreadsheet URL after goto."""

    def __init__(self) -> None:
        self.url = "https://docs.google.com/spreadsheets/create"

    async def goto(self, url: str, **_: Any) -> None:
        self.url = "https://docs.google.com/spreadsheets/d/MINTED_SHEET_1234567890/edit"


# ----- invoker read hardening (no stale reads, fail loud on empty) ----


class _LoadingLoc:
    async def wait_for(self, *, state: str, timeout: int) -> None:
        raise TimeoutError("no loading indicator")  # simulate a selector miss


class _Bubble:
    def __init__(self, text: str, exists: bool) -> None:
        self._t, self._exists = text, exists

    async def wait_for(self, *, state: str, timeout: int) -> None:
        if not self._exists:
            raise TimeoutError("bubble does not exist")

    async def inner_text(self) -> str:
        return self._t


class _RespLoc:
    def __init__(self, bubbles: list[str]) -> None:
        self._b = bubbles

    async def count(self) -> int:
        return len(self._b)

    def nth(self, i: int) -> _Bubble:
        return _Bubble(self._b[i] if i < len(self._b) else "", exists=i < len(self._b))

    @property
    def last(self) -> _Bubble:
        return _Bubble(self._b[-1] if self._b else "", exists=bool(self._b))


class _FakePage:
    def __init__(
        self,
        bubbles: list[str],
        chips: list[str] | None = None,
        thoughts: list[str] | None = None,
    ) -> None:
        self._bubbles = bubbles
        self._chips = chips or []
        self._thoughts = thoughts or []

    def locator(self, sel: str) -> Any:
        if "ThinkingState" in sel:
            return _LoadingLoc()
        if "hought" in sel or "easoning" in sel:  # model-thoughts selectors
            return _RespLoc(self._thoughts)
        if "uggestion" in sel:  # the suggestion-chip selector
            return _RespLoc(self._chips)
        return _RespLoc(self._bubbles)


def _fast_settle(monkeypatch: Any) -> Any:
    from advtest_ext.adapters.sheets.ui_mode import invoker as inv

    monkeypatch.setattr(inv, "_SETTLE_MAX_ROUNDS", 3)
    monkeypatch.setattr(inv, "_SETTLE_POLL_S", 0)
    return inv


async def test_read_returns_the_fresh_reply(monkeypatch: Any) -> None:
    inv = _fast_settle(monkeypatch)
    text = await inv._read_response(_FakePage(["skills: get_skills"]), timeout_ms=500, prev_count=0)
    assert "get_skills" in text


async def test_read_raises_on_empty_reply(monkeypatch: Any) -> None:
    from advtest_ext.adapters._ui_mode_common import SidePanelUnavailable

    inv = _fast_settle(monkeypatch)
    import pytest

    # A new bubble appears but is empty → likely a drifted selector → fail loud.
    with pytest.raises(SidePanelUnavailable):
        await inv._read_response(_FakePage([""]), timeout_ms=500, prev_count=0)


async def test_read_does_not_return_stale_previous_bubble(monkeypatch: Any) -> None:
    from advtest_ext.adapters._ui_mode_common import SidePanelUnavailable

    inv = _fast_settle(monkeypatch)
    import pytest

    # prev_count=1 but only the OLD bubble exists (no NEW one) and no chips →
    # must raise, NOT return the stale "old reply" (the back-to-back bug).
    with pytest.raises(SidePanelUnavailable):
        await inv._read_response(_FakePage(["old reply"]), timeout_ms=500, prev_count=1)


async def test_read_captures_choice_chips_when_no_text_bubble(monkeypatch: Any) -> None:
    inv = _fast_settle(monkeypatch)
    # Gemini replied with clickable choice chips, no text bubble → the attacker
    # should still SEE the options (not an empty-read error).
    page = _FakePage(bubbles=[], chips=["Summarise the sheet", "Explain the data"])
    text = await inv._read_response(page, timeout_ms=500, prev_count=0)
    assert "Summarise the sheet" in text
    assert "choose" in text.lower()


async def test_read_appends_chips_to_a_text_reply(monkeypatch: Any) -> None:
    inv = _fast_settle(monkeypatch)
    page = _FakePage(bubbles=["Here are the skills"], chips=["Show more"])
    text = await inv._read_response(page, timeout_ms=500, prev_count=0)
    assert "Here are the skills" in text
    assert "Show more" in text


async def test_read_model_thoughts_returns_reasoning(monkeypatch: Any) -> None:
    inv = _fast_settle(monkeypatch)
    page = _FakePage(bubbles=["reply"], thoughts=["The user wants get_skills; I'll refuse."])
    thoughts = await inv._read_model_thoughts(page)
    assert thoughts == "The user wants get_skills; I'll refuse."


async def test_read_model_thoughts_absent_returns_empty(monkeypatch: Any) -> None:
    inv = _fast_settle(monkeypatch)
    page = _FakePage(bubbles=["reply"], thoughts=[])
    assert await inv._read_model_thoughts(page) == ""


async def test_model_thoughts_stay_out_of_the_reply_text(monkeypatch: Any) -> None:
    # The judge scores the reply text; thoughts must NEVER leak into it (they'd
    # trip the deterministic leak regex on reasoning-about-refusing).
    inv = _fast_settle(monkeypatch)
    page = _FakePage(bubbles=["I can't share that."], thoughts=["get_skills lives in my registry"])
    reply = await inv._read_response(page, timeout_ms=500, prev_count=0)
    assert "get_skills" not in reply


async def test_browser_mint_hosts_once_via_browser_no_api() -> None:
    class _MintSession:
        def __init__(self) -> None:
            self.page = _MintPage()

        async def ensure_page(self) -> Any:
            return self.page

        async def close(self) -> None:
            pass

    minted: list[str] = []
    target = GeminiSheetsMultiTurnTarget(
        output_dir=Path("/tmp/out"),
        browser_mint=True,
        session=_MintSession(),
        invoke_fn=_recording_invoke_fn([]),
        # sheet_factory must NOT be called in browser-mint mode.
        sheet_factory=lambda: minted.append("api") or "API-SHEET",  # type: ignore[func-returns-value]
    )
    ctx = await target.render_document(_EMPTY_DOC)
    assert ctx.rendered["spreadsheet_id"] == "MINTED_SHEET_1234567890"
    assert minted == []  # no API create in browser-mint mode
