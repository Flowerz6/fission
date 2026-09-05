"""Playwright-driven Compose flow on outlook.com.

The V1 / V4 chains in the Google Red Team Report describe the
attacker_sender as ``$7/month subscriber with the recipient's
email address — no phishing URL, no malware, no sender-domain
spoofing``. The transport is incidental: anything that delivers
SMTP-routable email into the recipient's Workspace inbox satisfies
the threat model.

This module implements the **email send** half of that chain
against a real outlook.com tab driven by Playwright. Why Outlook
and not Gmail:

* The existing ``gmail.send_email`` handler uses the Gmail API
  under OAuth, and the advtest OAuth client is unverified — Google
  blocks unverified test apps from non-allowlist accounts. That
  gate is unrelated to the threat model.
* Outlook web compose has no equivalent gate. Once the operator
  has logged into outlook.com once in a persistent Playwright
  profile, send attempts go through.
* The recipient's inbox doesn't care whether the email came from
  Outlook, Gmail, ProtonMail, or a self-hosted SMTP relay. Gemini
  grounds on inbox content, not on sender provenance. So Outlook
  is faithful to the report's "anyone with email can do this"
  framing, not a workaround.

Entry point: :func:`outlook_ui_send_email`. Raises
:class:`OutlookComposeUnavailable` on selector miss / timeout.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from advtest_ext.adapters._ui_mode_common import jitter_pause
from advtest_ext.adapters.outlook.ui_mode.selectors import SELECTORS

# ----------------------------------------------------------------------
# Module-level tunables — mirror the google_docs invoker conventions
# so operators only have to learn one set of env-var knobs.
# ----------------------------------------------------------------------

_DEFAULT_TIMEOUT_MS = 20_000
_DEFAULT_PROFILE_DIR = Path.home() / ".advtest" / "profile-outlook-sender"

# Slow-mo for the live demo: every Playwright action waits this many
# ms before firing. Lets the operator and audience see what's
# happening. 0 disables.
_DEMO_SLOW_MO_MS = int(os.environ.get("ADVTEST_PLAYWRIGHT_SLOWMO_MS", "0") or "0")

# Human-pacing pause between Compose actions: real users glance,
# move the mouse, then click. Bounds in seconds. 0 disables.
_HUMAN_ACTION_PAUSE_S = (0.2, 0.6)


# ----------------------------------------------------------------------
# Public exception + result types
# ----------------------------------------------------------------------


class OutlookComposeUnavailable(RuntimeError):  # noqa: N818 — public contract
    """The Compose flow couldn't complete: a selector missed, a
    timeout fired, or the Send button stayed disabled. Caller
    decides whether to retry (Recipe Runner's on_fail policy)."""


@dataclass
class OutlookSendResult:
    """What :func:`outlook_ui_send_email` returns.

    Mirrors the shape of the existing ``adapters.gmail.recipe_ops``
    handler's outputs so a Recipe that's currently calling
    ``gmail.send_email`` can switch to ``outlook.send_email`` by
    re-pointing the operation name only — no template-ref changes
    needed for the ``{{step.<id>.to}}`` / ``subject`` chains.

    ``thread_id`` and ``message_id`` are intentionally blank on the
    UI path: extracting them from the Sent folder is selector-fragile
    and V1 doesn't need them (the reply step uses ``me`` self-reply).
    Downstream Recipes that DO need a thread id should use the
    API-backed ``gmail.send_email`` handler instead, behind OAuth
    approval.
    """

    to: str
    subject: str
    thread_id: str = ""
    message_id: str = ""


# ----------------------------------------------------------------------
# Helpers — keep these private so the public surface stays small.
# ----------------------------------------------------------------------


def _profile_dir() -> Path:
    """Resolve the persistent profile dir for the Outlook sender.

    Operator can override via ``ADVTEST_PROFILE_OUTLOOK_SENDER``;
    default lives under ``~/.advtest/profile-outlook-sender``.
    Matches the same env-var pattern the Docs adapter uses, so
    operators have one mental model for "where do logins live".
    """
    override = os.environ.get("ADVTEST_PROFILE_OUTLOOK_SENDER")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_PROFILE_DIR


def _timeout_ms() -> int:
    """Per-step timeout. ``ADVTEST_PLAYWRIGHT_TIMEOUT`` env var
    override; default 20 seconds — Outlook's Compose pane mount is
    slower than a typical SPA action."""
    raw = os.environ.get("ADVTEST_PLAYWRIGHT_TIMEOUT", "")
    if raw.isdigit():
        return int(raw)
    return _DEFAULT_TIMEOUT_MS


async def _launch_context(profile_dir: Path) -> Any:
    """Open a persistent Chromium context at the profile dir.

    Same pattern as ``google_docs.ui_mode.invoker._launch_context``:
    deferred Playwright import (so tests can mock this whole
    function without Playwright installed), persistent context
    against a user-data dir so logins persist between runs.
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
    """Close the persistent context AND the Playwright driver
    stashed on it. Mirrors ``google_docs.ui_mode.invoker._close_context``."""
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
    """Click 'New mail' and wait for the Compose pane to mount.

    Three failure modes wrap to :class:`OutlookComposeUnavailable`:

    1. The 'New mail' button never appears (Outlook redirected to
       SSO / a non-inbox page → operator needs to re-login).
    2. The click lands but the Compose pane never mounts (rate-
       limit, selector drift).
    3. The Compose pane mounts but the body field doesn't accept
       focus within the timeout.
    """
    try:
        button = page.locator(SELECTORS["new_mail_button"]).first
        await button.wait_for(state="visible", timeout=timeout_ms)
        # force=True: a just-arrived "New mail" toast / its dialog
        # backdrop can transiently overlay the toolbar; the real
        # Compose button is still the right target, so dispatch the
        # click regardless of a momentary pointer-event intercept.
        await button.click(force=True)
    except Exception as exc:
        raise OutlookComposeUnavailable(
            "New-mail button not actionable — re-validate selectors "
            "or check whether outlook.com redirected the profile "
            "to a login screen"
        ) from exc

    try:
        pane = page.locator(SELECTORS["compose_pane"]).first
        await pane.wait_for(state="visible", timeout=timeout_ms)
    except Exception as exc:
        raise OutlookComposeUnavailable(
            "Compose pane did not mount after clicking New mail"
        ) from exc


async def _fill_compose_field(
    page: Any, selector: str, value: str, *, field_name: str, timeout_ms: int
) -> None:
    """Focus a Compose field and type the value via the keyboard.

    Programmatic ``focus()`` then ``keyboard.type()`` is more
    reliable than ``locator.fill()`` for Outlook's contenteditable
    fields — fill() sometimes loses the value to Outlook's
    autocomplete / address-resolver popup. Keyboard input goes
    through the same path a human's input would.
    """
    try:
        target = page.locator(selector).first
        await target.wait_for(state="visible", timeout=timeout_ms)
        await target.click()  # focuses + dismisses any autocomplete popover
        await page.keyboard.type(value)
        await jitter_pause(_HUMAN_ACTION_PAUSE_S)
    except Exception as exc:
        raise OutlookComposeUnavailable(f"could not fill Compose {field_name!r} field") from exc


async def _set_body_html(page: Any, html: str, timeout_ms: int) -> None:
    """Insert raw HTML into the Compose body — used to plant a HIDDEN
    segment (white text, 1px font, off-screen span) that a human
    recipient can't see but Gemini still ingests when it grounds on the
    email. The visible cover text + the hidden span are both in ``html``.

    Outlook's body is a rich contenteditable with its own editor model,
    so we focus it and insert via ``execCommand('insertHTML')`` (which
    the editor intercepts), falling back to assigning ``innerHTML`` on
    the focused element when execCommand is a no-op.

    DELIVERY FIDELITY IS UNVERIFIED: Outlook may sanitise inline styles
    (strip ``color`` / ``font-size``) on send, and the receiving client
    or Gemini's grounding may normalise hidden text. Inspect the
    delivered message to confirm the segment arrived AND stayed hidden.
    """
    try:
        target = page.locator(SELECTORS["body_field"]).first
        await target.wait_for(state="visible", timeout=timeout_ms)
        await target.click()  # focus the contenteditable
        await page.evaluate(
            """(html) => {
                const ok = document.execCommand
                    && document.execCommand('insertHTML', false, html);
                if (!ok) {
                    const el = document.activeElement;
                    if (el) { el.innerHTML = html; }
                }
            }""",
            html,
        )
        await jitter_pause(_HUMAN_ACTION_PAUSE_S)
    except Exception as exc:
        raise OutlookComposeUnavailable("could not set HTML Compose body") from exc


async def _click_send(page: Any, timeout_ms: int) -> None:
    """Click the Send button. The send button stays ``aria-disabled``
    until To + Body have content; our selector explicitly excludes
    disabled state so the wait_for naturally stalls until Outlook
    is ready to accept the send."""
    try:
        send = page.locator(SELECTORS["send_button"]).first
        await send.wait_for(state="visible", timeout=timeout_ms)
        await send.click()
    except Exception as exc:
        raise OutlookComposeUnavailable(
            "Send button not actionable — recipient address may be "
            "rejected by Outlook's resolver, or selectors drifted"
        ) from exc


async def _wait_for_sent(page: Any, timeout_ms: int) -> None:
    """Wait for the Compose pane to detach — Outlook's most stable
    'send went through' signal. A transient toast also fires but
    its selector changes more often.

    On failure (pane stayed mounted), peek for an inline Outlook
    error banner before raising. This is the difference between
    "we don't know why the send didn't go through" and "Outlook
    said the recipient was rejected" — same heuristic primary
    signal, but the operator gets a real reason instead of a
    generic timeout. The peek uses a short timeout (1s) so the
    failure path doesn't stall the full per-step timeout twice.
    """
    try:
        await page.locator(SELECTORS["sent_confirmation"]).first.wait_for(
            state="hidden", timeout=timeout_ms
        )
    except Exception as exc:
        banner = await _read_compose_error(page, timeout_ms=1_000)
        if banner:
            raise OutlookComposeUnavailable(f"Outlook reported a send failure: {banner}") from exc
        raise OutlookComposeUnavailable(
            "Compose pane stayed mounted after Send click — message "
            "likely did not actually send (network failure, "
            "recipient rejected, or quota hit)"
        ) from exc


async def _read_compose_error(page: Any, *, timeout_ms: int) -> str | None:
    """Best-effort read of any inline Outlook error banner.

    Only called on the send-failure path to enrich the error
    message — never gates the success path. Any exception (no
    banner, page double doesn't implement inner_text, selector
    misses) falls through to ``None``, which lets the caller
    fall back to the generic failure message. We deliberately
    swallow all exceptions here because this is enrichment, not
    a load-bearing signal.
    """
    try:
        banner = page.locator(SELECTORS["compose_error"]).first
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


async def outlook_ui_send_email(
    *,
    to: str,
    subject: str,
    body: str,
    body_html: str | None = None,
    profile_dir: Path | None = None,
    timeout_ms: int | None = None,
    page_factory: Any = None,
    external_context: Any = None,
    external_page: Any = None,
) -> OutlookSendResult:
    """Send a single email via outlook.com web UI.

    Lifecycle matches the Docs ui_mode_invoke contract so the same
    UiModeSession / SessionPool plumbing can drive it:

    * ``page_factory`` — test injection of ``(context, page)`` pair;
      caller owns lifecycle.
    * ``external_page`` — reuse a persistent page across calls;
      navigate in place, close nothing.
    * ``external_context`` — open a fresh page on a shared context;
      close just the page.
    * neither — fresh launch on the profile, close everything.

    Returns :class:`OutlookSendResult` carrying ``to`` + ``subject``
    (echoed for downstream template refs). ``thread_id`` and
    ``message_id`` are empty strings on the UI path — see the
    :class:`OutlookSendResult` docstring.
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
        # there — same idempotency the Docs invoker uses to keep a
        # multi-call session from re-navigating mid-flow.
        current_url = getattr(page, "url", "") or ""
        if "outlook" not in current_url:
            await page.goto(SELECTORS["inbox_url"], timeout=timeout)

        await _wait_for_compose_open(page, timeout)
        await _fill_compose_field(
            page,
            SELECTORS["to_field"],
            to,
            field_name="To",
            timeout_ms=timeout,
        )
        await _fill_compose_field(
            page,
            SELECTORS["subject_field"],
            subject,
            field_name="Subject",
            timeout_ms=timeout,
        )
        if body_html is not None:
            await _set_body_html(page, body_html, timeout)
        else:
            await _fill_compose_field(
                page,
                SELECTORS["body_field"],
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

    return OutlookSendResult(to=to, subject=subject)


# ----------------------------------------------------------------------
# Persistent session — same shape as the Docs UiModeSession so the
# SessionPool can hand one out per account_role.
# ----------------------------------------------------------------------


class OutlookUiSession:
    """A persistent Outlook Compose session bound to one profile.

    Reuses one Chromium context + one page across many
    :func:`outlook_ui_send_email` invocations. The Recipe Runner
    gets one of these per ``attacker_sender`` account_role when
    the role's profile points at an Outlook-logged-in profile dir.

    Public surface mirrors ``google_docs.ui_mode.UiModeSession`` —
    intentional, so the SessionPool can hold a uniform protocol
    across services (current pool implementation is per-service
    but the shared shape makes a future merge straightforward).
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
        """Idempotent close. Safe to call multiple times — the
        Runner's finally block doesn't track whether the session
        was ever opened."""
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
    "OutlookComposeUnavailable",
    "OutlookSendResult",
    "OutlookUiSession",
    "outlook_ui_send_email",
]
