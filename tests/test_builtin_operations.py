"""Tests for the Phase-5.1 built-in operation handlers + the
``default_operations`` factory.

These tests don't hit Gmail, Drive, or Playwright — the handlers
that touch live services are tested separately under
``ADVTEST_WORKSPACE_INTEGRATION`` (the same gate the existing
adapter integration tests use). What we test here:

- The factory registers all four V1 operations.
- Each handler validates its required params (clear error
  messages, not stack traces) before reaching any live service.
- The registry's handlers are callable through the standard
  ``OperationHandler`` shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from advtest_ext.adapters.gmail import recipe_ops as gmail_ops
from advtest_ext.adapters.google_docs import recipe_ops as docs_ops
from advtest_ext.core.builtin_operations import default_operations
from advtest_ext.core.recipe import Step
from advtest_ext.core.recipe_operations import (
    OperationContext,
)
from advtest_ext.core.surface import Surface


def _ctx(params: dict[str, Any] | None = None, app: str = "docs") -> OperationContext:
    step = Step(
        id="t",
        intent="test",
        surface=Surface(app=app, region="sidebar", account_role="recipient"),  # type: ignore[arg-type]
        operation="t",
        params=params or {},
    )
    return OperationContext(
        step=step,
        params=params or {},
        session=None,
        account_slot=None,
    )


# ----------------------------------------------------------------------
# default_operations factory
# ----------------------------------------------------------------------


def test_default_operations_registers_v1_chain() -> None:
    reg = default_operations()
    assert reg.has("gmail", "send_email")
    assert reg.has("gmail", "reply")
    assert reg.has("docs", "open_document")
    assert reg.has("docs", "set_document_body")
    assert reg.has("docs", "type_prompt")


def test_default_operations_returns_fresh_registry_each_call() -> None:
    """Two factory calls must yield independent registries — a
    test that mutates one shouldn't leak into another."""
    r1 = default_operations()
    r2 = default_operations()
    assert r1 is not r2
    assert len(r1) == len(r2) == 11


def test_default_operations_registered_pairs_match_expected() -> None:
    reg = default_operations()
    assert sorted(reg.registered_pairs()) == sorted(
        [
            ("gmail", "send_email"),
            ("gmail", "reply"),
            ("docs", "open_document"),
            ("docs", "set_document_body"),
            ("docs", "type_prompt"),
            ("slides", "open_deck"),
            ("slides", "set_speaker_notes"),
            ("slides", "type_prompt"),
            ("calendar", "create_event"),
            ("calendar", "type_prompt"),
            ("outlook", "send_email"),
        ]
    )


# ----------------------------------------------------------------------
# Param-validation: handlers should reject empty / missing required
# params with clean error messages before touching any live service.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_requires_subject() -> None:
    with pytest.raises(ValueError, match="subject"):
        await gmail_ops.send_email(_ctx({"body": "x"}, app="gmail"))


@pytest.mark.asyncio
async def test_send_email_requires_body() -> None:
    with pytest.raises(ValueError, match="body"):
        await gmail_ops.send_email(_ctx({"subject": "x"}, app="gmail"))


@pytest.mark.asyncio
async def test_send_email_rejects_empty_string_subject() -> None:
    with pytest.raises(ValueError, match="subject"):
        await gmail_ops.send_email(_ctx({"subject": "", "body": "x"}, app="gmail"))


@pytest.mark.asyncio
async def test_reply_requires_thread_id() -> None:
    with pytest.raises(ValueError, match="thread_id"):
        await gmail_ops.reply(_ctx({"body": "x"}, app="gmail"))


@pytest.mark.asyncio
async def test_reply_requires_body() -> None:
    with pytest.raises(ValueError, match="body"):
        await gmail_ops.reply(_ctx({"thread_id": "x"}, app="gmail"))


@pytest.mark.asyncio
async def test_type_prompt_requires_document_id() -> None:
    with pytest.raises(ValueError, match="document_id"):
        await docs_ops.type_prompt(_ctx({"prompt": "x"}))


@pytest.mark.asyncio
async def test_type_prompt_requires_prompt() -> None:
    with pytest.raises(ValueError, match="prompt"):
        await docs_ops.type_prompt(_ctx({"document_id": "x"}))


@pytest.mark.asyncio
async def test_set_document_body_requires_document_id() -> None:
    with pytest.raises(ValueError, match="document_id"):
        await docs_ops.set_document_body(_ctx({"body": "x"}))


@pytest.mark.asyncio
async def test_set_document_body_requires_body() -> None:
    with pytest.raises(ValueError, match="body"):
        await docs_ops.set_document_body(_ctx({"document_id": "x"}))


# ----------------------------------------------------------------------
# Handler signature compatibility — confirms each handler matches
# the OperationHandler protocol the runner expects.
# ----------------------------------------------------------------------


def test_handlers_are_async_callables() -> None:
    """The runner awaits handler invocations — handlers must be
    coroutine functions, not sync."""
    import asyncio

    for handler in [
        gmail_ops.send_email,
        gmail_ops.reply,
        docs_ops.open_document,
        docs_ops.set_document_body,
        docs_ops.type_prompt,
    ]:
        assert asyncio.iscoroutinefunction(handler), (
            f"{handler.__name__} must be async — the runner awaits it"
        )


def test_handlers_returnable_via_registry() -> None:
    """A handler pulled out of the default registry is the same
    callable importable directly from its module."""
    reg = default_operations()
    assert reg.get("gmail", "send_email") is gmail_ops.send_email
    assert reg.get("gmail", "reply") is gmail_ops.reply
    assert reg.get("docs", "open_document") is docs_ops.open_document
    assert reg.get("docs", "set_document_body") is docs_ops.set_document_body
    assert reg.get("docs", "type_prompt") is docs_ops.type_prompt
