"""Post-run digest hook: feed integrated strategy-loop winners back to the
markdown strategy library — the library-learning half of PIMiner's flywheel,
wired to the normal run path.

This is the counterpart to the orchestrator's per-trace *seed* harvest
(``core/orchestrator.harvest_verified_variants``, which banks guide
variants into the DB). The digest is inherently a *post-run, whole-run*
operation — it dedups each winner against the whole library before
classifying it novel / fits / widens — so it runs once after a run
completes rather than per trace, and it lives in the strategy layer so the
dependency direction stays caller -> strategy -> core (nothing in core
imports strategy).

Gated, and off unless asked for: it needs the ``CLAUDE_DIGESTER`` role
(5.x models refuse the cataloguing task; point it at claude-opus-4-8) and
writes *candidate* library entries, mirroring the harvest flywheel's
"sharpen, don't clutter" discipline. Winners are taken only from
signal-verified successes by default, the same bar the run UI shows.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from structlog import get_logger

from advtest_ext.strategy.digester import Digester, Hit, ProviderDecider
from advtest_ext.strategy.models import Attempt, Sample

logger = get_logger(__name__)

_ENABLE_ENV = "ADVTEST_DIGEST_STRATEGY"
_DEFAULT_LIBRARY = Path("advtest_ext") / "strategy" / "library"

# The metadata namespace the StrategyLoopAttack plugin stamps onto each
# yielded attempt (and which the orchestrator persists verbatim into
# ``Trace.extra_metadata``). Keep in lockstep with
# ``advtest_ext/attacks/strategy_loop.py``.
_META_KEY = "strategy_loop"


def digest_enabled() -> bool:
    """True when the post-run strategy digest is switched on (off by default
    — it is an LLM-backed, library-mutating step)."""
    return os.environ.get(_ENABLE_ENV, "0").lower() in {"1", "true", "yes"}


def _hit_from_trace(meta: dict[str, Any], *, final_output: str) -> Hit | None:
    """Reconstruct a :class:`Hit` from a persisted trace's strategy-loop
    metadata. Returns ``None`` when the block is missing the self-describing
    sample (older traces) rather than raising."""
    sample_fields = meta.get("sample")
    if not isinstance(sample_fields, dict):
        return None
    try:
        sample = Sample(**sample_fields)
    except Exception:  # pragma: no cover - defensive against schema drift
        return None
    winning = Attempt(
        iter=int(meta.get("iter", 0)),
        analysis=str(meta.get("analysis", "")),
        injection=str(meta.get("injection", "")),
        utility=True,
        security=True,
        final_output=final_output,
    )
    return Hit(
        sample=sample,
        winning=winning,
        routed_strategy_ids=list(meta.get("strategy_ids") or []),
    )


def extract_hits_from_traces(traces: list[Any]) -> list[Hit]:
    """Pull strategy-loop winners out of a run's persisted traces.

    A winner is a ``verdict == 'success'`` trace carrying the plugin's
    self-describing ``strategy_loop`` metadata. Signal verification is
    applied upstream by the caller (``maybe_digest_run``) so this stays a
    pure, DB-free transform the tests can drive with plain objects.
    """
    hits: list[Hit] = []
    for t in traces:
        if (getattr(t, "verdict", None) or "").lower() != "success":
            continue
        meta_all = getattr(t, "extra_metadata", None) or getattr(t, "metadata", None) or {}
        meta = meta_all.get(_META_KEY) if isinstance(meta_all, dict) else None
        if not isinstance(meta, dict):
            continue
        hit = _hit_from_trace(meta, final_output=str(getattr(t, "raw_response", "") or ""))
        if hit is not None:
            hits.append(hit)
    return hits


async def digest_traces(
    traces: list[Any],
    *,
    decider: Any,
    library_dir: str | Path = _DEFAULT_LIBRARY,
    audit_dir: str | Path | None = None,
) -> Any:
    """Digest the strategy-loop winners among ``traces`` into the library.

    Returns a :class:`~advtest_ext.strategy.digester.DigestReport` (with
    ``n_hits == 0`` when nothing qualified). ``decider`` is any
    :class:`~advtest_ext.strategy.digester.Decider` — a
    :class:`ProviderDecider` in production, a ``ScriptedDecider`` in tests.
    """
    hits = extract_hits_from_traces(traces)
    return await Digester(decider).digest_hits(hits, library_dir=library_dir, audit_dir=audit_dir)


def decider_from_config(
    config_path: str | Path,
    role: str = "CLAUDE_DIGESTER",
    *,
    max_tokens: int | None = 4096,
) -> ProviderDecider:
    """Build the LLM decider from a fission provider config role."""
    from advtest_ext.providers.factory import load_provider_from_yaml

    provider = load_provider_from_yaml(config_path, role)
    return ProviderDecider(provider, max_tokens=max_tokens)


async def maybe_digest_run(
    session_factory: Any,
    run_id: str,
    *,
    config_path: str | Path,
    library_dir: str | Path = _DEFAULT_LIBRARY,
    audit_dir: str | Path | None = None,
    role: str = "CLAUDE_DIGESTER",
    require_verified_signal: bool = True,
) -> Any | None:
    """Run the post-run strategy digest for ``run_id`` — the one call a run
    caller wires in. A no-op (returns ``None``) when the gate is off, the
    run has no strategy-loop winners, or the decider can't be built.

    Best-effort by contract: a failure here must never fail the run, so the
    caller should treat exceptions as non-fatal (this logs and returns None).
    """
    if not digest_enabled():
        return None

    from advtest_ext.storage import (
        list_signals_for_trace,
        list_traces_for_run,
        session_scope,
    )

    try:
        with session_scope(session_factory) as s:
            rows = list_traces_for_run(s, run_id)
            winners: list[Any] = []
            for row in rows:
                if (row.verdict or "").lower() != "success":
                    continue
                if require_verified_signal:
                    sigs = list_signals_for_trace(s, row.id)
                    if not any(sig.fired and sig.kind != "llm_judge_verdict" for sig in sigs):
                        continue  # oracle-only success — not a verified winner
                winners.append(row)
            # Detach the plain fields we need before the session closes.
            snapshots = [
                _TraceSnapshot(
                    verdict=row.verdict,
                    extra_metadata=dict(row.extra_metadata or {}),
                    raw_response=row.raw_response or "",
                )
                for row in winners
            ]
    except Exception as exc:  # pragma: no cover - defensive boundary
        logger.warning("strategy_digest.load_failed", run_id=run_id, error=str(exc))
        return None

    if not snapshots:
        return None

    try:
        decider = decider_from_config(config_path, role)
        report = await digest_traces(
            snapshots, decider=decider, library_dir=library_dir, audit_dir=audit_dir
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        logger.warning("strategy_digest.failed", run_id=run_id, error=str(exc))
        return None

    logger.info(
        "strategy_digest.done",
        run_id=run_id,
        hits=report.n_hits,
        created=len(report.created),
        appended=len(report.appended),
        widened=len(report.widened),
    )
    return report


class _TraceSnapshot:
    """A detached read-only view of the trace fields the digest needs, so
    reconstruction happens after the DB session has closed."""

    __slots__ = ("extra_metadata", "raw_response", "verdict")

    def __init__(self, *, verdict: str | None, extra_metadata: dict[str, Any], raw_response: str) -> None:
        self.verdict = verdict
        self.extra_metadata = extra_metadata
        self.raw_response = raw_response
