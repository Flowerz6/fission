"""Signal ABC, result dataclass, and the shared evaluation context.

Every signal subclass implements one async ``evaluate`` method
that reads whatever it needs off the :class:`SignalContext` and
returns a :class:`SignalResult`. The orchestrator collects each
signal's result into a per-trace stack and persists them as
``SignalRecord`` rows alongside the existing ``JudgeScore`` rows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:  # pragma: no cover
    from advtest_ext.core.models import AttackTrace, Behavior


class Surface(StrEnum):
    """The observable surfaces a signal can read.

    The operator picks one per signal in the config (``where:``).
    Surfaces differ in fidelity:

    - ``ui_response`` — what Playwright managed to read from the
      rendered side panel. Fast, but often truncated mid-stream
      for long responses. Use for short / synchronous responses.
    - ``full_response`` — the assembled response extracted from
      Phase-2-intercepted streamGenerate bodies. Always the
      complete response; falls back to ``ui_response`` when no
      intercepted capture is available. **Default for most
      signals.**
    - ``request`` — the user-typed prompt
      (``trace.raw_request``). Useful for signals that need to
      verify what the operator actually submitted, not what the
      model returned.
    - ``intercepted_traffic`` — the raw JSONL capture as a
      string. For signals that want to grep across the entire
      file, including non-streamGenerate endpoints
      (quotaSummary, telemetry). Required for the V3-style
      stream-only leaks the Google Red Team Report documented.
    - ``document_body`` — content of the Google Doc the Recipe
      operated on. Required for V2-style attacks where the
      success criterion is "canary appears inside the doc body
      after the IRE writes it" (the canary is in the doc, not
      in any model response). **Phase 6.x stub:** returns empty
      string today; Drive-API fetch lands with the Runner-side
      signal evaluation work. Signals using this surface
      validate at dry-run but won't fire until the fetch is
      wired up.
    """

    UI_RESPONSE = "ui_response"
    FULL_RESPONSE = "full_response"
    REQUEST = "request"
    INTERCEPTED_TRAFFIC = "intercepted_traffic"
    DOCUMENT_BODY = "document_body"


class SignalResult:
    """Outcome of one signal evaluation.

    ``fired`` is the binary verdict. ``reasoning`` is a one-line
    human-readable explanation rendered on the dashboard.
    ``evidence`` is a structured dict (match counts, the first
    matched substring, etc.) preserved verbatim in the
    SignalRecord row so the operator can see exactly what the
    signal saw.
    """

    __slots__ = ("evidence", "fired", "reasoning")

    def __init__(
        self,
        *,
        fired: bool,
        reasoning: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.fired = fired
        self.reasoning = reasoning
        self.evidence = evidence or {}

    def __repr__(self) -> str:
        return (
            f"SignalResult(fired={self.fired}, "
            f"reasoning={self.reasoning!r}, "
            f"evidence={self.evidence!r})"
        )


class SignalContext:
    """Bundle of everything a signal might read from a trace.

    Constructed once per trace by the orchestrator and reused
    across every signal in the stack. Lazy properties so signals
    that only need ``ui_response`` don't pay the cost of parsing
    a 600 KB intercepted body.
    """

    def __init__(self, *, trace: AttackTrace, behavior: Behavior) -> None:
        self.trace = trace
        self.behavior = behavior
        self._full_response: str | None = None
        self._intercepted_path: Path | None | _Sentinel = _UNSET
        self._intercepted_text: str | None = None

    @property
    def ui_response(self) -> str:
        """Text Playwright read from the rendered side panel. May
        be truncated for streaming responses."""
        return getattr(self.trace, "raw_response", "") or ""

    @property
    def request(self) -> str:
        """The user prompt typed into the side panel."""
        return getattr(self.trace, "raw_request", "") or ""

    @property
    def intercepted_path(self) -> Path | None:
        """Path to the per-trace Workspace Gemini JSONL capture
        (Phase 2). ``None`` for api_mode traces, when interception
        was disabled, or for pre-2026-05-23 traces."""
        if self._intercepted_path is _UNSET:
            raw = getattr(self.trace, "raw_response_data", None) or {}
            path_str = raw.get("intercepted_traffic_path")
            self._intercepted_path = Path(path_str) if path_str else None
        return self._intercepted_path  # type: ignore[return-value]

    @property
    def full_response(self) -> str:
        """Assembled response extracted from intercepted
        streamGenerate bodies. Falls back to ``ui_response`` when
        no intercepted capture exists or the extraction yields
        nothing. Cached after first read."""
        if self._full_response is not None:
            return self._full_response
        path = self.intercepted_path
        if path is None or not path.exists():
            self._full_response = self.ui_response
        else:
            # Deferred import — extractor module pulls in
            # storage-aware deps; signals shouldn't drag those in
            # at import time.
            from advtest_ext.dashboard.intercepted_extract import (
                extract_full_response,
            )

            self._full_response = extract_full_response(path) or self.ui_response
        return self._full_response

    @property
    def intercepted_traffic(self) -> str:
        """Raw JSONL capture as a string, for signals that want to
        grep / pattern-match across the full file (all endpoints,
        not just streamGenerate). Empty string when no capture."""
        if self._intercepted_text is not None:
            return self._intercepted_text
        path = self.intercepted_path
        if path is None or not path.exists():
            self._intercepted_text = ""
        else:
            try:
                self._intercepted_text = path.read_text(encoding="utf-8")
            except OSError:
                self._intercepted_text = ""
        return self._intercepted_text

    @property
    def document_body(self) -> str:
        """Body content of the Google Doc the Recipe operated on.

        Sourced from the Playwright editor DOM during the Recipe
        Runner's UI invocation (``_read_doc_body_text`` reads
        ``inner_text`` from the live page before teardown) and
        stashed on the persisted Trace's
        ``raw_response_data['document_body']``. Empty string when
        no UI step ran or the editor was unreachable.
        """
        raw = getattr(self.trace, "raw_response_data", None) or {}
        value = raw.get("document_body", "")
        return value if isinstance(value, str) else ""

    def surface(self, where: Surface | str) -> str:
        """Return the text content of the named surface. Helper so
        each signal subclass doesn't reimplement the dispatch."""
        if isinstance(where, str):
            try:
                where = Surface(where)
            except ValueError as exc:
                valid = ", ".join(s.value for s in Surface)
                raise ValueError(f"unknown surface {where!r}; expected one of: {valid}") from exc
        if where is Surface.UI_RESPONSE:
            return self.ui_response
        if where is Surface.FULL_RESPONSE:
            return self.full_response
        if where is Surface.REQUEST:
            return self.request
        if where is Surface.INTERCEPTED_TRAFFIC:
            return self.intercepted_traffic
        if where is Surface.DOCUMENT_BODY:
            return self.document_body
        return ""  # pragma: no cover — exhaustive above


class Signal(ABC):
    """A typed predicate over a trace.

    Subclasses set ``KIND`` (the registry key matching the
    operator's ``kind:`` config value) and implement
    :meth:`evaluate`. Every signal carries a ``name`` — the
    operator-supplied label shown on the dashboard so multiple
    signals of the same kind can be told apart.
    """

    KIND: ClassVar[str]

    def __init__(self, *, name: str) -> None:
        if not name:
            raise ValueError("signal name must be non-empty")
        self.name = name

    @abstractmethod
    async def evaluate(self, *, context: SignalContext) -> SignalResult:
        """Evaluate the signal against ``context`` and return a
        :class:`SignalResult`. Must not raise — wrap any
        kind-specific failures into ``SignalResult(fired=False,
        reasoning='error: ...')``."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r}, kind={self.KIND!r})"


# ----------------------------------------------------------------------
# Internal sentinel for "not-yet-computed" lazy properties. Using a
# sentinel distinguishes "haven't tried" from "tried and got None".
# ----------------------------------------------------------------------


class _Sentinel:
    __slots__ = ()


_UNSET: _Sentinel = _Sentinel()
