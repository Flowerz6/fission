"""Shared exceptions for Workspace ui_mode adapters."""

from __future__ import annotations

from typing import NoReturn

# Errors returned by result codes / verdicts.
BOT_DETECTED = "bot_detected"
SELECTOR_DRIFT = "selector_drift"
ATTACK_ERROR = "attack_error"


class SidePanelUnavailable(RuntimeError):  # noqa: N818 — public-contract name
    """Raised when the Gemini side-panel automation can't complete.

    Covers every "the DOM didn't look the way we expected" case (button
    missing, input not focusable, loading never finishes, response
    container absent). The message is intentionally a single canonical
    string so the demo operator knows exactly where to look — selector
    drift is the dominant failure mode for headed Workspace automation.

    Shared across product invokers (Gmail, Google Docs, Outlook,
    future Sheets / Calendar / Slides) so the orchestrator's catch
    can be a single ``except SidePanelUnavailable`` no matter which
    adapter is driving.

    ``error_kind`` is carried on the exception so the orchestrator can label the
    trace without importing this module (avoids a core→adapters dependency)."""

    error_kind: str = SELECTOR_DRIFT


class AntiAutomationDetected(SidePanelUnavailable):
    """Raised when the failure looks like Google blocked the *bot*, not the attack.

    A subclass of :class:`SidePanelUnavailable` so existing ``except
    SidePanelUnavailable`` handlers keep catching it, but distinct so the
    orchestrator can label the trace ``error_kind='bot_detected'``. This matters
    for the seed-reliability ledger: a bot-blocked session is *inconclusive*
    (Google never let the exploit run), so it must NOT count toward a seed's
    attack-failure total and must never trigger discard. Selector drift and a
    genuinely-refused attack are different failures and are counted differently.
    """

    error_kind: str = BOT_DETECTED


# --------------------------------------------------------------------------
# Anti-automation detection helpers
# --------------------------------------------------------------------------

# Substrings Google's anti-automation / account-verification interstitials use.
# Matched case-insensitively against page text or URL. Deliberately conservative:
# a false positive would wrongly excuse a real attack failure, so only strings
# that are specific to the block page belong here.
_ANTI_AUTOMATION_MARKERS: tuple[str, ...] = (
    "unusual activity",
    "verify it's you",
    "verify it’s you",  # noqa: RUF001 — curly-apostrophe variant is deliberate
    "confirm you're not a robot",
    "confirm you’re not a robot",  # noqa: RUF001 — curly-apostrophe variant is deliberate
    "i'm not a robot",
    "recaptcha",
    "/sorry/index",  # google.com/sorry/... block URL
    "detected unusual traffic",
    "automated queries",
    "suspicious activity",
    "couldn't sign you in",
    "this browser or app may not be secure",
)


def looks_like_anti_automation(text: str | None) -> bool:
    """True if ``text`` (page body or URL) carries an anti-automation marker.

    Invokers call this on the DOM/URL they already capture on failure and, when
    it returns True, raise :class:`AntiAutomationDetected` instead of a plain
    :class:`SidePanelUnavailable`. Pure string scan, no browser, so it is unit
    testable against synthetic interstitial text."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _ANTI_AUTOMATION_MARKERS)


async def raise_side_panel_failure(page: object, cause: BaseException, message: str) -> NoReturn:
    """Raise the right side-panel error for a DOM failure, sniffing for a bot wall.

    Call this from an invoker's ``except`` block instead of raising
    ``SidePanelUnavailable`` directly. It reads the page's text and URL (duck-typed,
    no Playwright import) and, if either carries an anti-automation marker, raises
    :class:`AntiAutomationDetected` so the trace is labelled ``bot_detected`` (an
    inconclusive session) rather than a counted attack failure. Otherwise it raises
    a plain :class:`SidePanelUnavailable` (selector drift). Reading the page is
    best-effort: if the page itself is unusable we fall back to selector drift."""
    text = ""
    try:
        content = getattr(page, "content", None)
        if content is not None:
            text += str(await content())
        url = getattr(page, "url", None)
        if url is not None:
            text += " " + str(url)
    except Exception:  # pragma: no cover - page may be dead; treat as selector drift
        text = ""
    if looks_like_anti_automation(text):
        raise AntiAutomationDetected(message) from cause
    raise SidePanelUnavailable(message) from cause


def classify_error_kind(exc: BaseException) -> str:
    """Map an execution exception to a coarse ``error_kind`` for the ledger.

    - :class:`AntiAutomationDetected` → ``bot_detected`` (inconclusive session)
    - any other :class:`SidePanelUnavailable` → ``selector_drift`` (harness fault)
    - anything else → ``attack_error`` (unexpected failure during the attempt)

    A ``selector_drift`` still means the exploit never actually ran, so like
    ``bot_detected`` it should not be scored as a *refused* attack; only the
    ledger's discard rule decides how each kind is counted."""
    if isinstance(exc, AntiAutomationDetected):
        return BOT_DETECTED
    if isinstance(exc, SidePanelUnavailable):
        return SELECTOR_DRIFT
    return ATTACK_ERROR


__all__ = [
    "ATTACK_ERROR",
    "BOT_DETECTED",
    "SELECTOR_DRIFT",
    "AntiAutomationDetected",
    "SidePanelUnavailable",
    "classify_error_kind",
    "looks_like_anti_automation",
    "raise_side_panel_failure",
]
