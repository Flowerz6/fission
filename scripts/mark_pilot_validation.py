#!/usr/bin/env python
"""Attach a reviewed pilot validation result to one terminal trace.

Use one episode ID per fresh target session. The evidence reference should
identify the saved action trace, review record, or immutable test artifact.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advtest_ext.delivery.validation import mark_trace_validation
from advtest_ext.storage import make_engine, make_session_factory, session_scope


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("ADVTEST_DB_URL", "sqlite:///./advtest.db"))
    parser.add_argument("--trace-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--evaluator-version", required=True)
    parser.add_argument("--evidence-ref", required=True)
    parser.add_argument(
        "--verification-source", choices=("human_review", "effect_verifier"), required=True
    )
    outcome = parser.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--effect-verified", action="store_true")
    outcome.add_argument("--effect-not-verified", action="store_true")
    args = parser.parse_args()

    factory = make_session_factory(make_engine(args.db_url))
    with session_scope(factory) as session:
        mark_trace_validation(
            session,
            trace_id=args.trace_id,
            expected_run_id=args.run_id,
            candidate_id=args.candidate_id,
            episode_id=args.episode_id,
            environment_id=args.environment_id,
            target_version=args.target_version,
            evaluator_version=args.evaluator_version,
            evidence_ref=args.evidence_ref,
            verification_source=args.verification_source,
            effect_verified=args.effect_verified,
        )
    print(f"Recorded pilot validation for trace {args.trace_id}.")


if __name__ == "__main__":
    main()
