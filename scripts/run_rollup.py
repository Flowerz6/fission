#!/usr/bin/env python
"""Roll up a guide run: success rate + refusal-reason breakdown.

Reports THREE views of a ``run_guide.py`` output dir, because they disagree and
the disagreement is the point:

1. **Verdict** (target-side, judge-assigned) — what the run's judge concluded.
2. **Signal-verified** (judge-INDEPENDENT) — re-applies the guide's own
   ``success.signals`` / ``partial_success.signals`` to each captured reply. This
   is the honest disclosure rate: a mismatched judge (e.g. a retrieval judge on a
   disclosure attack) can score a real leak as "refused", but the guide's own
   regex/token tripwires still fire here.
3. **Candidate rejections** (attacker-side) — why proposed mutations were thrown
   out before execution (from ``tap_archive.json``), which tells you whether the
   attacker model is emitting valid mutation plans.

Usage:
    poetry run python scripts/run_rollup.py out/sheets_skill_leak_run2 --guide sheets_skill_leak
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _surface_text(spec: dict, trace: dict) -> str:
    """The text a signal scores against, honouring its ``where`` surface.
    ``model_thoughts`` reads the reasoning pane; everything else the reply."""
    where = str(spec.get("where") or "full_response")
    if where == "model_thoughts":
        rd = trace.get("raw_response_data") or {}
        val = rd.get("model_thoughts", "") if isinstance(rd, dict) else ""
        return val if isinstance(val, str) else ""
    if where == "request":
        val = trace.get("raw_request") or ""
        return val if isinstance(val, str) else json.dumps(val)
    return _reply_text(trace)


def _signal_fires(spec: dict, trace: dict) -> bool:
    text = _surface_text(spec, trace)
    kind = str(spec.get("kind") or "")
    if kind == "regex_match":
        pattern = spec.get("pattern")
        return bool(pattern) and re.search(str(pattern), text, re.IGNORECASE | re.DOTALL) is not None
    if kind == "token_appears":
        token = str(spec.get("token") or "")
        return bool(token) and token in text
    if kind == "canary_appears":
        canary = str(spec.get("canary") or spec.get("value") or "")
        return bool(canary) and canary in text
    return False


def _fired(signals: list[dict], trace: dict) -> list[str]:
    return [str(s.get("name") or s.get("kind")) for s in signals if _signal_fires(s, trace)]


def _reply_text(trace: dict) -> str:
    text = trace.get("raw_response") or ""
    if not isinstance(text, str):
        text = json.dumps(text)
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", type=Path, help="a run_guide.py --output-dir")
    ap.add_argument("--guide", help="guide id, to re-apply its success signals (recommended)")
    args = ap.parse_args()

    traces = sorted(glob.glob(str(args.run_dir / "run-*" / "traces" / "*.json")))
    if not traces:
        raise SystemExit(f"no traces under {args.run_dir}/run-*/traces/")

    success_signals: list[dict] = []
    partial_signals: list[dict] = []
    if args.guide:
        from advtest_ext.guides.corpus import load_guides

        guide = next((g for g in load_guides(_REPO_ROOT / "guides") if g.id == args.guide), None)
        if guide is not None:
            success_signals = list(guide.success.signals)
            partial_signals = list(guide.partial_success.signals) if guide.partial_success else []

    verdicts: collections.Counter = collections.Counter()
    n = sig_success = sig_partial = with_thoughts = empty_read = 0
    sig_hits: collections.Counter = collections.Counter()
    for path in traces:
        trace = json.loads(Path(path).read_text())
        n += 1
        verdicts[str(trace.get("verdict"))] += 1
        # An empty reply is a UI/selector failure (SidePanelUnavailable), NOT a
        # model refusal — the harness never got text to score. Track separately so
        # the refusal/success rates aren't polluted by infra failures.
        if not _reply_text(trace).strip():
            empty_read += 1
        fired = _fired(success_signals, trace)
        if fired:
            sig_success += 1
            sig_hits.update(fired)
        elif _fired(partial_signals, trace):
            sig_partial += 1
        rd = trace.get("raw_response_data") or {}
        if isinstance(rd, dict) and rd.get("model_thoughts"):
            with_thoughts += 1

    # Attacker-side rejections from the archive.
    rej: collections.Counter = collections.Counter()
    attempted = successful = 0
    for arch_path in glob.glob(str(args.run_dir / "tap_archive.json")):
        arch = json.loads(Path(arch_path).read_text())
        for inner in arch.values() if isinstance(arch, dict) else []:
            if not isinstance(inner, dict):
                continue
            attempted += len(inner.get("attempted", []) or [])
            successful += len(inner.get("successful_cells", []) or [])
            for r in inner.get("rejections", []) or []:
                for reason in r.get("reasons", []) or []:
                    rej[reason] += 1

    pct = lambda k: f"{100 * k / n:5.1f}%" if n else "  n/a"
    print(f"\n=== {args.run_dir}  ({n} nodes) ===\n")
    print("Verdict (judge-assigned):")
    for v, c in verdicts.most_common():
        print(f"  {c:3d}  {pct(c)}  {v}")
    print(f"\nSignal-verified (judge-independent, guide's own oracle):")
    print(f"  {sig_success:3d}  {pct(sig_success)}  SUCCESS  (a final success signal fired)")
    print(f"  {sig_partial:3d}  {pct(sig_partial)}  PARTIAL  (only a partial signal fired)")
    if sig_hits:
        print("  fired signals:", dict(sig_hits))
    if verdicts and sig_success and not any("succ" in v.lower() for v in verdicts):
        print("  ⚠  verdict says 0 successes but signals fired — judge/oracle mismatch.")
    real = n - empty_read
    print(f"\nEmpty-read / infra fail (SidePanelUnavailable): {empty_read}/{n} nodes"
          + (f"  → {real} nodes actually got a reply" if empty_read else ""))
    if real and empty_read:
        print(f"  signal-verified over nodes-with-a-reply: {sig_success}/{real} "
              f"= {100 * sig_success / real:.1f}%")
    print(f"\nModel-thoughts captured: {with_thoughts}/{n} nodes")
    print(f"\nCandidate mutations (attacker-side): {attempted} attempted, {successful} successful cells")
    if rej:
        print("Rejection reasons:")
        for reason, c in rej.most_common():
            print(f"  {c:3d}  {reason}")
    print()


if __name__ == "__main__":
    main()
