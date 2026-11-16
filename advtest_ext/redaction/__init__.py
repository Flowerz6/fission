"""Redaction gate: store full at rest, send only redacted projections to the third-party model.

The single chokepoint on the three egress channels (seed-at-upload, evidence-at-ingest,
exemplar/egress). See :mod:`advtest_ext.redaction.gate`.
"""

from __future__ import annotations

from advtest_ext.redaction.gate import (
    RedactionResult,
    guard,
    redact,
    redact_evidence,
    redact_exemplar,
    redact_for_egress,
    scrub_seed,
)

__all__ = [
    "RedactionResult",
    "guard",
    "redact",
    "redact_evidence",
    "redact_exemplar",
    "redact_for_egress",
    "scrub_seed",
]
