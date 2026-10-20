"""Post-injection tenant-state observation.

Through a browser we cannot read the agent's tool calls, so we INFER an action
objective's effect from observable deltas in the operator's OWN test tenant:
new drafts, sent mail, file shares, created files, doc comments, and hits on the
operator-owned exfil sink. This layer only READS tenant state and diffs it
against a pre-run baseline; it generates no payloads.

Modules:

* :mod:`model` — :class:`SideEffectObservation` (per-channel DELTAs + status) and
  the typed item records / :class:`TenantState` snapshot they diff from.
* :mod:`baseline` — snapshot-before / diff-after, plus fixture reset hooks.
* :mod:`workspace` — the live observer: reuses the existing Playwright
  page/context the integration contract names and prefers the read-only Google
  API path (``adapters._auth.get_service``); never opens a second browser.
* :mod:`outcome` — routes an observation FAILURE through
  ``adapters._ui_mode_common.errors.classify_error_kind`` →
  ``core.orch_trace_meta.run_outcome`` so flaky Playwright reads become
  ``invalid_technical``, and keeps ``observed_no_effect`` distinct from
  ``observation_failed``.
* :mod:`mock` — a scripted observer (and a tool-call→delta bridge for the mock
  target) so the whole layer + Part-C scorers are CI-testable with no live tenant.
"""

from __future__ import annotations

from advtest_ext.observe.baseline import (
    diff_observation,
    setup_fixture,
    snapshot_baseline,
    teardown_fixture,
)
from advtest_ext.observe.model import (
    CHANNELS,
    CommentItem,
    DraftItem,
    FileCreated,
    FileShare,
    MailItem,
    ObservationStatus,
    SideEffectObservation,
    SinkHit,
    TenantState,
)
from advtest_ext.observe.outcome import (
    ObservationOutcome,
    classify_observation,
    observation_run_outcome,
)

__all__ = [
    "CHANNELS",
    "CommentItem",
    "DraftItem",
    "FileCreated",
    "FileShare",
    "MailItem",
    "ObservationOutcome",
    "ObservationStatus",
    "SideEffectObservation",
    "SinkHit",
    "TenantState",
    "classify_observation",
    "diff_observation",
    "observation_run_outcome",
    "setup_fixture",
    "snapshot_baseline",
    "teardown_fixture",
]
