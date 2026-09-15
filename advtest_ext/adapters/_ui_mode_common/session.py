"""Shared Playwright context lifecycle + ``UiModeSession`` class.

Both gmail and google_docs ui_mode invokers needed identical:

  - ``launch_context(profile_dir)`` — open a persistent Chromium
    context with the Workspace stealth args.
  - ``close_context(context)`` — close the context and the driver
    handle stashed on it.
  - ``UiModeSession`` — long-lived wrapper that opens Chrome once,
    hands out the persistent context to each invocation, and tears
    down at the end of the run (avoids the "unusual activity" trip
    that comes from rapid cold-Chrome relaunches against the same
    profile).

Centralising the three here makes future products (Sheets,
Calendar, Slides) one-import additions. Outlook keeps its own
``_launch_context`` for now because its ``slow_mo`` reads from a
product-specific env var; a follow-up commit could unify that too
by parameterising ``launch_context(slow_mo_ms=...)``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from advtest_ext.adapters._ui_mode_common.config import (
    DEMO_SLOW_MO_MS,
    NAVIGATOR_WEBDRIVER_SHIM,
    resolve_window_geometry,
)


async def launch_context(profile_dir: Path) -> Any:
    """Open a persistent Chromium context at the profile dir with
    the Workspace stealth args.

    Deferred Playwright import: tests mock this function directly
    so the package's missing-import gates don't break the unit
    tests when Playwright isn't installed.
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    window_x, window_y, window_width, window_height = resolve_window_geometry()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        channel="chrome",  # use system Chrome, not bundled Chromium
        slow_mo=DEMO_SLOW_MO_MS,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-default-browser-check",
            "--no-first-run",
            f"--window-position={window_x},{window_y}",
            f"--window-size={window_width},{window_height}",
        ],
        # ``--enable-automation`` — strips the "Chrome is being
        #     controlled by automated test software" infobar.
        # ``--no-sandbox`` — Playwright sometimes passes this for
        #     Chromium launches; system Chrome warns the user via
        #     a yellow infobar that's an eyesore during a live demo.
        ignore_default_args=["--enable-automation", "--no-sandbox"],
        no_viewport=True,
    )
    # Mask the navigator.webdriver tell on EVERY launched context.
    # This used to live only in ``UiModeSession.ensure_open``, so the
    # bare fresh-launch path (``ui_mode_invoke`` with no session — used
    # by one-shot scripts like retrieval_ipi_validation.py) leaked the
    # property. Applying it here hardens every caller identically.
    # Best-effort: a shim-injection failure must not crash the launch.
    try:
        await context.add_init_script(NAVIGATOR_WEBDRIVER_SHIM)
    except Exception:
        pass
    # Stash the playwright handle on the context so the caller can
    # close it cleanly on teardown. Playwright's persistent_context
    # doesn't carry a back-reference to the driver.
    setattr(context, "__advtest_pw", pw)
    return context


async def close_context(context: Any) -> None:
    """Tear down the persistent context and the Playwright driver.

    The driver handle was stashed on the context object during
    :func:`launch_context` because Playwright doesn't expose a
    back-reference. Closing it shuts down the helper subprocess and
    avoids the ``pyee`` warning at process exit.
    """
    try:
        await context.close()
    finally:
        pw = getattr(context, "__advtest_pw", None)
        if pw is not None:
            await pw.stop()


class UiModeSession:
    """Long-lived Playwright context shared across many ui_mode
    invocations.

    The default per-invocation codepath launches a fresh Chrome on
    every call — fine for one-shot scenarios, but a death sentence
    for multi-round attack loops (PAIR, TAP) against Google's
    surface. Sixteen cold Chrome starts in ten minutes against the
    same persistent profile is exactly the shape "unusual activity"
    defences trip on, and once you trip them the side panel DOM
    changes (auth challenge / verification / throttled output) and
    selectors miss.

    ``UiModeSession`` opens Chrome once, hands out the persistent
    context to each invocation, and tears down at the end of the
    run. The session is typically owned by the per-product
    ``DocumentTarget`` (e.g. :class:`GoogleDocsTarget`,
    :class:`GmailTarget`) in ``ui_mode``; tests can also construct
    one directly with a custom launcher via monkeypatch.

    Lifecycle::

        session = UiModeSession(profile_dir=...)
        ctx = await session.ensure_open()    # idempotent
        ... (many ui_mode_invoke calls using ctx) ...
        await session.close()
    """

    def __init__(self, profile_dir: Path) -> None:
        self._profile_dir = profile_dir
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None

    @property
    def context(self) -> Any:
        """Return the live context, or ``None`` if not yet opened."""
        return self._context

    @property
    def page(self) -> Any:
        """Return the cached page (if any). Mostly here for tests."""
        return self._page

    async def ensure_open(self) -> Any:
        """Lazy-launch the Chrome context. Subsequent calls return
        the cached one. The ``navigator.webdriver`` shim is applied
        inside :func:`launch_context`, so it covers this session path
        and the bare fresh-launch path alike."""
        if self._context is not None:
            return self._context
        self._context = await launch_context(self._profile_dir)
        # Stash the Playwright handle for close(). launch_context
        # already stashes it on the context object; we duplicate the
        # reference here so close() doesn't have to introspect a
        # dunder attribute on a Playwright type.
        self._pw = getattr(self._context, "__advtest_pw", None)
        return self._context

    async def ensure_page(self) -> Any:
        """Lazy-create a SINGLE page on the session and reuse it
        across every ``ui_mode_invoke`` call.

        Why: opening a fresh tab per attack leaves the visible
        Chrome window blank for several seconds between branches —
        ``chrome://new-tab-page`` paints while we wait for the next
        Workspace doc / thread to be ready, then Playwright
        navigates. For a live demo, that blank gap looks broken.
        Reusing one page means every ``page.goto(...)`` is an
        in-place navigation: the previous doc stays visible until
        the next one renders, with no intermediate blank tab.

        The page is torn down by :meth:`close` along with the
        context."""
        await self.ensure_open()
        if self._page is not None:
            return self._page
        self._page = await self._context.new_page()
        return self._page

    async def new_page(self) -> Any:
        """Swap in a CLEAN page on the same warm context — a fresh tab for the
        next tree node — without a cold browser relaunch.

        Reusing one page (``ensure_page``) is right for a single linear
        crescendo, but in TAP tree mode each node is an independent branch:
        node N must not inherit node N-1's populated document, left-over
        confirmation dialogs, or side-panel conversation state (which was
        surfacing as ``SidePanelUnavailable`` on later nodes). Closing just the
        page and opening a new one keeps the persistent context — and its
        login — warm, so Google's anti-automation heuristics don't see a
        relaunch."""
        await self.ensure_open()
        old = self._page
        self._page = None
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass
        self._page = await self._context.new_page()
        return self._page

    async def close(self) -> None:
        """Tear down the context and the Playwright driver. Safe to
        call when never opened (no-op). Closes the cached page first
        so the context-close doesn't race a still-running page."""
        if self._page is not None:
            page = self._page
            self._page = None
            try:
                await page.close()
            except Exception:
                pass
        if self._context is None:
            return
        ctx = self._context
        self._context = None
        try:
            await close_context(ctx)
        finally:
            self._pw = None
