"""Unit tests for GeminiDocsMultiTurnTarget.

Fakes for the session + side-panel driver + doc lifecycle, so the
multi-turn flow is exercised with no Playwright / Chrome / Workspace
APIs. The load-bearing behaviours: ONE doc + ONE conversation across
turns, reset only on turn 1, teardown respects doc ownership.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from advtest_ext.adapters.google_docs.ui_mode.multi_turn_target import (
    GeminiDocsMultiTurnTarget,
)
from advtest_ext.document.document import AbstractDocument
from advtest_ext.document.target import DocumentCapabilities, Response

_EMPTY_DOC = AbstractDocument(id="empty", title="", body_blocks=[])


class _FakeSession:
    """UiModeSession-shaped double."""

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
    *, calls: list[dict[str, Any]], session: _FakeSession, doc_id: str | None = None
) -> tuple[GeminiDocsMultiTurnTarget, list[str]]:
    created: list[str] = []
    deleted: list[str] = []

    def doc_factory() -> str:
        created.append("DOC-NEW")
        return "DOC-NEW"

    def doc_deleter(document_id: str) -> None:
        deleted.append(document_id)

    target = GeminiDocsMultiTurnTarget(
        output_dir=Path("/tmp/out"),
        doc_id=doc_id,
        trace_prefix="test",
        session=session,
        invoke_fn=_recording_invoke_fn(calls),
        doc_factory=doc_factory,
        doc_deleter=doc_deleter,
    )
    # stash for assertions
    target._test_created = created  # type: ignore[attr-defined]
    target._test_deleted = deleted  # type: ignore[attr-defined]
    return target, deleted


def test_capabilities_declare_multi_turn() -> None:
    target = GeminiDocsMultiTurnTarget(output_dir="/tmp", session=_FakeSession())
    caps = target.capabilities()
    assert isinstance(caps, DocumentCapabilities)
    assert caps.multi_turn is True


@pytest.mark.asyncio
async def test_render_is_idempotent_one_doc_per_run() -> None:
    """Crescendo calls render_document every turn; we must hand back the
    SAME context and create the scratch doc exactly once."""
    calls: list[dict[str, Any]] = []
    session = _FakeSession()
    target, _ = _make_target(calls=calls, session=session)

    ctx1 = await target.render_document(_EMPTY_DOC)
    ctx2 = await target.render_document(_EMPTY_DOC)

    assert ctx1 is ctx2
    assert ctx1.rendered["document_id"] == "DOC-NEW"
    assert target._test_created == ["DOC-NEW"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_first_turn_resets_then_continues() -> None:
    """Turn 1 resets to a fresh conversation; later turns continue it on
    the same page. That reset-once is what lets the persona accumulate."""
    calls: list[dict[str, Any]] = []
    session = _FakeSession()
    target, _ = _make_target(calls=calls, session=session)

    ctx = await target.render_document(_EMPTY_DOC)
    r1 = await target.invoke(ctx, "turn one")
    r2 = await target.invoke(ctx, "turn two")
    r3 = await target.invoke(ctx, "turn three")

    assert [c["reset_conversation"] for c in calls] == [True, False, False]
    # Same warm page reused every turn.
    assert all(c["external_page"] is session.page for c in calls)
    # Distinct, ordered trace ids.
    assert [c["trace_id"] for c in calls] == ["test-c1-t1", "test-c1-t2", "test-c1-t3"]
    # All turns target the one scratch doc.
    assert {c["document_id"] for c in calls} == {"DOC-NEW"}
    # Turn provenance stamped.
    assert [r.raw["turn"] for r in (r1, r2, r3)] == [1, 2, 3]
    assert all(r.raw["is_multi_turn"] for r in (r1, r2, r3))


@pytest.mark.asyncio
async def test_teardown_deletes_owned_doc_and_close_closes_owned_session() -> None:
    calls: list[dict[str, Any]] = []
    session = _FakeSession()
    target, deleted = _make_target(calls=calls, session=session)

    ctx = await target.render_document(_EMPTY_DOC)
    await target.invoke(ctx, "hi")
    await target.teardown(ctx)

    assert deleted == ["DOC-NEW"]
    # Injected session is the caller's — close() must NOT close it.
    await target.close()
    assert session.closed is False


@pytest.mark.asyncio
async def test_supplied_doc_id_is_used_and_never_deleted() -> None:
    calls: list[dict[str, Any]] = []
    session = _FakeSession()
    target, deleted = _make_target(calls=calls, session=session, doc_id="MY-DOC")

    ctx = await target.render_document(_EMPTY_DOC)
    assert ctx.rendered["document_id"] == "MY-DOC"
    assert target._test_created == []  # type: ignore[attr-defined]  # no doc created

    await target.invoke(ctx, "hi")
    await target.teardown(ctx)
    assert deleted == []  # caller's doc is left alone


@pytest.mark.asyncio
async def test_doc_ids_rotate_one_per_node_and_never_deleted() -> None:
    """A doc_ids pool grounds each node on the NEXT existing doc (wrapping
    around), minting and deleting nothing — the docs pre-exist and belong
    to the caller. This is the 'tree nodes over existing payload docs' path."""
    session = _FakeSession()
    target = GeminiDocsMultiTurnTarget(
        output_dir=Path("/tmp/out"),
        session=session,
        invoke_fn=_recording_invoke_fn([]),
        doc_ids=["docA", "docB", "docC"],
    )
    seen: list[str] = []
    for _ in range(5):  # 5 nodes over a 3-doc pool -> wraps to docA, docB
        ctx = await target.render_document(_EMPTY_DOC)
        seen.append(ctx.rendered["document_id"])
        target.reset()
    assert seen == ["docA", "docB", "docC", "docA", "docB"]
    await target.close()
    assert target._created_docs == []  # nothing minted, nothing to delete


@pytest.mark.asyncio
async def test_browser_mint_hosts_once_via_browser_no_api() -> None:
    """browser_mint opens a blank host through the BROWSER (goto /create ->
    redirect to /document/d/<id>), reuses it across nodes, and never touches
    the Docs API or the delete list."""

    class _FakePage:
        def __init__(self) -> None:
            self.url = "https://docs.google.com/document/create"
            self.goto_calls = 0

        async def goto(self, url: str, **_: Any) -> None:
            self.goto_calls += 1
            self.url = "https://docs.google.com/document/d/HOSTDOC1234567890abcd/edit"

    class _Session:
        def __init__(self, page: _FakePage) -> None:
            self._page = page
            self.closed = False

        async def ensure_page(self) -> Any:
            return self._page

        async def close(self) -> None:
            self.closed = True

    page = _FakePage()
    target = GeminiDocsMultiTurnTarget(
        output_dir=Path("/tmp/out"),
        session=_Session(page),
        invoke_fn=_recording_invoke_fn([]),
        browser_mint=True,
    )
    ids: list[str] = []
    for _ in range(3):  # 3 nodes
        ctx = await target.render_document(_EMPTY_DOC)
        ids.append(ctx.rendered["document_id"])
        target.reset()
    assert ids == ["HOSTDOC1234567890abcd"] * 3  # minted once, reused as host
    assert page.goto_calls == 1  # browser-minted exactly once
    assert target._created_docs == []  # Docs API never used, nothing to delete


@pytest.mark.asyncio
async def test_reset_mints_fresh_doc_per_node_and_close_deletes_all() -> None:
    """Owned docs: reset() drops the cached doc so each node gets a fresh
    blank one; close() deletes every doc minted across nodes."""
    session = _FakeSession()
    counter = {"n": 0}
    deleted: list[str] = []

    calls: list[dict[str, Any]] = []

    def doc_factory() -> str:
        counter["n"] += 1
        return f"DOC-{counter['n']}"

    target = GeminiDocsMultiTurnTarget(
        output_dir=Path("/tmp"),
        trace_prefix="test",
        session=session,
        invoke_fn=_recording_invoke_fn(calls),
        doc_factory=doc_factory,
        doc_deleter=deleted.append,
    )
    ctx1 = await target.render_document(_EMPTY_DOC)
    await target.invoke(ctx1, "node1 turn1")
    target.reset()  # new node → should mint a fresh doc
    ctx2 = await target.render_document(_EMPTY_DOC)
    assert ctx1.rendered["document_id"] == "DOC-1"
    assert ctx2.rendered["document_id"] == "DOC-2"  # fresh doc, not reused
    # First invoke of the new node resets the conversation again.
    r = await target.invoke(ctx2, "node2 turn1")
    assert r.raw["turn"] == 1
    assert [c["trace_id"] for c in calls] == ["test-c1-t1", "test-c2-t1"]
    await target.close()
    assert sorted(deleted) == ["DOC-1", "DOC-2"]  # both cleaned up


@pytest.mark.asyncio
async def test_reset_reuses_supplied_doc_id() -> None:
    """A caller-supplied doc_id can't be re-minted, so reset() keeps it."""
    session = _FakeSession()
    deleted: list[str] = []
    target = GeminiDocsMultiTurnTarget(
        output_dir=Path("/tmp"),
        doc_id="FIXED-DOC",
        session=session,
        invoke_fn=_recording_invoke_fn([]),
        doc_deleter=deleted.append,
    )
    ctx1 = await target.render_document(_EMPTY_DOC)
    target.reset()
    ctx2 = await target.render_document(_EMPTY_DOC)
    assert ctx1.rendered["document_id"] == "FIXED-DOC"
    assert ctx2.rendered["document_id"] == "FIXED-DOC"  # reused
    await target.close()
    assert deleted == []  # caller's doc never deleted


@pytest.mark.asyncio
async def test_owned_session_is_closed() -> None:
    """When the target creates its own session it must close it."""
    fake = _FakeSession()
    target = GeminiDocsMultiTurnTarget(output_dir="/tmp")
    # Simulate setup() having created a session we own.
    target._session = fake
    target._owns_session = True
    await target.close()
    assert fake.closed is True
