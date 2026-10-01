"""Record reviewed effect evidence for independent pilot validation episodes."""

from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from advtest_ext.storage import get_trace

VerificationSource = Literal["human_review", "effect_verifier"]


def mark_trace_validation(
    session: Session,
    *,
    trace_id: str,
    expected_run_id: str,
    candidate_id: str,
    episode_id: str,
    environment_id: str,
    target_version: str,
    evaluator_version: str,
    evidence_ref: str,
    verification_source: VerificationSource,
    effect_verified: bool,
) -> None:
    """Attach a review result to one terminal trace, without changing its verdict.

    The caller must supply a stable candidate ID and a fresh-session episode ID.
    Repeating an identical record is safe; changing an existing review requires
    an explicit audit operation outside this helper.
    """
    fields = {
        "trace_id": trace_id,
        "expected_run_id": expected_run_id,
        "candidate_id": candidate_id,
        "episode_id": episode_id,
        "environment_id": environment_id,
        "target_version": target_version,
        "evaluator_version": evaluator_version,
        "evidence_ref": evidence_ref,
    }
    if any(not isinstance(value, str) or not value.strip() for value in fields.values()):
        raise ValueError("all validation identifiers and evidence_ref must be nonempty")
    if verification_source not in {"human_review", "effect_verifier"}:
        raise ValueError("unsupported verification source")
    if not isinstance(effect_verified, bool):
        raise ValueError("effect_verified must be a boolean")

    trace = get_trace(session, trace_id)
    if trace is None or trace.run_id != expected_run_id:
        raise ValueError("trace not found in the expected run")
    if trace.verdict == "error":
        raise ValueError("an error trace cannot be a validation episode")

    record = {
        "phase": "validation",
        "candidate_id": candidate_id,
        "episode_id": episode_id,
        "environment_id": environment_id,
        "target_version": target_version,
        "evaluator_version": evaluator_version,
        "evidence_ref": evidence_ref,
        "verification_source": verification_source,
        "effect_verified": effect_verified,
    }
    meta = trace.extra_metadata if isinstance(trace.extra_metadata, dict) else {}
    prior = meta.get("pilot_validation")
    if prior is not None and prior != record:
        raise ValueError("trace already has a different validation record")
    trace.extra_metadata = {**meta, "pilot_validation": record}
    session.flush()


__all__ = ["VerificationSource", "mark_trace_validation"]
