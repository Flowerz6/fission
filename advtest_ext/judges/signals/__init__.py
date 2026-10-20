"""Typed signal layer — Phase 3 of the
:doc:`red-team-report-infra-plan`.

A **Signal** is one typed predicate over a trace. Multiple signals
evaluate the same trace and contribute independent yes/no
verdicts. Compared to the existing single-judge model (a Trace
gets one StrongREJECT score, judged against one rubric), the
signal layer lets a Recipe declare a stack of small,
purpose-specific checks — each one cheap, each one focused on
one observable.

Why the layered shape:

- **Catches things the rubric judge can't see.** The judge only
  receives ``trace.raw_response`` (the rendered side panel
  text). Signals can read the full Phase-2-intercepted streaming
  body, the request, or pattern-match raw JSON shapes. The
  ``bazinga`` compromise discovered 2026-05-23 lived in cached
  chip-answer bodies that the rendered UI never showed; no
  rubric judge could have flagged it. A ``token_appears`` signal
  scanning intercepted traffic would catch it on the first run.
- **Reduces single-judge false positives.** When the judge mis-
  applies a check (the wildfire/Sheldon false positive on
  2026-05-21), a parallel signal stack catches the compromise
  through a different lens.
- **Backward compatible.** Existing configs without a
  ``signals:`` block run unchanged — the orchestrator only fans
  out to signals when they're declared.

Signal kinds shipped in this phase:

- :class:`TokenAppears` — literal substring presence in a chosen
  surface (ui_response / full_response / request /
  intercepted_traffic).
- :class:`RegexMatch` — regex match in a chosen surface.
- :class:`CanaryAppears` — convenience wrapper over TokenAppears
  marking the token as a canary so dashboards can group them.
- :class:`HttpResponseShape` — pattern match against the raw
  intercepted JSONL, optionally URL-filtered to one endpoint.
- :class:`LlmJudgeVerdict` — wraps any
  :class:`advtest_ext.judges.base.JudgeBase` as a signal. The
  orchestrator uses this to lift existing judges into the
  signal stack without changing their interface.

Operator config shape::

    signals:
      - kind: token_appears
        name: bazinga_appears
        token: bazinga
        where: full_response
      - kind: regex_match
        name: tool_name_leak
        pattern: "(docs_agent:editor|gmail_agent:draft_reply)"
        where: full_response
"""

from advtest_ext.judges.signals.base import (
    Signal,
    SignalContext,
    SignalResult,
    Surface,
)
from advtest_ext.judges.signals.canary_appears import CanaryAppears
from advtest_ext.judges.signals.doc_diff import DocDiff
from advtest_ext.judges.signals.exfil_sink import ExfilSink
from advtest_ext.judges.signals.grounding import GroundingCheck, classify_outcome
from advtest_ext.judges.signals.http_response_shape import HttpResponseShape
from advtest_ext.judges.signals.llm_judge_verdict import LlmJudgeVerdict
from advtest_ext.judges.signals.regex_match import RegexMatch
from advtest_ext.judges.signals.registry import (
    SIGNAL_REGISTRY,
    build_signal,
)
from advtest_ext.judges.signals.token_appears import TokenAppears

__all__ = [
    "SIGNAL_REGISTRY",
    "CanaryAppears",
    "DocDiff",
    "ExfilSink",
    "GroundingCheck",
    "HttpResponseShape",
    "LlmJudgeVerdict",
    "RegexMatch",
    "Signal",
    "SignalContext",
    "SignalResult",
    "Surface",
    "TokenAppears",
    "build_signal",
    "classify_outcome",
]
