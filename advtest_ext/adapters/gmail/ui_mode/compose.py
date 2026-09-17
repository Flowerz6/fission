"""Playwright-driven Compose flow on mail.google.com.

Sibling of :mod:`advtest_ext.adapters.outlook.ui_mode.invoker`. The
Outlook invoker sends the poisoned series into the victim inbox; this
one sends the **acknowledgment reply from the victim's own Gmail
account**, so the adopted persona is written into the victim's Sent
folder. That Sent content is itself grounding fodder for the Gemini
side panel, which is why the ack is automated to the same standard as
the inbound series rather than left as a manual afterthought.

Why a UI compose path instead of the Gmail API:

* The existing ``gmail.send_email`` handler uses the Gmail API under
  OAuth, and the advtest OAuth client is unverified — Google blocks
  unverified test apps from non-allowlist accounts. That gate is
  unrelated to the threat model and would block the ack on most test
  accounts.
* Gmail web compose has no equivalent gate. Once the operator has
  logged into mail.google.com once in a persistent Playwright
  profile, send attempts go through — exactly the Outlook story, on
  the Google side.

Entry point: :func:`gmail_ui_send_email`. Raises
:class:`GmailComposeUnavailable` on selector miss / timeout.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from advtest_ext.adapters._ui_mode_common import jitter_pause
from advtest_ext.adapters.gmail.ui_mode.compose_selectors import COMPOSE_SELECTORS

# ----------------------------------------------------------------------
# Module-level tunables — mirror the Outlook invoker conventions so
# operators only have to learn one set of env-var knobs.
# ----------------------------------------------------------------------

_DEFAULT_TIMEOUT_MS = 20_000
_DEFAULT_PROFILE_DIR = Path.home() / ".advtest" / "profile-gmail-sender"

# Slow-mo for the live demo: every Playwright action waits this many
# ms before firing. Shares the Outlook/Docs env var so one knob slows
# every surface. 0 disables.
_DEMO_SLOW_MO_MS = int(os.environ.get("ADVTEST_PLAYWRIGHT_SLOWMO_MS", "0") or "0")

# Human-pacing pause between Compose actions. Bounds in seconds.
_HUMAN_ACTION_PAUSE_S = (0.2, 0.6)


# ----------------------------------------------------------------------
# Public exception + result types
# ----------------------------------------------------------------------


class GmailComposeUnavailable(RuntimeError):  # noqa: N818 — public contract
    """The Compose flow couldn't complete: a selector missed, a
    timeout fired, or the Send button stayed disabled. Caller
    decides whether to retry."""


@dataclass
class GmailSendResult:
    """What :func:`gmail_ui_send_email` returns.

    Mirrors :class:`OutlookSendResult`. ``thread_id`` and
    ``message_id`` are intentionally blank on the UI path: extracting
    them from the Sent folder is selector-fragile and the ack step
    doesn't need them.
    """

    to: str
    subject: str
    thread_id: str = ""
    message_id: str = ""


# ----------------------------------------------------------------------
# Helpers — keep these private so the public surface stays small.
# ----------------------------------------------------------------------


def _profile_dir() -> Path:
    """Resolve the persistent profile dir for the Gmail sender.

    Operator can override via ``ADVTEST_PROFILE_GMAIL_SENDER``;
    default lives under ``~/.advtest/profile-gmail-sender``. Keep this
    distinct from the side-panel profile
    (``ADVTEST_PLAYWRIGHT_PROFILE_DIR``) only if you want a separate
    login; pointing both at the same Gmail-logged-in profile is fine
    and keeps the ack in the same account Gemini grounds on.
    """
    override = os.environ.get("ADVTEST_PROFILE_GMAIL_SENDER")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_PROFILE_DIR


def _timeout_ms() -> int:
    """Per-step timeout. ``ADVTEST_PLAYWRIGHT_TIMEOUT`` env var
    override; default 20 seconds."""
    raw = os.environ.get("ADVTEST_PLAYWRIGHT_TIMEOUT", "")
    if raw.isdigit():
        return int(raw)
    return _DEFAULT_TIMEOUT_MS


async def _launch_context(profile_dir: Path) -> Any:
    """Open a persistent Chromium context at the profile dir.

    Same pattern as the Outlook invoker's ``_launch_context``:
    deferred Playwright import (so tests can mock this whole function
    without Playwright installed), persistent context against a
    user-data dir so logins persist between runs.
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        channel="chrome",
        slow_mo=_DEMO_SLOW_MO_MS,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-default-browser-check",
            "--no-first-run",
        ],
        ignore_default_args=["--enable-automation", "--no-sandbox"],
        viewport={"width": 1280, "height": 900},
    )
    setattr(context, "__advtest_pw", pw)
    return context


async def _close_context(context: Any) -> None:
    """Close the persistent context AND the Playwright driver stashed
    on it. Mirrors the Outlook invoker's ``_close_context``."""
    pw = getattr(context, "__advtest_pw", None)
    try:
        await context.close()
    except Exception:
        pass
    if pw is not None:
        try:
            await pw.stop()
        except Exception:
            pass


async def _wait_for_compose_open(page: Any, timeout_ms: int) -> None:
    """Click 'Compose' and wait for the Compose window to mount.

    Two failure modes wrap to :class:`GmailComposeUnavailable`:

    1. The 'Compose' button never appears (Gmail redirected to SSO /
       a non-inbox page -> operator needs to re-login).
    2. The click lands but the Compose window never mounts (rate-
       limit, selector drift).
    """
    try:
        button = page.locator(COMPOSE_SELECTORS["compose_button"]).first
        await button.wait_for(state="visible", timeout=timeout_ms)
        await button.click()
    except Exception as exc:
        raise GmailComposeUnavailable(
            "Compose button not actionable — re-validate selectors or "
            "check whether mail.google.com redirected the profile to a "
            "login screen"
        ) from exc

    try:
        pane = page.locator(COMPOSE_SELECTORS["compose_pane"]).first
        await pane.wait_for(state="visible", timeout=timeout_ms)
    except Exception as exc:
        raise GmailComposeUnavailable(
            "Compose window did not mount after clicking Compose"
        ) from exc


async def _fill_compose_field(
    page: Any,
    selector: str,
    value: str,
    *,
    field_name: str,
    timeout_ms: int,
    press_after: str | None = None,
) -> None:
    """Focus a Compose field and type the value via the keyboard.

    Programmatic focus-via-click then ``keyboard.type()`` is more
    reliable than ``locator.fill()`` for Gmail's contenteditable body
    and its peoplekit recipient input — fill() sometimes loses the
    value to Gmail's address-resolver / autocomplete popover.

    ``press_after`` presses a key once typing is done. The recipient
    (To) field needs ``press_after="Enter"``: Gmail's peoplekit opens a
    contact-autocomplete dropdown that OVERLAYS the next field (Subject)
    and intercepts its click. Pressing Enter commits the recipient chip
    and closes that dropdown, so the subsequent Subject click lands.
    """
    try:
        target = page.locator(selector).first
        await target.wait_for(state="visible", timeout=timeout_ms)
        await target.click()  # focuses + dismisses any autocomplete popover
        await page.keyboard.type(value)
        if press_after:
            await page.keyboard.press(press_after)
        await jitter_pause(_HUMAN_ACTION_PAUSE_S)
    except Exception as exc:
        raise GmailComposeUnavailable(f"could not fill Compose {field_name!r} field") from exc


async def _click_send(page: Any, timeout_ms: int) -> None:
    """Click the Send button. The selector excludes the
    ``aria-disabled`` state, so the wait_for naturally stalls until
    Gmail is ready to accept the send (To + Body present)."""
    try:
        send = page.locator(COMPOSE_SELECTORS["send_button"]).first
        await send.wait_for(state="visible", timeout=timeout_ms)
        await send.click()
    except Exception as exc:
        raise GmailComposeUnavailable(
            "Send button not actionable — recipient address may be "
            "rejected by Gmail's resolver, or selectors drifted"
        ) from exc


async def _wait_for_sent(page: Any, timeout_ms: int) -> None:
    """Wait for the Compose window to detach — Gmail's most stable
    'send went through' signal, mirroring the Outlook invoker.

    On failure (window stayed mounted), peek for an inline Gmail
    error banner before raising, so the operator gets a real reason
    instead of a generic timeout. The peek uses a short timeout so the
    failure path doesn't stall the full per-step timeout twice.
    """
    try:
        await page.locator(COMPOSE_SELECTORS["sent_confirmation"]).first.wait_for(
            state="hidden", timeout=timeout_ms
        )
    except Exception as exc:
        banner = await _read_compose_error(page, timeout_ms=1_000)
        if banner:
            raise GmailComposeUnavailable(f"Gmail reported a send failure: {banner}") from exc
        raise GmailComposeUnavailable(
            "Compose window stayed mounted after Send click — message "
            "likely did not actually send (network failure, recipient "
            "rejected, or quota hit)"
        ) from exc


async def _read_compose_error(page: Any, *, timeout_ms: int) -> str | None:
    """Best-effort read of any inline Gmail error banner.

    Only called on the send-failure path to enrich the error message
    — never gates the success path. Any exception falls through to
    ``None``, which lets the caller fall back to the generic failure
    message.
    """
    try:
        banner = page.locator(COMPOSE_SELECTORS["compose_error"]).first
        await banner.wait_for(state="visible", timeout=timeout_ms)
        text = await banner.inner_text()
    except Exception:
        return None
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    return cleaned[:200]


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------


async def gmail_ui_send_email(
    *,
    to: str,
    subject: str,
    body: str,
    profile_dir: Path | None = None,
    timeout_ms: int | None = None,
    page_factory: Any = None,
    external_context: Any = None,
    external_page: Any = None,
) -> GmailSendResult:
    """Send a single email via mail.google.com web UI.

    Lifecycle matches :func:`outlook_ui_send_email`:

    * ``page_factory`` — test injection of ``(context, page)`` pair;
      caller owns lifecycle.
    * ``external_page`` — reuse a persistent page across calls;
      navigate in place, close nothing.
    * ``external_context`` — open a fresh page on a shared context;
      close just the page.
    * neither — fresh launch on the profile, close everything.

    Returns :class:`GmailSendResult` carrying ``to`` + ``subject``
    (echoed for downstream refs). ``thread_id`` / ``message_id`` are
    empty on the UI path — see the :class:`GmailSendResult` docstring.
    """
    profile = profile_dir or _profile_dir()
    timeout = timeout_ms if timeout_ms is not None else _timeout_ms()

    page: Any = None
    context: Any = None
    owns_context: bool = False
    owns_page: bool = False
    try:
        if page_factory is not None:
            context, page = await page_factory()
        elif external_page is not None:
            page = external_page
        elif external_context is not None:
            context = external_context
            page = await context.new_page()
            owns_page = True
        else:
            context = await _launch_context(profile)
            owns_context = True
            page = await context.new_page()

        # Land on the Inbox. Skip the goto when the page is already
        # there — same idempotency the Outlook invoker uses to keep a
        # multi-call session from re-navigating mid-flow.
        current_url = getattr(page, "url", "") or ""
        if "mail.google.com" not in current_url:
            await page.goto(COMPOSE_SELECTORS["inbox_url"], timeout=timeout)

        await _wait_for_compose_open(page, timeout)
        await _fill_compose_field(
            page,
            COMPOSE_SELECTORS["to_field"],
            to,
            field_name="To",
            timeout_ms=timeout,
            press_after="Enter",  # commit recipient chip + close peoplekit dropdown
        )
        await _fill_compose_field(
            page,
            COMPOSE_SELECTORS["subject_field"],
            subject,
            field_name="Subject",
            timeout_ms=timeout,
        )
        await _fill_compose_field(
            page,
            COMPOSE_SELECTORS["body_field"],
            body,
            field_name="Body",
            timeout_ms=timeout,
        )
        await _click_send(page, timeout)
        await _wait_for_sent(page, timeout)
    finally:
        if owns_page and page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if owns_context and context is not None:
            try:
                await _close_context(context)
            except Exception:
                pass

    return GmailSendResult(to=to, subject=subject)


# ----------------------------------------------------------------------
# Persistent session — same shape as OutlookUiSession so a driver can
# reuse one Chrome across many sends (avoids the cold-relaunch
# "unusual activity" trip against the same profile).
# ----------------------------------------------------------------------


class GmailUiComposeSession:
    """A persistent Gmail Compose session bound to one profile.

    Reuses one Chromium context + one page across many
    :func:`gmail_ui_send_email` invocations. Public surface mirrors
    :class:`OutlookUiSession`.
    """

    def __init__(self, *, profile_dir: Path | None = None) -> None:
        self._profile_dir = profile_dir or _profile_dir()
        self._context: Any | None = None
        self._page: Any | None = None

    async def ensure_page(self) -> Any:
        """Lazily launch the context + open a page on first call,
        return the same page on subsequent calls."""
        if self._context is None:
            self._context = await _launch_context(self._profile_dir)
        if self._page is None:
            self._page = await self._context.new_page()
        return self._page

    async def close(self) -> None:
        """Idempotent close. Safe to call multiple times."""
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None
        if self._context is not None:
            try:
                await _close_context(self._context)
            except Exception:
                pass
            self._context = None


__all__ = [
    "GmailComposeUnavailable",
    "GmailSendResult",
    "GmailUiComposeSession",
    "gmail_ui_send_email",
]
