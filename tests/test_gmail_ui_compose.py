"""Unit tests for the Gmail UI compose invoker.

Same fake-Playwright pattern as ``test_outlook_ui_mode.py``: inject a
scripted Page double via ``page_factory`` and assert on the recorded
call sequence. No Playwright install required.
"""

from __future__ import annotations

from typing import Any

import pytest

from advtest_ext.adapters.gmail.ui_mode import (
    GmailComposeUnavailable,
    gmail_ui_send_email,
)
from advtest_ext.adapters.gmail.ui_mode.compose_selectors import (
    COMPOSE_SELECTORS,
    EXPECTED_KEYS,
)

# ----------------------------------------------------------------------
# Selector registry — shape contract
# ----------------------------------------------------------------------


def test_selectors_have_expected_keys() -> None:
    """COMPOSE_SELECTORS must carry exactly the keys EXPECTED_KEYS
    lists. Catches typos at module load, not at run time."""
    assert set(COMPOSE_SELECTORS) == EXPECTED_KEYS


def test_selectors_values_are_non_empty_strings() -> None:
    for key, value in COMPOSE_SELECTORS.items():
        assert isinstance(value, str), f"{key!r} is not a string"
        assert value, f"{key!r} is empty"


# ----------------------------------------------------------------------
# Fake Playwright surface — minimal, just enough for the Compose flow
# ----------------------------------------------------------------------


class _FakeLocator:
    """Stand-in for ``playwright.async_api.Locator``."""

    def __init__(self, page: _FakePage, selector: str) -> None:
        self._page = page
        self.selector = selector

    @property
    def first(self) -> _FakeLocator:
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        self._page.calls.append(("wait_for", self.selector, state, timeout))
        # State-specific failures take precedence. Gmail's compose_pane
        # and sent_confirmation are the SAME selector (one window, keyed
        # on the subject box): it must pass on state="visible" (window
        # mounted) yet fail on state="hidden" (didn't detach after
        # send). Selector-only failures can't express that.
        failure = self._page.wait_for_state_failures.get((self.selector, state))
        if failure is None:
            failure = self._page.wait_for_failures.get(self.selector)
        if failure is not None:
            raise failure

    async def click(self, **kwargs: Any) -> None:
        self._page.calls.append(("click", self.selector, kwargs))

    async def inner_text(self) -> str:
        return self._page.inner_texts.get(self.selector, "")


class _FakeKeyboard:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def type(self, text: str, **kwargs: Any) -> None:
        self._page.calls.append(("keyboard.type", text))


class _FakePage:
    """Minimal Page double — just records the Compose call sequence."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.wait_for_failures: dict[str, Exception] = {}
        self.wait_for_state_failures: dict[tuple[str, str], Exception] = {}
        self.inner_texts: dict[str, str] = {}
        self.keyboard = _FakeKeyboard(self)
        # url defaults to outside-gmail so the invoker's "already on
        # inbox" short-circuit doesn't fire by accident.
        self.url: str = "about:blank"

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    async def goto(self, url: str, *, timeout: int = 0) -> None:
        self.calls.append(("goto", url, timeout))
        self.url = url


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def _build_factory(page: _FakePage) -> Any:
    async def factory() -> tuple[_FakeContext, _FakePage]:
        return _FakeContext(page), page

    return factory


# ----------------------------------------------------------------------
# Happy path — full Compose sequence
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_compose_walks_send_sequence() -> None:
    """The invoker should: navigate to inbox, click Compose, wait for
    the Compose window, fill To/Subject/Body, click Send, wait for the
    Compose window to detach. The order matters — the ack must
    actually leave the Sent folder for the persona anchor to exist."""
    page = _FakePage()
    factory = await _build_factory(page)

    result = await gmail_ui_send_email(
        to="alice@example.com",
        subject="Re: Quillfeather onboarding",
        body="Thanks — I'll get started as part of the engineering team.",
        page_factory=factory,
    )

    assert result.to == "alice@example.com"
    assert result.subject == "Re: Quillfeather onboarding"
    assert result.thread_id == ""  # UI path doesn't extract these
    assert result.message_id == ""

    # Navigate to the inbox.
    assert ("goto", COMPOSE_SELECTORS["inbox_url"], 20000) in page.calls

    # Click Compose and wait for the Compose window to mount.
    assert (
        "wait_for",
        COMPOSE_SELECTORS["compose_button"],
        "visible",
        20000,
    ) in page.calls
    assert any(c[0] == "click" and c[1] == COMPOSE_SELECTORS["compose_button"] for c in page.calls)
    assert (
        "wait_for",
        COMPOSE_SELECTORS["compose_pane"],
        "visible",
        20000,
    ) in page.calls

    # Each field: visible-wait → click (focuses) → keyboard.type.
    for field_selector, expected_value in [
        (COMPOSE_SELECTORS["to_field"], "alice@example.com"),
        (COMPOSE_SELECTORS["subject_field"], "Re: Quillfeather onboarding"),
        (
            COMPOSE_SELECTORS["body_field"],
            "Thanks — I'll get started as part of the engineering team.",
        ),
    ]:
        assert (
            "wait_for",
            field_selector,
            "visible",
            20000,
        ) in page.calls, f"missing wait_for on {field_selector}"
        assert any(c[0] == "click" and c[1] == field_selector for c in page.calls), (
            f"missing focus-click on {field_selector}"
        )
        assert (
            "keyboard.type",
            expected_value,
        ) in page.calls, f"missing keyboard.type for {expected_value!r}"

    # Send: wait visible → click. Then the Compose window must detach
    # (state=hidden) — that's our "send went through" gate.
    assert (
        "wait_for",
        COMPOSE_SELECTORS["send_button"],
        "visible",
        20000,
    ) in page.calls
    assert any(c[0] == "click" and c[1] == COMPOSE_SELECTORS["send_button"] for c in page.calls)
    assert (
        "wait_for",
        COMPOSE_SELECTORS["sent_confirmation"],
        "hidden",
        20000,
    ) in page.calls


# ----------------------------------------------------------------------
# Failure modes — every selector miss must surface as GmailComposeUnavailable
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compose_button_missing_raises_compose_unavailable() -> None:
    page = _FakePage()
    page.wait_for_failures[COMPOSE_SELECTORS["compose_button"]] = TimeoutError(
        "Compose button not visible"
    )
    factory = await _build_factory(page)

    with pytest.raises(GmailComposeUnavailable, match="Compose button"):
        await gmail_ui_send_email(
            to="x@example.com",
            subject="s",
            body="b",
            page_factory=factory,
        )


@pytest.mark.asyncio
async def test_compose_pane_missing_raises_compose_unavailable() -> None:
    page = _FakePage()
    page.wait_for_failures[COMPOSE_SELECTORS["compose_pane"]] = TimeoutError(
        "Compose window not mounted"
    )
    factory = await _build_factory(page)

    with pytest.raises(GmailComposeUnavailable, match="Compose window"):
        await gmail_ui_send_email(
            to="x@example.com",
            subject="s",
            body="b",
            page_factory=factory,
        )


@pytest.mark.asyncio
async def test_send_button_disabled_raises_compose_unavailable() -> None:
    page = _FakePage()
    page.wait_for_failures[COMPOSE_SELECTORS["send_button"]] = TimeoutError("Send button disabled")
    factory = await _build_factory(page)

    with pytest.raises(GmailComposeUnavailable, match="Send button"):
        await gmail_ui_send_email(
            to="x@example.com",
            subject="s",
            body="b",
            page_factory=factory,
        )


# ----------------------------------------------------------------------
# Send-failure enrichment — when the Compose window stays mounted, the
# invoker should read the inline error banner and surface its text.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compose_error_banner_text_is_surfaced_on_send_failure() -> None:
    page = _FakePage()
    page.wait_for_state_failures[(COMPOSE_SELECTORS["sent_confirmation"], "hidden")] = TimeoutError(
        "Compose window stayed mounted"
    )
    page.inner_texts[COMPOSE_SELECTORS["compose_error"]] = "This is not a valid email address."
    factory = await _build_factory(page)

    with pytest.raises(GmailComposeUnavailable, match="not a valid email"):
        await gmail_ui_send_email(
            to="invalid@@example",
            subject="s",
            body="b",
            page_factory=factory,
        )


@pytest.mark.asyncio
async def test_send_failure_without_banner_falls_back_to_generic_message() -> None:
    page = _FakePage()
    page.wait_for_state_failures[(COMPOSE_SELECTORS["sent_confirmation"], "hidden")] = TimeoutError(
        "Compose window stayed mounted"
    )
    page.wait_for_failures[COMPOSE_SELECTORS["compose_error"]] = TimeoutError("no banner visible")
    factory = await _build_factory(page)

    with pytest.raises(GmailComposeUnavailable, match="Compose window stayed mounted"):
        await gmail_ui_send_email(
            to="x@example.com",
            subject="s",
            body="b",
            page_factory=factory,
        )


# ----------------------------------------------------------------------
# Same-URL skip — don't re-navigate to inbox if already there
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_on_gmail_skips_redundant_goto() -> None:
    """When the persistent session is already on mail.google.com (a
    previous send in the same driver run), the invoker must not
    re-navigate — re-navigation would close the Compose window
    mid-flow on a follow-up call."""
    page = _FakePage()
    page.url = "https://mail.google.com/mail/u/0/#inbox"
    factory = await _build_factory(page)

    await gmail_ui_send_email(
        to="x@example.com",
        subject="s",
        body="b",
        page_factory=factory,
    )

    goto_calls = [c for c in page.calls if c[0] == "goto"]
    assert not goto_calls, f"expected no goto when already on inbox; got {goto_calls}"
