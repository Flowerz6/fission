"""Unit tests for GeminiGmailMultiTurnTarget (provider-free Gmail UI target).

Fakes the session + side-panel driver so the multi-turn flow runs with no
Playwright / Chrome. Load-bearing behaviours: ONE warm conversation across
turns (continue_conversation flips after turn 1), inbox-alias normalisation,
reset between nodes, and carrier binding by thread_id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from advtest_ext.adapters.gmail.ui_mode.multi_turn_target import GeminiGmailMultiTurnTarget
from advtest_ext.document.document import AbstractDocument
from advtest_ext.document.target import DocumentCapabilities, Response

_EMPTY = AbstractDocument(id="empty", title="", body_blocks=[])


class _FakeSession:
    def __init__(self) -> None:
        self.page = object()
        self.closed = False

    async def ensure_page(self) -> Any:
        return self.page

    async def close(self) -> None:
        self.closed = True


def _recording_invoke(calls: list[dict[str, Any]]) -> Any:
    async def invoke_fn(**kwargs: Any) -> Response:
        calls.append(kwargs)
        return Response(text=f"reply: {kwargs['user_prompt']}", raw={})

    return invoke_fn


def _target(calls: list[dict[str, Any]], session: _FakeSession, thread_id: str | None = None):
    return GeminiGmailMultiTurnTarget(
        output_dir=Path("/tmp/out"),
        thread_id=thread_id,
        trace_prefix="test",
        session=session,
        invoke_fn=_recording_invoke(calls),
    )


@pytest.mark.asyncio
async def test_one_conversation_continues_after_first_turn() -> None:
    calls: list[dict[str, Any]] = []
    t = _target(calls, _FakeSession(), thread_id="THREAD-1")
    ctx = await t.render_document(_EMPTY)
    for _ in range(3):
        await t.invoke(ctx, "hi")
    assert [c["continue_conversation"] for c in calls] == [False, True, True]
    assert {c["thread_id"] for c in calls} == {"THREAD-1"}
    # render_document is idempotent — same context across turns.
    assert await t.render_document(_EMPTY) is ctx


@pytest.mark.asyncio
async def test_inbox_alias_normalised_to_empty() -> None:
    calls: list[dict[str, Any]] = []
    t = _target(calls, _FakeSession(), thread_id="inbox")
    await t.invoke(await t.render_document(_EMPTY), "summarise my inbox")
    assert calls[0]["thread_id"] == ""  # inbox alias -> empty -> inbox URL


@pytest.mark.asyncio
async def test_reset_starts_fresh_conversation() -> None:
    calls: list[dict[str, Any]] = []
    t = _target(calls, _FakeSession(), thread_id="T")
    await t.invoke(await t.render_document(_EMPTY), "a")
    t.reset()
    await t.invoke(await t.render_document(_EMPTY), "b")
    assert [c["continue_conversation"] for c in calls] == [False, False]


@pytest.mark.asyncio
async def test_close_leaves_injected_session_alone() -> None:
    """An injected session belongs to the caller — close() must not close it
    (only a session the target created itself is torn down)."""
    s = _FakeSession()
    t = _target([], s, thread_id="T")
    await t.close()
    assert s.closed is False


def test_capabilities_multi_turn() -> None:
    assert _target([], _FakeSession()).capabilities() == DocumentCapabilities(multi_turn=True)


def test_bind_carrier_sets_thread() -> None:
    t = _target([], _FakeSession())
    t.bind_carrier(thread_id="BOUND-42")
    assert t._thread_id == "BOUND-42"
    with pytest.raises(ValueError):
        t.bind_carrier(thread_id="")
