"""Diagnose a judge call end to end, on one real trace.

``advtest rescore`` reports that a judge failed; it cannot tell you WHY,
because everything that would explain it — the exact prompt, the raw API
response, the stop reason, where the output tokens went — is discarded
by the time a ``JudgeScore`` comes back. Guessing between the candidate
causes costs a full re-scoring run per guess.

This prints the whole exchange for a single trace:

* the stored trace (verdict, response length, which L0 signals fired)
* the EXACT prompt the judge builds, and its token count
* the raw Anthropic response — ``stop_reason``, every content block and
  its type, token usage split input/output
* the parse result, and the score that would follow

The three failure modes it separates:

``stop_reason="max_tokens"``
    Thinking consumed the budget and the answer never got written.
    Fix: raise ``max_tokens`` on the judge role, or disable thinking.
``stop_reason="refusal"``
    The JUDGE model's own safety classifiers declined to evaluate the
    transcript. These transcripts are jailbreak and system-prompt-leak
    content, so this is a live possibility, and no amount of extra
    budget fixes it. Fix: a fallback model for refused judge calls.
``stop_reason="end_turn"`` with empty text
    The model chose to say nothing — a prompt problem, not a plumbing
    one.

Read-only: touches no rows, writes no files.

    poetry run python scripts/diagnose_judge.py --errored
    poetry run python scripts/diagnose_judge.py --trace-id trace-c784e69a6f7e
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from advtest_ext.core.rescore import trace_row_to_domain
from advtest_ext.judges.decomposed_ipi import _PROMPT, parse_decomposition
from advtest_ext.judges.ipi_verdict import _extract_hidden_injection, _response_text
from advtest_ext.storage import (
    get_trace,
    list_signals_for_trace,
    make_engine,
    make_session_factory,
    session_scope,
)

_RULE = "=" * 72


def _find_errored(session, limit: int) -> list[str]:
    """Trace ids whose stored decomposed score looks like a failure.

    A judge failure is persisted as score 0.0 with a 'could not parse'
    reasoning, so it is findable after the fact even though the run that
    produced it is gone.
    """
    from sqlalchemy import text as sql

    rows = session.execute(
        sql(
            "SELECT trace_id FROM judge_scores "
            "WHERE judge_name LIKE '%decomposed%' "
            "AND reasoning LIKE 'could not parse%' LIMIT :n"
        ),
        {"n": limit},
    ).all()
    return [r[0] for r in rows]


def _longest_traces(session, limit: int) -> list[str]:
    """Fallback target set: the longest responses.

    Length is the best available proxy when nothing is marked failed —
    both candidate causes (a big prompt crowding the token budget, and a
    transcript with more leaked content in it) scale with it.
    """
    from sqlalchemy import text as sql

    rows = session.execute(
        sql(
            "SELECT id FROM traces WHERE raw_response IS NOT NULL "
            "ORDER BY LENGTH(raw_response) DESC LIMIT :n"
        ),
        {"n": limit},
    ).all()
    return [r[0] for r in rows]


async def _diagnose(session, trace_id: str, *, provider, max_tokens: int) -> None:
    from advtest_ext.providers.base import ChatMessage

    row = get_trace(session, trace_id)
    if row is None:
        print(f"!! no trace {trace_id}")
        return
    trace = trace_row_to_domain(row)
    signals = list_signals_for_trace(session, trace_id)
    fired = [s.name for s in signals if s.fired]

    print(f"\n{_RULE}\nTRACE {trace_id}")
    print(f"  stored verdict : {row.verdict}")
    print(f"  behaviour      : {row.behavior_id}")
    print(f"  response chars : {len(trace.raw_response)}")
    print(f"  L0 fired       : {fired or '(none)'}")

    prompt = _PROMPT.format(
        user_instruction=trace.raw_request[:2000],
        hidden_injection=_extract_hidden_injection(trace) or "(none found)",
        model_response=_response_text(trace),
    )
    print(f"  prompt chars   : {len(prompt)}  (~{len(prompt) // 4} tokens)")
    print(f"  max_tokens     : {max_tokens}")

    resp = await provider.complete(
        [ChatMessage(role="user", content=prompt)],
        max_tokens=max_tokens,
        temperature=0.0,
    )
    raw: dict[str, Any] = resp.raw or {}

    print("\n  --- RAW RESPONSE ---")
    print(f"  stop_reason    : {raw.get('stop_reason')!r}")
    print(f"  block_types    : {raw.get('block_types')}")
    print(f"  input tokens   : {resp.usage.prompt_tokens}")
    print(f"  output tokens  : {resp.usage.completion_tokens}")
    print(f"  text length    : {len(resp.text)}")
    print(f"  text           : {resp.text[:600]!r}")

    decomp = parse_decomposition(resp.text)
    print("\n  --- VERDICT ---")
    if decomp is None:
        print("  PARSE FAILED — this is the failure being diagnosed.")
        stop = raw.get("stop_reason")
        if stop == "max_tokens":
            print("  CAUSE: budget. Thinking consumed max_tokens before the")
            print("         answer was written. Raise max_tokens on")
            print("         CLAUDE_JUDGE, or disable thinking for judging.")
        elif stop == "refusal":
            print("  CAUSE: the JUDGE model declined to evaluate this")
            print("         transcript — its own safety classifiers, not a")
            print("         budget problem. More tokens will not help;")
            print("         needs a fallback model for refused judge calls.")
        else:
            print(f"  CAUSE: unclear — stop_reason={stop!r} with empty text.")
            print("         Not budget and not a refusal; suspect the prompt.")
    else:
        from advtest_ext.judges.decomposed_ipi import aggregate_score

        print(
            f"  attended={decomp.attended} complied={decomp.complied} "
            f"harmful_effect={decomp.harmful_effect} conf={decomp.confidence}"
        )
        print(f"  score          : {aggregate_score(decomp):.2f}")
        print(f"  reason         : {decomp.reason[:200]}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trace-id", action="append", default=[], help="Repeatable.")
    ap.add_argument(
        "--errored",
        action="store_true",
        help="Auto-select traces whose stored judge score is a parse failure.",
    )
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--db-url", default=os.environ.get("ADVTEST_DB_URL", "sqlite:///./advtest.db"))
    ap.add_argument("--config", default="configs/dev.yaml")
    ap.add_argument("--role", default="CLAUDE_JUDGE")
    ap.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Override the role's budget — sweep it to test the budget theory.",
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2

    from advtest_ext.providers import load_provider_from_yaml

    provider = load_provider_from_yaml(args.config, args.role)
    max_tokens = args.max_tokens or provider.config.max_tokens
    print(f"judge model: {provider.config.model_id}  max_tokens={max_tokens}")

    factory = make_session_factory(make_engine(args.db_url))
    with session_scope(factory) as session:
        targets = list(args.trace_id)
        if not targets:
            targets = _find_errored(session, args.limit) if args.errored else []
        if not targets:
            print("(no errored traces recorded — falling back to longest responses)")
            targets = _longest_traces(session, args.limit)

        for tid in targets:
            await _diagnose(session, tid, provider=provider, max_tokens=max_tokens)

    print(f"\n{_RULE}\ndone — nothing was written")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
