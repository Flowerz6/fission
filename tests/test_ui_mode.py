"""Tests for ``advtest_ext.adapters.google_docs.ui_mode``.

Demo-grade Playwright wiring; tests run against mocked Page /
Locator doubles so Playwright doesn't need to be installed. Coverage:

* :data:`SELECTORS` carries exactly the keys the invoker looks up.
* ``ui_mode_invoke`` walks the open → submit → read → screenshot
  sequence on the supplied Page, in the right order, with the
  right selectors.
* Any Playwright failure surfaces as :class:`SidePanelUnavailable`
  with the canonical drift-pointer message.
* ``Response.raw["screenshot_path"]`` is the absolute path under the
  configured output_dir / screenshots / <trace_id>.png.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from advtest_ext.adapters._ui_mode_common import session as _session_module
from advtest_ext.adapters.google_docs.ui_mode import (
    SELECTORS,
    SidePanelUnavailable,
    UiModeSession,
    ui_mode_invoke,
)
from advtest_ext.adapters.google_docs.ui_mode import invoker as _invoker_module
from advtest_ext.adapters.google_docs.ui_mode.selectors import EXPECTED_KEYS


@pytest.fixture(autouse=True)
def _disable_human_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``jitter_pause`` with a no-op for the whole module.

    The human-pacing helpers add hundreds of milliseconds of
    ``asyncio.sleep`` per side-panel flow — fine for live demos,
    poison for unit tests. Tests assert on call ordering and
    artefacts, not on the timing, so zero delay is correct here.
    Anything genuinely testing pacing behaviour (none right now)
    would opt out by re-monkey-patching inside the test."""

    async def _noop(_bounds: tuple[float, float]) -> None:
        return None

    monkeypatch.setattr(_invoker_module, "jitter_pause", _noop)


# ----------------------------------------------------------------------
# Selector registry contract
# ----------------------------------------------------------------------


def test_selectors_have_expected_keys() -> None:
    """The invoker keys into SELECTORS by name; a missing key would
    surface as KeyError at runtime. EXPECTED_KEYS is the canonical
    set — the registry must match exactly."""
    assert set(SELECTORS.keys()) == EXPECTED_KEYS


def test_selectors_values_are_non_empty_strings() -> None:
    """Each selector is a Playwright selector string. Empty values
    would silently match any element — protect against an accidental
    truncation."""
    for key, value in SELECTORS.items():
        assert isinstance(value, str), f"{key} must be str"
        assert value.strip(), f"{key} must be non-empty"


# ----------------------------------------------------------------------
# Fake Playwright surface
# ----------------------------------------------------------------------


class _FakeLocator:
    """Stand-in for ``playwright.async_api.Locator``.

    Records every ``wait_for`` / ``click`` / ``type`` / ``inner_text``
    / ``bounding_box`` / ``get_attribute`` call so assertions on the
    call sequence are direct. Behaviour is scripted via the parent
    ``_FakePage`` — visibility of the side-panel container is tied
    to a small state machine so the hardened open flow's
    "already-open" / "click-then-retry" branches are exercisable
    without a real browser."""

    def __init__(self, page: _FakePage, selector: str) -> None:
        self._page = page
        self.selector = selector

    async def wait_for(self, *, state: str, timeout: int) -> None:
        self._page.calls.append(("wait_for", self.selector, state, timeout))
        # The side-panel container's visibility is governed by the
        # open-state machine, not the generic wait_for_failures map.
        # Fast-path / post-click verification both go through this
        # branch with the same selector, distinguished only by timeout.
        if self.selector == SELECTORS["side_panel_container"] and state == "visible":
            if not self._page.container_open:
                raise TimeoutError("side panel container not visible")
            return
        # State-keyed failures take precedence so tests can fail just
        # the hidden-wait on the loading indicator (or just the
        # visible-wait) without also affecting the other state. The
        # legacy state-agnostic map below stays for tests that don't
        # care about the state edge.
        state_key = (self.selector, state)
        if state_key in self._page.wait_for_failures_by_state:
            raise self._page.wait_for_failures_by_state[state_key]
        if self._page.wait_for_failures.get(self.selector):
            raise self._page.wait_for_failures[self.selector]

    async def click(self, **kwargs: Any) -> None:
        self._page.calls.append(("click", self.selector, kwargs))
        # Clicking the entry button advances the open-state counter.
        # The panel opens once the cumulative click count meets the
        # configured threshold — default 1 (first click works); set
        # higher to simulate a swallowed first click.
        if self.selector == SELECTORS["side_panel_button"]:
            self._page.button_click_count += 1
            if self._page.button_click_count >= self._page.button_clicks_to_open:
                self._page.container_open = True

    async def hover(self) -> None:
        # Recorded so tests CAN assert hover-before-click ordering
        # if they care (the happy-path test doesn't, by design — the
        # pacing helpers are exercised by their own targeted tests).
        self._page.calls.append(("hover", self.selector))

    async def focus(self) -> None:
        # Programmatic focus. Production uses this for the side-
        # panel combobox (the click-based focus path triggers
        # quick-suggestion chips that submit before our keystrokes
        # arrive). Recorded so tests can lock the "focus before
        # keyboard.type" ordering.
        self._page.calls.append(("focus", self.selector))

    @property
    def first(self) -> _FakeLocator:
        # Playwright's ``Locator.first`` returns the first match.
        # The fake collapses "all matches" into a single recorded
        # locator, so ``first`` is just self.
        return self

    @property
    def last(self) -> _FakeLocator:
        # Same shape as ``first`` — the fake has only one match per
        # selector, so ``last`` is also self. Production uses
        # ``.last`` on side_panel_response to pick the newest agent
        # bubble across a multi-turn conversation.
        return self

    async def type(self, text: str, **kwargs: Any) -> None:
        # Production code now types via ``page.keyboard.type`` for the
        # human-pacing delay; this method survives so legacy /
        # locator-level callers don't break (the input field still
        # has historical .type() callers in some tests). ``**kwargs``
        # absorbs Playwright's optional ``delay`` parameter.
        self._page.calls.append(("type", self.selector, text))

    async def inner_text(self, **kwargs: Any) -> str:
        # ``timeout`` kwarg is absorbed — production passes it for
        # the post-flow document_body read so a missing element
        # can't hang the run. Tests assert on the call only.
        self._page.calls.append(("inner_text", self.selector))
        return self._page.response_text

    async def all_inner_texts(self) -> list[str]:
        # Used by the post-flow document_body read for
        # ``.kix-paragraphrenderer`` (one element per paragraph) so
        # ``.first`` doesn't truncate the body to the leading line.
        self._page.calls.append(("all_inner_texts", self.selector))
        return [self._page.response_text]

    async def bounding_box(self) -> dict[str, float] | None:
        self._page.calls.append(("bounding_box", self.selector))
        return self._page.bbox

    async def get_attribute(self, name: str) -> str | None:
        self._page.calls.append(("get_attribute", self.selector, name))
        return self._page.attributes.get(self.selector, {}).get(name)


class _FakeKeyboard:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def press(self, key: str) -> None:
        self._page.calls.append(("keyboard.press", key))

    async def type(self, text: str, **kwargs: Any) -> None:
        # Records the typed text; absorbs the ``delay`` kwarg
        # production passes for human-pacing. Tests assert on the
        # text only — delay is variability, not behaviour.
        self._page.calls.append(("keyboard.type", text))


class _FakePage:
    """Stand-in for ``playwright.async_api.Page``.

    Returns scripted locators, records goto + screenshot calls.
    Tests configure response_text, bbox, and selective wait_for
    failures to drive each branch of the invoker."""

    # Sentinel — distinguishes "caller omitted bbox" (use default)
    # from "caller explicitly passed None" (test the fallback path).
    _BBOX_DEFAULT = object()

    def __init__(
        self,
        *,
        response_text: str = "side panel response",
        bbox: dict[str, float] | None | object = _BBOX_DEFAULT,
    ) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.response_text = response_text
        if bbox is _FakePage._BBOX_DEFAULT:
            self.bbox: dict[str, float] | None = {"x": 0, "y": 0, "width": 10, "height": 10}
        else:
            self.bbox = bbox  # type: ignore[assignment]
        # Map of selector -> exception to raise on wait_for, regardless
        # of state. Empty means "all wait_for calls succeed". Note:
        # the side-panel container is governed by ``container_open``
        # below instead; entries here for the container selector are
        # ignored.
        self.wait_for_failures: dict[str, Exception] = {}
        # Same idea but keyed by (selector, state) — lets a test
        # fail just the hidden-wait on a selector while leaving its
        # visible-wait succeeding (the loading-indicator edge tests
        # need this distinction).
        self.wait_for_failures_by_state: dict[tuple[str, str], Exception] = {}
        self.keyboard = _FakeKeyboard(self)
        self.screenshots: list[dict[str, Any]] = []
        # Open-state machine for the hardened side-panel flow.
        # ``container_open`` starts False; clicking the entry button
        # advances ``button_click_count`` and flips it once the
        # configured threshold is met.
        self.container_open: bool = False
        self.button_click_count: int = 0
        self.button_clicks_to_open: int = 1
        # selector -> {attribute_name: value} map for
        # ``locator.get_attribute(...)``. Default empty → every
        # attribute reads as None.
        self.attributes: dict[str, dict[str, str | None]] = {}
        # Failure-capture diagnostic surface: production code reads
        # ``page.url`` (attribute), ``page.title()`` (async), and
        # ``page.locator("body").inner_text()`` (already covered by
        # the locator fake). All three default to neutral values
        # that exercise the happy artefact-write path without
        # requiring tests to wire them up explicitly.
        self.url: str = "https://docs.google.com/document/d/fake"
        self._title: str = "Fake doc"
        # Lifecycle tracking — the session-reuse path closes the
        # page but NOT the surrounding context; tests assert on
        # these flags to lock that contract.
        self.page_closed: bool = False

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    async def goto(self, url: str, *, timeout: int = 0) -> None:
        self.calls.append(("goto", url, timeout))

    # ------------------------------------------------------------------
    # Event-listener surface — required by the traffic interceptor
    # (``page.on("response", handler)``). Stored as a no-op list so
    # tests don't have to wire up real event dispatch; the interceptor
    # opens its capture file but never sees any matching responses.
    # ------------------------------------------------------------------
    def on(self, event: str, handler: Any) -> None:
        self.calls.append(("on", event))

    def remove_listener(self, event: str, handler: Any) -> None:
        self.calls.append(("remove_listener", event))

    async def screenshot(self, **kwargs: Any) -> None:
        self.screenshots.append(kwargs)
        # Real Playwright writes the PNG; we simulate by touching the
        # file so the manifest copy-into-bundle path has something
        # non-zero on disk.
        path = kwargs.get("path")
        if path:
            Path(path).write_bytes(b"\x89PNG\r\n\x1a\nFAKE")

    async def title(self) -> str:
        return self._title

    async def close(self) -> None:
        # External-context path closes the page (but not the context).
        # Tests assert on this flag to lock that contract.
        self.page_closed = True


class _FakeContext:
    """Minimal Context double — owns a Page and closes cleanly.

    Two construction shapes:

    - Single-page (existing): ``_FakeContext(page)``. ``new_page`` is
      not expected to be called.
    - Session-reuse: ``_FakeContext(page_factory=lambda: <page>)``.
      ``new_page`` calls the factory and tracks how often. Lets the
      session-reuse tests assert "new_page was called once, context
      was NOT closed"."""

    def __init__(
        self,
        page: _FakePage | None = None,
        *,
        page_factory: Any = None,
    ) -> None:
        self.page = page
        self._page_factory = page_factory
        self.closed = False
        self.new_page_count = 0

    async def new_page(self) -> _FakePage:
        self.new_page_count += 1
        if self._page_factory is not None:
            return self._page_factory()
        # Fall back to the constructor-injected page if no factory.
        if self.page is None:
            raise RuntimeError("_FakeContext has no page configured")
        return self.page

    async def close(self) -> None:
        self.closed = True


async def _build_factory(page: _FakePage) -> Any:
    """Build a ``page_factory`` callable that hands the same page to
    every call so test assertions on ``page.calls`` accumulate."""

    async def factory() -> tuple[_FakeContext, _FakePage]:
        return _FakeContext(page), page

    return factory


# ----------------------------------------------------------------------
# Happy-path flow
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ui_mode_invoke_walks_side_panel_sequence(tmp_path: Path) -> None:
    """The invoker should: navigate, wait for the side panel
    button, click it, wait for input, type prompt, press Enter,
    wait for loading to disappear, wait for response visible, read
    inner_text, screenshot. The order is the demo contract."""
    page = _FakePage(response_text="Gemini says hello")
    factory = await _build_factory(page)

    response = await ui_mode_invoke(
        document_id="doc-abc",
        user_prompt="please summarise",
        trace_id="trace-1",
        output_dir=tmp_path,
        page_factory=factory,
    )

    assert response.text == "Gemini says hello"
    expected_screenshot = tmp_path / "screenshots" / "trace-1.png"
    assert response.raw["screenshot_path"] == str(expected_screenshot.resolve())
    assert response.raw["ui_mode"] is True
    assert response.raw["document_id"] == "doc-abc"
    # document_body is captured from the editor DOM before page
    # teardown. The fake page returns ``response_text`` for every
    # ``inner_text`` call, so it surfaces here too — what we're
    # locking is that the field is populated (not empty), which
    # is the precondition the SignalContext.document_body lazy
    # property depends on.
    assert response.raw["document_body"] == "Gemini says hello"
    # PNG was actually written to disk by the fake screenshot impl.
    assert expected_screenshot.exists()
    assert expected_screenshot.read_bytes().startswith(b"\x89PNG")

    # Call ordering — pull just the action names for a readable trace.
    # Filter out interception lifecycle (``on`` / ``remove_listener``)
    # since those are orthogonal to the side-panel sequence this test
    # asserts on.
    actions = [call[0] for call in page.calls if call[0] not in ("on", "remove_listener")]
    # goto first, then the fast-path container probe (misses), then
    # the entry-button wait + enabled check + force-click, then the
    # post-click container verification, then the input wait + click
    # + type, keyboard.press("Enter"), loading-indicator wait, response
    # read, screenshot.
    assert actions[0] == "goto"
    # Fast-path probe for an already-open panel (misses; 500ms probe).
    assert (
        "wait_for",
        SELECTORS["side_panel_container"],
        "visible",
        500,
    ) in page.calls
    # Entry button: visibility wait, aria-disabled probe, force-click.
    assert ("wait_for", SELECTORS["side_panel_button"], "visible", 20_000) in page.calls
    assert (
        "get_attribute",
        SELECTORS["side_panel_button"],
        "aria-disabled",
    ) in page.calls
    assert (
        "click",
        SELECTORS["side_panel_button"],
        {"force": True},
    ) in page.calls
    # Post-click container verification (uses the full timeout).
    assert (
        "wait_for",
        SELECTORS["side_panel_container"],
        "visible",
        20_000,
    ) in page.calls
    # "New conversation" reset fires after the panel opens — clears
    # Google's auto-summary so our prompt lands as turn 1 of a
    # fresh conversation. Best-effort, but the fake's wait_for
    # succeeds by default so the click lands.
    assert (
        "click",
        SELECTORS["side_panel_new_conversation"],
        {"force": True},
    ) in page.calls

    # Input flow: visible-wait → focus (programmatic, NOT click) →
    # keyboard.type. No click on the contenteditable anywhere —
    # clicking the side-panel combobox lands on a quick-suggestion
    # chip that auto-submits before our keystrokes arrive.
    assert ("wait_for", SELECTORS["side_panel_input"], "visible", 20_000) in page.calls
    assert ("focus", SELECTORS["side_panel_input"]) in page.calls
    assert ("keyboard.type", "please summarise") in page.calls
    assert not any(c[0] == "click" and c[1] == SELECTORS["side_panel_input"] for c in page.calls), (
        "input field must NOT be clicked — focus() is the safe path"
    )
    # Submission: prefer the send button (more reliable than Enter,
    # which the composer's IME / autocomplete sometimes swallows as
    # a newline). Falls back to Enter only if the send-button
    # selector misses — the fake-page test exposes the send button
    # by default, so we expect the click path here.
    assert (
        any(c[0] == "click" and SELECTORS["side_panel_send"] in c[1] for c in page.calls)
        or ("keyboard.press", "Enter") in page.calls
    )
    # Loading-indicator edges. _read_response confirms Gemini
    # started (visible, 5s probe), then waits for it to finish
    # (hidden, full timeout) before reading the response.
    assert (
        "wait_for",
        SELECTORS["side_panel_loading_indicator"],
        "visible",
        5_000,
    ) in page.calls
    assert (
        "wait_for",
        SELECTORS["side_panel_loading_indicator"],
        "hidden",
        20_000,
    ) in page.calls
    # Response read.
    assert ("inner_text", SELECTORS["side_panel_response"]) in page.calls
    # Screenshot clipped to the response bounding box.
    assert page.screenshots, "expected at least one screenshot call"
    clip = page.screenshots[0].get("clip")
    assert clip is not None and clip["width"] == 10


@pytest.mark.asyncio
async def test_ui_mode_invoke_screenshot_path_under_output_dir(tmp_path: Path) -> None:
    """``screenshot_path`` is always under ``<output_dir>/screenshots/``.
    The orchestrator relies on this to copy the PNG into the replay
    bundle without scanning."""
    page = _FakePage()
    factory = await _build_factory(page)

    response = await ui_mode_invoke(
        document_id="doc-x",
        user_prompt="p",
        trace_id="trace-2",
        output_dir=tmp_path,
        page_factory=factory,
    )

    screenshot = Path(response.raw["screenshot_path"])
    assert screenshot.parent == (tmp_path / "screenshots").resolve()
    assert screenshot.name == "trace-2.png"


# ----------------------------------------------------------------------
# Multi-turn — reset_conversation flag controls "New conversation" click,
# and same-doc navigation is skipped to keep the side panel alive.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_conversation_false_skips_new_conversation_click(
    tmp_path: Path,
) -> None:
    """When ``reset_conversation=False``, the invoker must NOT
    click "New conversation" — that would discard the in-flight
    Gemini conversation and break multi-turn Recipe chains
    (V3/V4 progressive disclosure)."""
    page = _FakePage(response_text="turn 2 reply")
    factory = await _build_factory(page)

    await ui_mode_invoke(
        document_id="doc-mt",
        user_prompt="follow-up prompt",
        trace_id="trace-mt-2",
        output_dir=tmp_path,
        page_factory=factory,
        reset_conversation=False,
    )

    # No click on the New conversation button. The default test
    # (test_ui_mode_invoke_walks_side_panel_sequence) locks the
    # opposite contract for reset_conversation=True.
    new_conv_clicks = [
        c
        for c in page.calls
        if c[0] == "click" and c[1] == SELECTORS["side_panel_new_conversation"]
    ]
    assert not new_conv_clicks, (
        "reset_conversation=False must not click 'New conversation' — "
        "multi-turn Recipe chains depend on the existing conversation "
        f"being preserved. Got: {new_conv_clicks}"
    )


@pytest.mark.asyncio
async def test_invoke_skips_navigation_when_already_on_target_doc(
    tmp_path: Path,
) -> None:
    """When the external page is already at the target doc URL
    (subsequent type_prompt step in a multi-turn Recipe), the
    invoker must skip ``page.goto`` — re-navigating reloads
    the doc and closes the Gemini side panel, which would erase
    any in-flight conversation."""
    page = _FakePage(response_text="turn 2 reply")
    # Pre-set the page URL to the target doc — simulates the
    # state after the first type_prompt step in a Recipe.
    page.url = "https://docs.google.com/document/d/doc-mt/edit"
    factory = await _build_factory(page)

    await ui_mode_invoke(
        document_id="doc-mt",
        user_prompt="follow-up prompt",
        trace_id="trace-mt-2",
        output_dir=tmp_path,
        page_factory=factory,
        reset_conversation=False,
    )

    goto_calls = [c for c in page.calls if c[0] == "goto"]
    assert not goto_calls, (
        "invoker must skip page.goto when the page is already on "
        "the target doc — re-navigation closes the side panel. "
        f"Got: {goto_calls}"
    )


@pytest.mark.asyncio
async def test_invoke_navigates_when_target_id_only_substring_of_other_doc(
    tmp_path: Path,
) -> None:
    """Regression: the target doc id appearing only as a substring of a
    DIFFERENT doc's URL (here in a query param) must NOT be read as 'already
    on the doc'. A substring check would skip navigation and strand the
    session on the wrong document; the invoker must navigate."""
    page = _FakePage(response_text="reply")
    # Viewing OTHER-doc-9; the target id "doc-mt" only rides in a query param.
    page.url = "https://docs.google.com/document/d/OTHER-doc-9/edit?ref=doc-mt"
    factory = await _build_factory(page)

    await ui_mode_invoke(
        document_id="doc-mt",
        user_prompt="p",
        trace_id="trace-wrong-target",
        output_dir=tmp_path,
        page_factory=factory,
        reset_conversation=False,
    )

    goto_calls = [c for c in page.calls if c[0] == "goto"]
    assert goto_calls, (
        "invoker must navigate when not actually on the target doc — the id "
        "appeared only in another doc's query string"
    )


def test_url_is_on_doc_matches_path_id_not_substrings() -> None:
    from advtest_ext.adapters.google_docs.ui_mode.invoker import _url_is_on_doc

    base = "https://docs.google.com/document/d/{id}/edit"
    # Exact path id, with or without suffixes → on the doc.
    assert _url_is_on_doc(base.format(id="abc123"), "abc123")
    assert _url_is_on_doc(base.format(id="abc123") + "?usp=sharing", "abc123")
    assert _url_is_on_doc("https://docs.google.com/document/d/abc123", "abc123")
    # Id only in a query, or a prefix of a different doc's id → NOT on it.
    assert not _url_is_on_doc(base.format(id="OTHER") + "?ref=abc123", "abc123")
    assert not _url_is_on_doc(base.format(id="abc123456"), "abc123")
    assert not _url_is_on_doc("", "abc123")


# ----------------------------------------------------------------------
# Failure modes — all surface as SidePanelUnavailable
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_side_panel_button_timeout_raises(tmp_path: Path) -> None:
    """If the entry-point button never appears, the invoker bails
    out with the canonical drift-pointer message."""
    page = _FakePage()
    page.wait_for_failures[SELECTORS["side_panel_button"]] = TimeoutError("selector not visible")
    factory = await _build_factory(page)

    with pytest.raises(SidePanelUnavailable) as exc_info:
        await ui_mode_invoke(
            document_id="d",
            user_prompt="p",
            trace_id="t",
            output_dir=tmp_path,
            page_factory=factory,
        )
    assert "TODO_BEFORE_PITCH.md item 4" in str(exc_info.value)


@pytest.mark.asyncio
async def test_loading_indicator_stuck_visible_raises(tmp_path: Path) -> None:
    """Gemini started generating but never finished — the loading
    indicator stays visible past the timeout. The invoker surfaces
    the canonical drift-pointer message.

    Uses the state-keyed failure map so the visible-wait still
    resolves (indicator did mount); only the hidden-wait raises."""
    page = _FakePage()
    page.wait_for_failures_by_state[(SELECTORS["side_panel_loading_indicator"], "hidden")] = (
        TimeoutError("still busy")
    )
    factory = await _build_factory(page)

    with pytest.raises(SidePanelUnavailable, match="Selector drift suspected"):
        await ui_mode_invoke(
            document_id="d",
            user_prompt="p",
            trace_id="t",
            output_dir=tmp_path,
            page_factory=factory,
        )


@pytest.mark.asyncio
async def test_loading_indicator_never_appears_falls_back_to_response_read(
    tmp_path: Path,
) -> None:
    """If the loading indicator never appears (fast reply, or a
    synchronous defence message that skips the thinking state),
    the invoker should skip the hidden-wait and go straight to
    reading the response container — no SidePanelUnavailable."""
    page = _FakePage(response_text="blocked: I can't help with that")
    page.wait_for_failures_by_state[(SELECTORS["side_panel_loading_indicator"], "visible")] = (
        TimeoutError("indicator never mounted")
    )
    factory = await _build_factory(page)

    response = await ui_mode_invoke(
        document_id="d",
        user_prompt="p",
        trace_id="t",
        output_dir=tmp_path,
        page_factory=factory,
    )

    assert response.text == "blocked: I can't help with that"
    # The visible-wait was attempted (and failed); the hidden-wait
    # must NOT have run.
    assert (
        "wait_for",
        SELECTORS["side_panel_loading_indicator"],
        "visible",
        5_000,
    ) in page.calls
    hidden_waits = [
        c
        for c in page.calls
        if c[0] == "wait_for"
        and c[1] == SELECTORS["side_panel_loading_indicator"]
        and c[2] == "hidden"
    ]
    assert not hidden_waits, "fallback path must skip the hidden-wait on the loading indicator"


@pytest.mark.asyncio
async def test_response_locator_timeout_raises(tmp_path: Path) -> None:
    """Response container missing — even if the loading indicator
    disappeared, we couldn't find the assistant message. Same
    failure class."""
    page = _FakePage()
    page.wait_for_failures[SELECTORS["side_panel_response"]] = TimeoutError(
        "response container missing"
    )
    factory = await _build_factory(page)

    with pytest.raises(SidePanelUnavailable):
        await ui_mode_invoke(
            document_id="d",
            user_prompt="p",
            trace_id="t",
            output_dir=tmp_path,
            page_factory=factory,
        )


# ----------------------------------------------------------------------
# Timeout / profile env overrides
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_timeout_overrides_default(tmp_path: Path) -> None:
    """The ``timeout_ms`` kwarg flows into every Playwright wait so
    the demo can dial it up when running on a slow network."""
    page = _FakePage()
    factory = await _build_factory(page)

    await ui_mode_invoke(
        document_id="d",
        user_prompt="p",
        trace_id="t",
        output_dir=tmp_path,
        page_factory=factory,
        timeout_ms=7_777,
    )

    # Every operational wait_for call in the trace should carry the
    # override value, not the default 20_000. Six fixed-timeout
    # probes are intentional exceptions:
    #   - "panel already open?" container probe (500ms)
    #   - "doc body painted?" body-render wait (15_000ms —
    #     _DOC_BODY_READY_MS) on any of the .kix-* selectors
    #   - "new conversation button visible?" probe (3_000ms)
    #   - "is auto-summary in flight?" loading-indicator visible
    #     probe in _wait_for_panel_idle (500ms)
    #   - "auto-summary settle" loading-indicator hidden wait
    #     in _wait_for_panel_idle (30_000ms — _AUTO_SUMMARY_SETTLE_MS)
    #   - "did Gemini start generating?" loading-indicator visible
    #     probe in _read_response (5_000ms)
    # All six are constants in the invoker by design — none should
    # scale with the operational timeout — so we exclude them rather
    # than weakening the contract on the rest.
    loading_sel = SELECTORS["side_panel_loading_indicator"]
    new_conv_sel = SELECTORS["side_panel_new_conversation"]
    send_sel = SELECTORS["side_panel_send"]
    doc_body_selectors = {".kix-paragraphrenderer", ".kix-page-content-wrap", ".kix-page"}
    wait_for_calls = [c for c in page.calls if c[0] == "wait_for"]
    operational = [
        c
        for c in wait_for_calls
        if not (c[1] == SELECTORS["side_panel_container"] and c[3] == 500)
        and not (c[1] in doc_body_selectors and c[3] == 4_000)
        and not (c[1] == new_conv_sel and c[3] == 1_500)
        and not (c[1] == send_sel and c[3] == 2_000)
        and not (c[1] == loading_sel and c[2] == "visible" and c[3] == 500)
        and not (c[1] == loading_sel and c[2] == "hidden" and c[3] == 5_000)
        and not (c[1] == loading_sel and c[2] == "visible" and c[3] == 5_000)
        # doc-suggestion resolution probe (_resolve_pending_suggestions):
        # a short constant probe for the V2/IRE Accept/Reject gate; like
        # the other probes above it must NOT scale with the operational
        # timeout.
        and not (c[3] == 800)
    ]
    assert operational
    assert all(c[3] == 7_777 for c in operational), (
        "expected every operational wait_for to pick up the explicit timeout"
    )


# ----------------------------------------------------------------------
# Hardened side-panel open flow
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_path_skips_button_click_when_panel_already_open(
    tmp_path: Path,
) -> None:
    """If ``side_panel_container`` is already visible at flow start —
    a previous run or the live operator left it open — the invoker
    must skip the entry-button click entirely. This is the safety net
    that lets the demo recover from a half-finished prior run without
    a manual reset."""
    page = _FakePage()
    page.container_open = True  # pretend the panel is already up
    factory = await _build_factory(page)

    await ui_mode_invoke(
        document_id="d",
        user_prompt="p",
        trace_id="t",
        output_dir=tmp_path,
        page_factory=factory,
    )

    # No entry-button activity of any kind: no visibility wait, no
    # aria-disabled probe, no click.
    button_actions = [
        c for c in page.calls if len(c) >= 2 and c[1] == SELECTORS["side_panel_button"]
    ]
    assert not button_actions, (
        "expected the hardened flow to short-circuit before touching "
        "the entry button when the panel was already open"
    )
    # The container probe still ran (it's how we detected the open
    # state), and the input wait still ran (the final gate).
    assert (
        "wait_for",
        SELECTORS["side_panel_container"],
        "visible",
        500,
    ) in page.calls
    assert (
        "wait_for",
        SELECTORS["side_panel_input"],
        "visible",
        20_000,
    ) in page.calls


@pytest.mark.asyncio
async def test_first_click_swallowed_triggers_one_retry(tmp_path: Path) -> None:
    """If the first entry-button click looks like it landed but the
    side panel never mounts, the invoker should wait briefly and
    click once more before raising. Models the actionability-race
    failure that motivated the retry layer."""
    page = _FakePage()
    page.button_clicks_to_open = 2  # first click is a no-op
    factory = await _build_factory(page)

    await ui_mode_invoke(
        document_id="d",
        user_prompt="p",
        trace_id="t",
        output_dir=tmp_path,
        page_factory=factory,
    )

    button_clicks = [
        c for c in page.calls if c[0] == "click" and c[1] == SELECTORS["side_panel_button"]
    ]
    assert len(button_clicks) == 2, (
        f"expected exactly one retry after a swallowed first click; "
        f"got {len(button_clicks)} click(s)"
    )
    # Both clicks must use force=True — the retry isn't a softer
    # fallback, it's the same bypass-actionability semantics.
    assert all(c[2] == {"force": True} for c in button_clicks)


@pytest.mark.asyncio
async def test_panel_never_opens_raises_after_retry(tmp_path: Path) -> None:
    """If the retry click also fails to open the panel, the invoker
    surfaces ``SidePanelUnavailable`` with the canonical message —
    we don't loop indefinitely."""
    page = _FakePage()
    page.button_clicks_to_open = 99  # panel never opens

    factory = await _build_factory(page)

    with pytest.raises(SidePanelUnavailable, match="Selector drift suspected"):
        await ui_mode_invoke(
            document_id="d",
            user_prompt="p",
            trace_id="t",
            output_dir=tmp_path,
            page_factory=factory,
            timeout_ms=50,  # keep the test fast — the post-click
            # container wait is what times out here
        )


@pytest.mark.asyncio
async def test_screenshot_falls_back_to_full_page_when_bbox_missing(
    tmp_path: Path,
) -> None:
    """If the response container has no bounding box (mid-animation
    race), the invoker falls back to a non-full-page screenshot so
    the replay bundle still gets a PNG."""
    page = _FakePage(bbox=None)
    factory = await _build_factory(page)

    await ui_mode_invoke(
        document_id="d",
        user_prompt="p",
        trace_id="t",
        output_dir=tmp_path,
        page_factory=factory,
    )

    assert page.screenshots, "expected fallback screenshot call"
    fallback = page.screenshots[0]
    assert "clip" not in fallback
    assert fallback.get("full_page") is False


# ----------------------------------------------------------------------
# Session reuse (external_context path)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_external_context_reuses_session_and_only_closes_page(
    tmp_path: Path,
) -> None:
    """When an ``external_context`` is supplied, ui_mode_invoke
    opens a page on it, drives the flow, and closes JUST THE PAGE on
    exit — the context itself stays open for the next caller. This
    is the contract that makes :class:`UiModeSession` valuable; if
    it ever regresses to closing the context, every multi-round
    attack loop would relaunch Chrome per call and re-trip Google's
    automation defences."""
    page = _FakePage(response_text="multi-round reply")
    context = _FakeContext(page=page)

    response = await ui_mode_invoke(
        document_id="doc-multi",
        user_prompt="p",
        trace_id="trace-multi",
        output_dir=tmp_path,
        external_context=context,
    )

    assert response.text == "multi-round reply"
    assert context.new_page_count == 1, "expected exactly one new_page on the shared context"
    # Page closed (we opened it), context not (caller owns it).
    assert page.page_closed is True
    assert context.closed is False


@pytest.mark.asyncio
async def test_external_context_failure_still_only_closes_page(
    tmp_path: Path,
) -> None:
    """Even when the inner flow raises ``SidePanelUnavailable``, the
    external context must survive — the next round of the attack
    loop needs to reuse it. Only the per-invocation page closes."""
    page = _FakePage()
    page.wait_for_failures[SELECTORS["side_panel_button"]] = TimeoutError("selector miss")
    context = _FakeContext(page=page)

    with pytest.raises(SidePanelUnavailable):
        await ui_mode_invoke(
            document_id="doc-fail",
            user_prompt="p",
            trace_id="trace-fail",
            output_dir=tmp_path,
            external_context=context,
        )

    assert page.page_closed is True
    assert context.closed is False, "failure must NOT tear down the shared session"


# ----------------------------------------------------------------------
# Failure-artefact capture
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_side_panel_failure_writes_diagnostic_artefacts(
    tmp_path: Path,
) -> None:
    """On ``SidePanelUnavailable``, a PNG + a text excerpt land
    under ``<output_dir>/failures/<trace_id>.{png,txt}``. Lets the
    operator distinguish selector drift from auth challenges /
    onboarding modals without re-running the whole flow."""
    page = _FakePage(response_text="not the reply — body excerpt fallback")
    page._title = "Unusual activity detected"
    page.url = "https://accounts.google.com/signin/v2/challenge"
    page.wait_for_failures[SELECTORS["side_panel_button"]] = TimeoutError("auth interstitial")
    factory = await _build_factory(page)

    with pytest.raises(SidePanelUnavailable):
        await ui_mode_invoke(
            document_id="doc-fail",
            user_prompt="p",
            trace_id="trace-fail",
            output_dir=tmp_path,
            page_factory=factory,
        )

    png = tmp_path / "failures" / "trace-fail.png"
    txt = tmp_path / "failures" / "trace-fail.txt"
    assert png.exists(), "expected forensic PNG under failures/"
    assert png.read_bytes().startswith(b"\x89PNG"), "PNG should not be empty"
    assert txt.exists(), "expected forensic text excerpt under failures/"
    body = txt.read_text(encoding="utf-8")
    # The text artefact captures the three signals that
    # discriminate the failure shapes: URL, title, body excerpt.
    assert "URL: https://accounts.google.com/signin/v2/challenge" in body
    assert "Title: Unusual activity detected" in body
    assert "body inner_text" in body


# ----------------------------------------------------------------------
# UiModeSession lifecycle
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ui_mode_session_close_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``UiModeSession.close()`` may be invoked multiple times (the
    orchestrator's finally block doesn't track whether the session
    ever opened). Second close must be a no-op, not an error."""
    # Stub out _launch_context so we don't actually try to launch
    # Playwright. The session's close path calls _close_context
    # which awaits ``context.close`` + ``pw.stop`` — both supplied
    # by the fake.
    closed = {"context": 0, "pw": 0}

    class _FakeDriver:
        async def stop(self) -> None:
            closed["pw"] += 1

    class _SessionFakeContext:
        async def close(self) -> None:
            closed["context"] += 1

        async def add_init_script(self, _script: str) -> None:
            return None

    async def _fake_launch(_profile: Path) -> Any:
        ctx = _SessionFakeContext()
        # _launch_context stashes the driver as __advtest_pw — mirror
        # that so UiModeSession can stop the driver on close.
        ctx.__advtest_pw = _FakeDriver()
        return ctx

    monkeypatch.setattr(_session_module, "launch_context", _fake_launch)

    session = UiModeSession(profile_dir=tmp_path / "profile")
    await session.ensure_open()
    # Calling ensure_open twice must NOT relaunch the context — the
    # whole point of the session is "one Chrome per run".
    await session.ensure_open()
    assert closed["context"] == 0  # not closed yet

    await session.close()
    assert closed["context"] == 1
    assert closed["pw"] == 1
    # Second close is a no-op (doesn't bump the counters).
    await session.close()
    assert closed["context"] == 1
    assert closed["pw"] == 1
