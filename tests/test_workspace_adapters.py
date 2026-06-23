"""Tests for the three Workspace adapters: ``GoogleDocsTarget``,
``GmailTarget``, ``SheetsTarget``.

Two layers:

* Unit tests (always-on) — every adapter is exercised end-to-end
  with mocked Workspace API services + a mocked Gemini ``Provider``.
  No real network, no real auth. These run in the default pytest
  invocation.
* Integration scaffolds (gated) — skipped unless
  ``ADVTEST_WORKSPACE_INTEGRATION=1`` is set. The scaffolds round-
  trip through real Docs / Gmail / Sheets + Drive APIs (auth via
  ``advtest_ext.adapters._auth.get_service``) and the configured
  Gemini provider, then teardown. Designed for nightly CI with
  credentials wired.

The unit tests patch ``advtest_ext.adapters._auth.get_service`` to
return a stub Workspace client and a fake Gemini provider that
returns a canned ``ChatResponse``. They assert:
  - the right Workspace API methods got called with the right
    arguments,
  - the rendered ``NativeContext`` records the artefact id +
    applied techniques,
  - ``invoke`` linearises and forwards to the Gemini provider,
  - ``teardown`` calls the right delete / trash endpoint.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from advtest_ext.adapters._auth import (
    DOCS_SCOPES,
    GMAIL_SCOPES,
    SHEETS_SCOPES,
)
from advtest_ext.adapters.gmail.target import GmailTarget
from advtest_ext.adapters.google_docs.target import (
    SUPPORTED_TECHNIQUES as DOCS_SUPPORTED,
)
from advtest_ext.adapters.google_docs.target import (
    GoogleDocsTarget,
)
from advtest_ext.adapters.sheets.target import (
    SheetsTarget,
)
from advtest_ext.document import AbstractDocument
from advtest_ext.document.blocks import (
    Formatting,
    HeadingBlock,
    HiddenContent,
    TableBlock,
    TableCell,
    TextBlock,
)
from advtest_ext.document.elements import Comment, Link, Reply
from advtest_ext.providers.base import (
    ChatMessage,
    ChatResponse,
    Provider,
    ProviderConfig,
    Usage,
)

# ----------------------------------------------------------------------
# Shared doubles
# ----------------------------------------------------------------------


class _ScriptedGemini(Provider):
    """Provider that returns a fixed ``ChatResponse``. Captures the
    forwarded messages so tests can assert what the adapter sent."""

    def __init__(
        self,
        *,
        text: str = "scripted summary",
        calls: list[Sequence[ChatMessage]] | None = None,
    ) -> None:
        super().__init__(ProviderConfig(provider_type="gemini", model_id="scripted"))
        self._text = text
        self.calls: list[Sequence[ChatMessage]] = calls if calls is not None else []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> ChatResponse:
        del tools, overrides
        self.calls.append(list(messages))
        return ChatResponse(
            text=self._text,
            usage=Usage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            provider_type="gemini",
            model_id="scripted",
            raw={},
        )


def _doc_with_basic_blocks() -> AbstractDocument:
    """Canonical test document — exercises every Block type the
    adapters need to handle, plus a hidden-content marker so the
    technique-collection path runs."""
    return AbstractDocument(
        id="t-doc",
        title="Quarterly summary",
        body_blocks=[
            HeadingBlock(text="Q3 review", level=1),
            TextBlock(text="Revenue grew 12%."),
            TextBlock(
                text="white-text payload",
                formatting=Formatting(color="#FFFFFF"),
                hidden=HiddenContent(technique_id="white_text", payload_id="p-1"),
            ),
            TableBlock(
                rows=[
                    [TableCell(text="region"), TableCell(text="value")],
                    [TableCell(text="EMEA"), TableCell(text="42")],
                ]
            ),
        ],
        comments=[
            Comment(
                author="alice",
                anchor="block-1",
                body="please check the totals",
                replies=[Reply(author="bob", body="will do")],
            ),
        ],
        attached_links=[
            Link(url="https://example.org/x", anchor_text="src"),
        ],
        metadata={"reviewer": "alice"},
    )


# ======================================================================
# GoogleDocsTarget
# ======================================================================


class _GoogleApiStub:
    """Mock ``googleapiclient.discovery.Resource`` chain for the
    methods the Docs / Drive / Sheets adapters touch.

    Real Resource objects accept arbitrary kwargs per method (the
    schema is generated at runtime from the discovery doc), so a
    typed stub with rigid signatures would fail on legitimate calls
    like ``drive.comments().create(fileId=…, fields=…, body=…)`` vs.
    ``docs.documents().create(body=…)``.

    This stub is method-name-keyed, accepts any args, and lets
    tests pre-load return values per (method, sequence) — enough
    fidelity for adapter-side branch coverage without modelling the
    full API."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._returns: dict[str, list[Any]] = {}
        self._defaults: dict[str, Any] = {
            "create_doc": {"documentId": "doc-id-1"},
            "create_sheet": {
                "spreadsheetId": "ss-1",
                "sheets": [{"properties": {"sheetId": 0}}],
            },
            "send": {"id": "msg-1", "threadId": "thread-1"},
            "comment_create": {"id": "c-1"},
            "export": b"--- exported text ---",
        }
        self._pending: Any = None

    def queue(self, key: str, value: Any) -> None:
        """Push a return value the next call with ``key`` will see."""
        self._returns.setdefault(key, []).append(value)

    def _record(self, method: str, *args: Any, **kwargs: Any) -> _GoogleApiStub:
        self.calls.append({"method": method, "args": args, "kwargs": kwargs})
        self._pending = self._next_return(method)
        return self

    def _next_return(self, method: str) -> Any:
        queued = self._returns.get(method)
        if queued:
            return queued.pop(0)
        return self._defaults.get(method, {})

    # ---- Resource-chain accessors all return self -------------------

    def documents(self) -> _GoogleApiStub:
        return self

    def files(self) -> _GoogleApiStub:
        return self

    def comments(self) -> _GoogleApiStub:
        return self

    def replies(self) -> _GoogleApiStub:
        return self

    def users(self) -> _GoogleApiStub:
        return self

    def messages(self) -> _GoogleApiStub:
        return self

    def threads(self) -> _GoogleApiStub:
        return self

    def spreadsheets(self) -> _GoogleApiStub:
        return self

    def values(self) -> _GoogleApiStub:
        return self

    # ---- Method calls — branch by signature so the test fixture's
    # default returns dispatch correctly ------------------------------

    def create(self, *args: Any, **kwargs: Any) -> _GoogleApiStub:
        # Disambiguate Docs vs Sheets vs Drive comment creation by
        # the kwargs Google APIs use. ``fileId`` ⇒ Drive comment;
        # ``spreadsheets`` body ⇒ Sheets create; otherwise a
        # documents().create.
        if "fileId" in kwargs:
            key = "comment_create"
        elif (
            "body" in kwargs and isinstance(kwargs["body"], dict) and "properties" in kwargs["body"]
        ):
            key = "create_sheet"
        else:
            key = "create_doc"
        return self._record(key, *args, **kwargs)

    def batchUpdate(self, *args: Any, **kwargs: Any) -> _GoogleApiStub:  # noqa: N802
        return self._record("batchUpdate", *args, **kwargs)

    def update(self, *args: Any, **kwargs: Any) -> _GoogleApiStub:
        return self._record("update", *args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> _GoogleApiStub:
        return self._record("delete", *args, **kwargs)

    def export(self, *args: Any, **kwargs: Any) -> _GoogleApiStub:
        return self._record("export", *args, **kwargs)

    def send(self, *args: Any, **kwargs: Any) -> _GoogleApiStub:
        # Threaded sends carry ``body.threadId`` so the second send
        # for a doc's reply lands on the same thread.
        body = kwargs.get("body", {})
        thread_id = body.get("threadId") or "thread-1"
        msg_id = f"msg-{len(self.calls) + 1}"
        self.queue("send", {"id": msg_id, "threadId": thread_id})
        return self._record("send", *args, **kwargs)

    def getProfile(self, *args: Any, **kwargs: Any) -> _GoogleApiStub:  # noqa: N802
        # Gmail ``users().getProfile`` returns the authenticated user's
        # profile; the adapter reads ``emailAddress`` to translate the
        # ``me`` recipient alias into a real RFC822 ``To:`` address.
        self.queue("getProfile", {"emailAddress": "test@example.com"})
        return self._record("getProfile", *args, **kwargs)

    def get(self, *args: Any, **kwargs: Any) -> _GoogleApiStub:
        # The Sheets ``values.get`` and the Gmail ``threads.get``
        # have different shapes; queue accordingly per call site.
        if "spreadsheetId" in kwargs:
            self.queue(
                "get",
                {"values": [["Q3 review"], ["Revenue grew 12%."]]},
            )
        else:
            import base64

            plain = base64.urlsafe_b64encode(b"linearised body text").decode("ascii")
            self.queue(
                "get",
                {"messages": [{"payload": {"body": {"data": plain}}}]},
            )
        return self._record("get", *args, **kwargs)

    def trash(self, *args: Any, **kwargs: Any) -> _GoogleApiStub:
        return self._record("trash", *args, **kwargs)

    def execute(self) -> Any:
        return self._pending

    # ---- Helpers for assertions -------------------------------------

    def calls_for(self, method: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method]


# Back-compat aliases so existing test references resolve. The Docs
# adapter exercises both Docs and Drive calls through one stub
# instance; same for Sheets + Drive (we keep them separate so tests
# can assert the cross-API split when it matters).
_DocsServiceStub = _GoogleApiStub
_GmailStub = _GoogleApiStub
_SheetsStub = _GoogleApiStub
_DriveStub = _GoogleApiStub


def _patch_get_service(
    monkeypatch: pytest.MonkeyPatch, stubs: dict[str, Any]
) -> dict[str, list[Any]]:
    """Patch every adapter's ``get_service`` symbol to dispatch by
    api name. Returns a ``calls`` map test code can assert against
    to verify the right APIs were touched."""
    calls: dict[str, list[Any]] = {}

    def fake(api_name: str, api_version: str, scopes: Sequence[str]) -> Any:
        calls.setdefault(api_name, []).append({"version": api_version, "scopes": tuple(scopes)})
        return stubs[api_name]

    for module in (
        "advtest_ext.adapters.google_docs.target",
        "advtest_ext.adapters.gmail.target",
        "advtest_ext.adapters.sheets.target",
    ):
        monkeypatch.setattr(f"{module}.get_service", fake)
    return calls


# ----------------------------------------------------------------------
# Docs unit tests
# ----------------------------------------------------------------------


async def test_docs_render_creates_doc_and_runs_batch_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``render_document`` must create a Doc, batch-update its body,
    apply Drive metadata + comments, and record the doc id +
    applied techniques onto the ``NativeContext``."""
    docs_stub = _GoogleApiStub()
    drive_stub = _GoogleApiStub()
    api_calls = _patch_get_service(monkeypatch, {"docs": docs_stub, "drive": drive_stub})
    target = GoogleDocsTarget(gemini_provider=_ScriptedGemini())
    await target.setup()
    ctx = await target.render_document(_doc_with_basic_blocks())

    # The Docs API was hit for create + batchUpdate.
    assert "docs" in api_calls
    create_doc_calls = docs_stub.calls_for("create_doc")
    assert create_doc_calls
    assert create_doc_calls[0]["kwargs"]["body"]["title"] == "Quarterly summary"
    batch_calls = docs_stub.calls_for("batchUpdate")
    assert batch_calls, "expected at least one batchUpdate"
    requests = batch_calls[0]["kwargs"]["body"]["requests"]
    insert_text_blobs = [r["insertText"]["text"] for r in requests if "insertText" in r]
    assert any("Q3 review" in t for t in insert_text_blobs)

    # Drive scope was the requested DOCS_SCOPES (drive.file).
    drive_call = api_calls["drive"][0]
    assert drive_call["scopes"] == tuple(DOCS_SCOPES)

    # Native context records the doc id + applied techniques.
    rendered = ctx.rendered
    assert isinstance(rendered, dict)
    assert rendered["document_id"] == "doc-id-1"
    assert "white_text" in rendered["applied_techniques"]


async def test_docs_invoke_exports_and_forwards_to_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``invoke`` must export the doc as plain text via Drive and
    forward the export to the Gemini provider with the user prompt."""
    docs_stub = _GoogleApiStub()
    drive_stub = _GoogleApiStub()
    _patch_get_service(monkeypatch, {"docs": docs_stub, "drive": drive_stub})
    provider = _ScriptedGemini(text="summary OK")
    target = GoogleDocsTarget(gemini_provider=provider)
    await target.setup()
    ctx = await target.render_document(_doc_with_basic_blocks())

    resp = await target.invoke(ctx, "summarise this doc please")

    assert resp.text == "summary OK"
    export_calls = drive_stub.calls_for("export")
    assert export_calls, "expected a Drive export call"
    assert export_calls[0]["kwargs"]["mimeType"] == "text/plain"
    # Provider got one call; the prompt embeds the exported text.
    assert len(provider.calls) == 1
    sent = provider.calls[0][0].content
    assert "summarise this doc please" in sent
    assert "--- exported text ---" in sent
    # Raw response carries auditable metadata.
    assert resp.raw["document_id"] == "doc-id-1"
    assert resp.raw["model_id"] == "scripted"


async def test_docs_teardown_deletes_doc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs_stub = _GoogleApiStub()
    drive_stub = _GoogleApiStub()
    _patch_get_service(monkeypatch, {"docs": docs_stub, "drive": drive_stub})
    target = GoogleDocsTarget(gemini_provider=_ScriptedGemini())
    await target.setup()
    ctx = await target.render_document(_doc_with_basic_blocks())
    await target.teardown(ctx)
    delete_calls = drive_stub.calls_for("delete")
    assert any(c["kwargs"].get("fileId") == "doc-id-1" for c in delete_calls), (
        "expected files().delete to be called with the doc id"
    )


async def test_docs_render_filters_oversized_appproperty_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive caps each appProperty at 124 bytes (key + value, UTF-8).
    The ``document_metadata`` adversarial technique routinely exceeds
    this; render must drop the oversized entry rather than crash with
    ``403 propertyLengthLimitExceeded``. Locks the regression we hit
    on the first live ``run_workspace_docs.yaml`` invocation."""
    docs_stub = _GoogleApiStub()
    drive_stub = _GoogleApiStub()
    _patch_get_service(monkeypatch, {"docs": docs_stub, "drive": drive_stub})

    from advtest_ext.document import AbstractDocument
    from advtest_ext.document.blocks import TextBlock

    long_payload = "x" * 200  # Way over the 124-byte cap.
    doc = AbstractDocument(
        id="oversized-doc",
        title="t",
        body_blocks=[TextBlock(text="hello")],
        metadata={
            "small_key": "ok",  # under the limit
            "author_notes": long_payload,  # over the limit
        },
    )
    target = GoogleDocsTarget(gemini_provider=_ScriptedGemini())
    await target.setup()
    await target.render_document(doc)

    update_calls = drive_stub.calls_for("update")
    assert len(update_calls) == 1, "expected exactly one Drive files().update"
    payload = update_calls[0]["kwargs"]["body"]["appProperties"]
    assert "small_key" in payload
    assert "author_notes" not in payload, (
        "oversized appProperty must be filtered out before the API call"
    )


async def test_docs_render_isolates_failed_inline_image_inserts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``insertInlineImage`` fails (the URL isn't fetchable from
    Google's side — very common with adversarial placeholder URLs),
    the failure must not abort the whole render. Image inserts go in
    their own per-request batches; failures count toward
    ``rendered['images_skipped']`` so the dashboard can plot
    "image-delivery refusals" as a per-technique signal."""
    from googleapiclient.errors import HttpError

    docs_stub = _GoogleApiStub()
    drive_stub = _GoogleApiStub()
    _patch_get_service(monkeypatch, {"docs": docs_stub, "drive": drive_stub})

    # Make every image-only batch raise. The text batch (which has
    # no insertInlineImage) succeeds; only the per-image batches we
    # split out should hit this branch.
    original_batch_update = docs_stub.batchUpdate

    def selective_batch(*args: Any, **kwargs: Any) -> _GoogleApiStub:
        body = kwargs.get("body", {})
        requests = body.get("requests", [])
        if any("insertInlineImage" in r for r in requests):
            # Mimic Docs' "Access to the provided image was forbidden"
            # 400. We don't construct a real ``HttpError`` — its
            # constructor needs an httplib2 response. A subclass with
            # the same name is enough to be caught by the adapter's
            # ``except HttpError`` clause.
            raise HttpError(
                resp=MagicMock(status=400, reason="Bad Request"),
                content=b'{"error": {"message": "image fetch failed"}}',
                uri="https://docs.googleapis.com/v1/documents/x:batchUpdate",
            )
        return original_batch_update(*args, **kwargs)

    docs_stub.batchUpdate = selective_batch  # type: ignore[method-assign]

    from advtest_ext.document import AbstractDocument
    from advtest_ext.document.blocks import (
        HiddenContent,
        ImageBlock,
        TextBlock,
    )

    doc = AbstractDocument(
        id="image-fail-doc",
        title="t",
        body_blocks=[
            TextBlock(text="visible body"),
            ImageBlock(
                src="https://example-org.invalid/q2-chart.png",
                alt_text="HIDDEN PAYLOAD via alt text",
                hidden=HiddenContent(technique_id="image_alt_text", payload_id="p-img-1"),
            ),
            ImageBlock(
                src="https://example-org.invalid/q3-chart.png",
                alt_text="another payload",
            ),
        ],
    )
    target = GoogleDocsTarget(gemini_provider=_ScriptedGemini())
    await target.setup()
    ctx = await target.render_document(doc)

    # Render completed without raising — image failures isolated.
    rendered = ctx.rendered
    assert isinstance(rendered, dict)
    assert rendered["document_id"] == "doc-id-1"
    assert rendered["images_skipped"] == 2
    # The technique is still recorded in applied_techniques (it was
    # *attempted*); the orchestrator joins on this for ASR plotting.
    assert "image_alt_text" in rendered["applied_techniques"]
    # The recorded text batch contains zero ``insertInlineImage``
    # requests — text/style writes weren't dragged down with the
    # image inserts.
    text_batches = docs_stub.calls_for("batchUpdate")
    assert text_batches  # at least the text batch landed
    for call in text_batches:
        for req in call["kwargs"]["body"]["requests"]:
            assert "insertInlineImage" not in req


async def test_docs_render_skips_appproperty_update_when_all_oversized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every metadata entry is oversized, skip ``files().update``
    entirely — empty ``appProperties`` is a no-op write but still
    burns a Drive quota request, and Drive may reject the empty
    payload as "no changes" anyway."""
    docs_stub = _GoogleApiStub()
    drive_stub = _GoogleApiStub()
    _patch_get_service(monkeypatch, {"docs": docs_stub, "drive": drive_stub})

    from advtest_ext.document import AbstractDocument
    from advtest_ext.document.blocks import TextBlock

    doc = AbstractDocument(
        id="all-oversized",
        title="t",
        body_blocks=[TextBlock(text="hello")],
        metadata={"big_key": "y" * 300},
    )
    target = GoogleDocsTarget(gemini_provider=_ScriptedGemini())
    await target.setup()
    await target.render_document(doc)

    assert not drive_stub.calls_for("update"), (
        "Drive update must be skipped when every metadata entry is oversized"
    )


def test_docs_supported_techniques_lists_known_set() -> None:
    """Lock the supported set so a future technique addition that
    drops one shows up as a test failure rather than a silent
    coverage regression."""
    target = GoogleDocsTarget(gemini_provider=_ScriptedGemini())
    assert target.supported_techniques() == set(DOCS_SUPPORTED)
    # Things explicitly NOT supported per TECHNIQUE_COVERAGE.md.
    assert "suggested_edit" not in target.supported_techniques()
    assert "text_box_offscreen" not in target.supported_techniques()


async def test_docs_ui_mode_invoke_delegates_to_ui_mode_invoke(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``ui_mode`` delegates to
    ``advtest_ext.adapters.google_docs.ui_mode.ui_mode_invoke``,
    threading the document_id from the rendered context plus the
    target's output_dir. We patch the invoker to avoid touching
    Playwright (full Playwright coverage lives in test_ui_mode.py)."""
    from advtest_ext.adapters.google_docs import ui_mode as ui_mode_pkg
    from advtest_ext.document.target import Response as _Response

    _patch_get_service(monkeypatch, {"docs": _GoogleApiStub(), "drive": _GoogleApiStub()})

    calls: list[dict[str, object]] = []

    async def _fake_invoke(**kwargs: object) -> _Response:
        calls.append(kwargs)
        return _Response(
            text="side-panel reply",
            raw={"screenshot_path": "/tmp/x.png", "ui_mode": True},
        )

    # Patch where the function is *imported*, not where it's defined —
    # GoogleDocsTarget imports from the package root.
    monkeypatch.setattr(ui_mode_pkg, "ui_mode_invoke", _fake_invoke)

    # Stub out UiModeSession so the real path doesn't try to launch
    # Playwright/Chrome — the test only exercises the delegation
    # contract. Real Playwright coverage lives in test_ui_mode.py.
    class _FakeSession:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def ensure_open(self) -> object:
            return object()

        async def ensure_page(self) -> object:
            return object()

        async def close(self) -> None:
            return None

    # target.py imports UiModeSession from the package root, so patch
    # there (not on the invoker submodule).
    monkeypatch.setattr(ui_mode_pkg, "UiModeSession", _FakeSession)

    target = GoogleDocsTarget(
        gemini_provider=_ScriptedGemini(),
        mode="ui_mode",
        ui_output_dir=tmp_path,
    )
    ctx = await target.render_document(AbstractDocument(id="x", title="t"))
    resp = await target.invoke(ctx, "type this")

    assert resp.text == "side-panel reply"
    assert resp.raw["screenshot_path"] == "/tmp/x.png"
    # ``applied_techniques`` decoration merged on from the rendered
    # context (empty for the trivial doc above, but the key is
    # present — orchestrator-side code expects the same shape as
    # api_mode).
    assert "applied_techniques" in resp.raw

    [call_kwargs] = calls
    assert call_kwargs["user_prompt"] == "type this"
    assert call_kwargs["output_dir"] == tmp_path
    # document_id comes from the rendered context — the stub's
    # documents().create call assigns one synthetically.
    assert isinstance(call_kwargs["document_id"], str) and call_kwargs["document_id"]


# ======================================================================
# GmailTarget
# ======================================================================


async def test_gmail_render_sends_message_and_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gmail_stub = _GoogleApiStub()
    api_calls = _patch_get_service(monkeypatch, {"gmail": gmail_stub})

    target = GmailTarget(gemini_provider=_ScriptedGemini())
    await target.setup()
    ctx = await target.render_document(_doc_with_basic_blocks())

    # The first send is the original message; the doc has one comment
    # so a second send (reply on the same thread) follows.
    send_calls = gmail_stub.calls_for("send")
    assert len(send_calls) == 2
    second_body = send_calls[1]["kwargs"]["body"]
    assert second_body.get("threadId") == "thread-1"
    # GMAIL_SCOPES were requested.
    assert api_calls["gmail"][0]["scopes"] == tuple(GMAIL_SCOPES)
    # Native context records the thread id.
    rendered = ctx.rendered
    assert isinstance(rendered, dict)
    assert rendered["thread_id"] == "thread-1"
    assert "white_text" in rendered["applied_techniques"]


async def test_gmail_invoke_linearises_thread_for_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gmail_stub = _GoogleApiStub()
    _patch_get_service(monkeypatch, {"gmail": gmail_stub})
    provider = _ScriptedGemini(text="thread summary")
    target = GmailTarget(gemini_provider=provider)
    await target.setup()
    ctx = await target.render_document(_doc_with_basic_blocks())

    resp = await target.invoke(ctx, "summarise this thread")

    assert resp.text == "thread summary"
    assert gmail_stub.calls_for("get"), "expected threads().get to be called"
    sent = provider.calls[0][0].content
    assert "summarise this thread" in sent
    assert "linearised body text" in sent
    assert resp.raw["thread_id"] == "thread-1"


async def test_gmail_teardown_trashes_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gmail_stub = _GoogleApiStub()
    _patch_get_service(monkeypatch, {"gmail": gmail_stub})
    target = GmailTarget(gemini_provider=_ScriptedGemini())
    await target.setup()
    ctx = await target.render_document(AbstractDocument(id="x", title="t"))
    await target.teardown(ctx)
    trash_calls = gmail_stub.calls_for("trash")
    assert trash_calls
    assert trash_calls[0]["kwargs"]["id"] == "thread-1"


def test_gmail_supported_techniques_excludes_doc_only_set() -> None:
    target = GmailTarget(gemini_provider=_ScriptedGemini())
    supported = target.supported_techniques()
    # Email surface can't carry these.
    for missing in (
        "suggested_edit",
        "text_box_offscreen",
        "footnote",
        "header_footer",
        "table_of_contents",
        "citation_block",
        "document_metadata",
    ):
        assert missing not in supported, f"Gmail must not advertise {missing} support"


# ======================================================================
# SheetsTarget
# ======================================================================


async def test_sheets_render_creates_spreadsheet_writes_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sheets_stub = _GoogleApiStub()
    drive_stub = _GoogleApiStub()
    api_calls = _patch_get_service(monkeypatch, {"sheets": sheets_stub, "drive": drive_stub})
    target = SheetsTarget(gemini_provider=_ScriptedGemini())
    await target.setup()
    ctx = await target.render_document(_doc_with_basic_blocks())

    create_sheet_calls = sheets_stub.calls_for("create_sheet")
    assert create_sheet_calls
    assert create_sheet_calls[0]["kwargs"]["body"]["properties"]["title"] == "Quarterly summary"
    update_calls = sheets_stub.calls_for("update")
    assert update_calls
    written_rows = update_calls[0]["kwargs"]["body"]["values"]
    flat = [c for row in written_rows for c in row]
    assert "Q3 review" in flat
    assert any("HYPERLINK" in str(c) for c in flat)
    # Sheets scope used.
    assert api_calls["sheets"][0]["scopes"] == tuple(SHEETS_SCOPES)
    # Comments routed through Drive.
    assert drive_stub.calls_for("comment_create")
    rendered = ctx.rendered
    assert isinstance(rendered, dict)
    assert rendered["spreadsheet_id"] == "ss-1"
    assert "white_text" in rendered["applied_techniques"]


async def test_sheets_invoke_reads_values_and_forwards_to_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sheets_stub = _GoogleApiStub()
    drive_stub = _GoogleApiStub()
    _patch_get_service(monkeypatch, {"sheets": sheets_stub, "drive": drive_stub})
    provider = _ScriptedGemini(text="sheet summary")
    target = SheetsTarget(gemini_provider=provider)
    await target.setup()
    ctx = await target.render_document(_doc_with_basic_blocks())
    resp = await target.invoke(ctx, "summarise this sheet")

    assert resp.text == "sheet summary"
    assert sheets_stub.calls_for("get"), "expected values().get to be called"
    sent = provider.calls[0][0].content
    assert "summarise this sheet" in sent
    assert "Q3 review" in sent
    assert resp.raw["spreadsheet_id"] == "ss-1"


async def test_sheets_teardown_deletes_via_drive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sheets_stub = _GoogleApiStub()
    drive_stub = _GoogleApiStub()
    _patch_get_service(monkeypatch, {"sheets": sheets_stub, "drive": drive_stub})
    target = SheetsTarget(gemini_provider=_ScriptedGemini())
    await target.setup()
    ctx = await target.render_document(_doc_with_basic_blocks())
    await target.teardown(ctx)
    delete_calls = drive_stub.calls_for("delete")
    assert any(c["kwargs"].get("fileId") == "ss-1" for c in delete_calls)


def test_sheets_supported_techniques_excludes_flowing_text_set() -> None:
    target = SheetsTarget(gemini_provider=_ScriptedGemini())
    supported = target.supported_techniques()
    for missing in (
        "footnote",
        "header_footer",
        "table_of_contents",
        "citation_block",
        "suggested_edit",
        "cross_document_chain",
        "text_box_offscreen",
    ):
        assert missing not in supported


# ======================================================================
# Defensive: hex parser handles malformed inputs
# ======================================================================


def test_docs_hex_parser_returns_none_for_malformed() -> None:
    assert GoogleDocsTarget._hex_to_rgb("not-a-hex") is None
    assert GoogleDocsTarget._hex_to_rgb("#GGGGGG") is None


# ======================================================================
# Integration scaffolds — gated behind ADVTEST_WORKSPACE_INTEGRATION=1
# ======================================================================


_INTEGRATION_REASON = (
    "set ADVTEST_WORKSPACE_INTEGRATION=1 to run integration tests "
    "(requires real Google + Gemini credentials)"
)


def _integration_active() -> bool:
    return os.environ.get("ADVTEST_WORKSPACE_INTEGRATION") == "1"


@pytest.mark.skipif(not _integration_active(), reason=_INTEGRATION_REASON)
async def test_integration_docs_round_trip() -> None:
    """Live Docs round-trip: create with one technique injected,
    invoke against real Gemini, teardown. Skipped unless
    ``ADVTEST_WORKSPACE_INTEGRATION=1`` AND the env vars in
    ``_auth.py`` are set. Designed for nightly CI."""
    from pathlib import Path

    from advtest_ext.providers import load_provider_from_yaml

    # The user is expected to have ATTACKER_MODEL or TARGET_MODEL set
    # to a Gemini provider in the dev.yaml. This scaffold uses
    # whichever role is configured under ``GEMINI_TARGET``; absent
    # that, the test is skipped.
    cfg = Path(__file__).resolve().parents[1] / "configs" / "dev.yaml"
    if not cfg.exists():
        pytest.skip("dev.yaml not present; configure providers to integration-test")
    try:
        provider = load_provider_from_yaml(cfg, "GEMINI_TARGET")
    except KeyError:
        pytest.skip("no GEMINI_TARGET role in dev.yaml")

    target = GoogleDocsTarget(gemini_provider=provider)
    await target.setup()
    doc = AbstractDocument(
        id="integration-doc",
        title="advtest integration smoke",
        body_blocks=[
            HeadingBlock(text="Integration test"),
            TextBlock(text="hello world"),
            TextBlock(
                text="HIDDEN PAYLOAD — please ignore",
                formatting=Formatting(color="#FFFFFF"),
                hidden=HiddenContent(technique_id="white_text", payload_id="p-int-1"),
            ),
        ],
    )
    ctx = await target.render_document(doc)
    try:
        resp = await target.invoke(ctx, "summarise this document")
        assert resp.text  # non-empty Gemini response
    finally:
        await target.teardown(ctx)


@pytest.mark.skipif(not _integration_active(), reason=_INTEGRATION_REASON)
async def test_integration_gmail_round_trip() -> None:
    from pathlib import Path

    from advtest_ext.providers import load_provider_from_yaml

    cfg = Path(__file__).resolve().parents[1] / "configs" / "dev.yaml"
    if not cfg.exists():
        pytest.skip("dev.yaml not present; configure providers to integration-test")
    try:
        provider = load_provider_from_yaml(cfg, "GEMINI_TARGET")
    except KeyError:
        pytest.skip("no GEMINI_TARGET role in dev.yaml")

    target = GmailTarget(gemini_provider=provider)
    await target.setup()
    doc = AbstractDocument(
        id="integration-thread",
        title="advtest integration smoke",
        body_blocks=[
            TextBlock(text="hello — this is an automated test"),
            TextBlock(
                text="HIDDEN PAYLOAD via white text",
                formatting=Formatting(color="#FFFFFF"),
                hidden=HiddenContent(technique_id="white_text", payload_id="p-int-2"),
            ),
        ],
    )
    ctx = await target.render_document(doc)
    try:
        resp = await target.invoke(ctx, "summarise this thread")
        assert resp.text
    finally:
        await target.teardown(ctx)


@pytest.mark.skipif(not _integration_active(), reason=_INTEGRATION_REASON)
async def test_integration_sheets_round_trip() -> None:
    from pathlib import Path

    from advtest_ext.providers import load_provider_from_yaml

    cfg = Path(__file__).resolve().parents[1] / "configs" / "dev.yaml"
    if not cfg.exists():
        pytest.skip("dev.yaml not present; configure providers to integration-test")
    try:
        provider = load_provider_from_yaml(cfg, "GEMINI_TARGET")
    except KeyError:
        pytest.skip("no GEMINI_TARGET role in dev.yaml")

    target = SheetsTarget(gemini_provider=provider)
    await target.setup()
    doc = AbstractDocument(
        id="integration-sheet",
        title="advtest integration smoke",
        body_blocks=[
            HeadingBlock(text="Integration test"),
            TableBlock(
                rows=[
                    [TableCell(text="region"), TableCell(text="value")],
                    [TableCell(text="EMEA"), TableCell(text="42")],
                ]
            ),
            TextBlock(
                text="HIDDEN PAYLOAD",
                formatting=Formatting(color="#FFFFFF"),
                hidden=HiddenContent(technique_id="white_text", payload_id="p-int-3"),
            ),
        ],
    )
    ctx = await target.render_document(doc)
    try:
        resp = await target.invoke(ctx, "summarise this sheet")
        assert resp.text
    finally:
        await target.teardown(ctx)
