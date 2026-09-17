"""Shared configuration for Workspace ui_mode adapters.

The Playwright-driven side-panel invokers under
:mod:`advtest_ext.adapters.gmail.ui_mode` and
:mod:`advtest_ext.adapters.google_docs.ui_mode` share an almost
identical set of environment-variable knobs and pacing constants.
Centralising them here keeps the two invokers in sync and makes
future products (Sheets, Calendar, Slides) one-import additions.

Operator-facing env vars
    ADVTEST_PLAYWRIGHT_PROFILE_DIR — Chromium persistent context dir
    ADVTEST_PLAYWRIGHT_TIMEOUT     — milliseconds for selector waits
    ADVTEST_INTERCEPT_TRAFFIC      — toggle Gemini API traffic capture
    ADVTEST_UI_MODE_HOLD_S         — post-response hold (demo affordance)
"""

from __future__ import annotations

import os
from pathlib import Path

# ----------------------------------------------------------------------
# Environment-variable names + defaults.
# ----------------------------------------------------------------------

PROFILE_ENV = "ADVTEST_PLAYWRIGHT_PROFILE_DIR"
TIMEOUT_ENV = "ADVTEST_PLAYWRIGHT_TIMEOUT"
INTERCEPT_ENV = "ADVTEST_INTERCEPT_TRAFFIC"
POST_RESPONSE_HOLD_ENV = "ADVTEST_UI_MODE_HOLD_S"
WINDOW_X_ENV = "ADVTEST_BROWSER_WINDOW_X"
WINDOW_Y_ENV = "ADVTEST_BROWSER_WINDOW_Y"
WINDOW_WIDTH_ENV = "ADVTEST_BROWSER_WINDOW_WIDTH"
WINDOW_HEIGHT_ENV = "ADVTEST_BROWSER_WINDOW_HEIGHT"

DEFAULT_PROFILE_DIR = Path.home() / ".advtest" / "playwright-profile"
DEFAULT_TIMEOUT_MS = 20_000
DEFAULT_POST_RESPONSE_HOLD_S = 6.0
DEFAULT_WINDOW_GEOMETRY = (640, 0, 1280, 1040)

# ----------------------------------------------------------------------
# Pacing constants — identical across products by design. Tweaking one
# value here changes timing for every adapter; the matching panel-open
# / submit / read flow already absorbs any product-specific slack via
# the readiness gates.
# ----------------------------------------------------------------------

# Demo affordance — Playwright pauses between actions. Set 0 in prod.
DEMO_SLOW_MO_MS = 0

# Fast-path probe: "is the side panel already open?". Short fixed
# timeout because this is a check-and-skip, not a wait.
PANEL_OPEN_PROBE_MS = 500

# Delay between a swallowed first click and the retry click.
OPEN_RETRY_DELAY_S = 1.0

# Poll cadence for the "button still aria-disabled" wait.
BUTTON_READY_POLL_S = 0.1

# Short window to confirm Gemini *started* generating (loading
# indicator appeared) before falling back to direct response wait.
LOADING_VISIBLE_PROBE_MS = 5_000

# Max wait for an in-flight auto-summary to settle before submitting
# our own prompt. The Sidekick panel auto-summarises the open doc /
# thread on first open; typing into the input while that's still
# running queues behind the auto-summary in a way that surfaces as
# "the prompt was submitted before the typing happened".
AUTO_SUMMARY_SETTLE_MS = 5_000

# Max wait for the main content pane to paint after navigation.
# Critical because Workspace shells (toolbar, Ask Gemini icon) load
# BEFORE the body content — clicking too early fires the auto-summary
# against an empty pane and Gemini's reply becomes "the document is
# empty" / "no thread loaded". Per-product readiness gates land on
# this same budget.
MAIN_PANE_READY_MS = 4_000

# Anti-automation: Playwright sets ``navigator.webdriver = true`` on
# every page, which is the dominant tell modern bot-detection scripts
# (Google's included) check first. The Chromium launch args strip the
# ``--enable-automation`` flag, but the JS property survives further
# up the stack. This init script runs on every new document and
# replaces the property with ``undefined``.
NAVIGATOR_WEBDRIVER_SHIM = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

# Human-pacing knobs — bounds on ``random.uniform`` draws so the
# behaviour varies within bounds across rounds / characters. Tuned
# for "looks like a human on a laptop" without making the demo
# agonisingly slow.
HUMAN_TYPING_DELAY_MS = (20, 60)  # per-character (Playwright keyboard.type delay)
HUMAN_HOVER_PAUSE_S = (0.05, 0.15)  # between hover and click
HUMAN_ACTION_PAUSE_S = (0.10, 0.30)  # between high-level steps

SELECTOR_DRIFT_MESSAGE = (
    "Selector drift suspected; see TODO_BEFORE_PITCH.md item 4 to update selectors."
)


# ----------------------------------------------------------------------
# Env-var resolvers — return resolved values with the documented
# defaults. Pure I/O helpers; safe to call freely.
# ----------------------------------------------------------------------


def resolve_window_geometry() -> tuple[int, int, int, int]:
    """Chrome outer-window geometry as ``(x, y, width, height)``.

    Defaults dock a 1280px-wide runner flush right on the 1920x1080
    demo display, leaving the dashboard visible on the left.
    """
    names = (WINDOW_X_ENV, WINDOW_Y_ENV, WINDOW_WIDTH_ENV, WINDOW_HEIGHT_ENV)
    values: list[int] = []
    for name, default in zip(names, DEFAULT_WINDOW_GEOMETRY, strict=True):
        raw = os.environ.get(name)
        try:
            value = int(raw) if raw else default
        except ValueError:
            value = default
        values.append(value)
    x, y, width, height = values
    return x, y, max(640, width), max(480, height)


def intercept_enabled() -> bool:
    """On/off flag for Workspace Gemini traffic interception.

    Default on. Set ``ADVTEST_INTERCEPT_TRAFFIC=0`` (or
    ``false`` / ``no`` / ``off``) to disable. The interceptor is
    observe-only and wrapped in try/except for safety, so leaving
    it on is the right default.
    """
    raw = os.environ.get(INTERCEPT_ENV, "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "")


def resolve_profile_dir() -> Path:
    """Persistent Chromium context dir, with ``~`` expansion."""
    raw = os.environ.get(PROFILE_ENV)
    return Path(raw).expanduser() if raw else DEFAULT_PROFILE_DIR


def resolve_timeout_ms() -> int:
    """Default selector-wait timeout (ms). Raises ``ValueError`` when
    the operator passes a non-integer override."""
    raw = os.environ.get(TIMEOUT_ENV)
    if not raw:
        return DEFAULT_TIMEOUT_MS
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{TIMEOUT_ENV} must be an integer (milliseconds), got {raw!r}") from exc


def resolve_post_response_hold_s() -> float:
    """Seconds to keep Gemini's response visible after capture, before
    the next attack moves on. Non-numeric values fall back to the
    default; ``0`` disables the hold; negatives clamp to 0."""
    raw = os.environ.get(POST_RESPONSE_HOLD_ENV)
    if raw is None:
        return DEFAULT_POST_RESPONSE_HOLD_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_POST_RESPONSE_HOLD_S
    return max(value, 0.0)
