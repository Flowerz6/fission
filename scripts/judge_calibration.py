#!/usr/bin/env python3
"""Calibrate the eight-axis rubric judge against a labelled anchor set.

For each anchor it parses the judge's reply with the real
:func:`advtest_ext.judges.rubric_ipi.parse_rubric` and compares the parsed mark
on every axis to the human gold label, then reports, per axis: Cohen's kappa,
simple agreement, and the number of ``unknown`` predictions.

Offline and deterministic by default: each anchor carries a canned
``judge_reply`` (what a model said), so the harness needs no network or API key
and exercises the parser + axis mapping end to end. That is what the §10
acceptance command runs::

    .venv/bin/python scripts/judge_calibration.py --anchors tests/data/judge_anchors

An anchor is a JSON object::

    {
      "id": "...", "note": "...",
      "judge_reply": "r1: yes | evidence: \"...\"\\n...",   # the model's reply
      "gold": {"r1": "yes", "r2": "no", ..., "r8": "known"},
      "missing_injection": false        # when true, r1/r2 are excluded (no span)
    }

Rows flagged ``missing_injection`` contribute no r1/r2 signal, mirroring the
judge's own rule that those axes are unanswerable without an injected span.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow running as a plain script (python scripts/judge_calibration.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from advtest_ext.judges.rubric_ipi import parse_rubric

_AXES: tuple[str, ...] = ("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8")
_AXIS_ATTR: dict[str, str] = {
    "r1": "r1_grounded",
    "r2": "r2_complied",
    "r3": "r3_effect_present",
    "r4": "r4_authentic",
    "r5": "r5_arrived",
    "r6": "r6_gate",
    "r7": "r7_defence",
    "r8": "r8_mechanism",
}


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa for a list of ``(gold, predicted)`` categorical pairs.

    Returns ``None`` when it is undefined — fewer than two items, or an expected
    agreement of 1.0 (a single label in play), which happens on tiny or
    zero-variance samples and is honest to report as n/a rather than fake as 1.0.
    """
    n = len(pairs)
    if n < 2:
        return None
    labels = {a for a, _ in pairs} | {b for _, b in pairs}
    po = sum(1 for a, b in pairs if a == b) / n
    pe = 0.0
    for lab in labels:
        p_gold = sum(1 for a, _ in pairs if a == lab) / n
        p_pred = sum(1 for _, b in pairs if b == lab) / n
        pe += p_gold * p_pred
    if abs(1.0 - pe) < 1e-12:
        return None
    return (po - pe) / (1.0 - pe)


def _predicted_marks(judge_reply: str) -> dict[str, str] | None:
    v = parse_rubric(judge_reply)
    if v is None:
        return None
    out: dict[str, str] = {}
    for axis, attr in _AXIS_ATTR.items():
        val = getattr(v, attr)
        out[axis] = val.mark.value if hasattr(val, "mark") else str(val)
    return out


def load_anchors(anchors_dir: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(anchors_dir.glob("*.json")):
        cases.append(json.loads(path.read_text(encoding="utf-8")))
    return cases


def calibrate(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    per_axis_pairs: dict[str, list[tuple[str, str]]] = {a: [] for a in _AXES}
    per_axis_unknown: dict[str, int] = dict.fromkeys(_AXES, 0)
    unparsed = 0
    for case in cases:
        pred = _predicted_marks(case.get("judge_reply", ""))
        if pred is None:
            unparsed += 1
            continue
        gold = case.get("gold", {})
        missing = bool(case.get("missing_injection", False))
        for axis in _AXES:
            if missing and axis in ("r1", "r2"):
                continue  # no injected span → axis excluded, per the judge's rule
            if pred[axis] == "unknown":
                per_axis_unknown[axis] += 1
            if axis in gold:
                per_axis_pairs[axis].append((str(gold[axis]), pred[axis]))
    report: dict[str, dict[str, Any]] = {}
    for axis in _AXES:
        pairs = per_axis_pairs[axis]
        n = len(pairs)
        agree = sum(1 for a, b in pairs if a == b) / n if n else None
        report[axis] = {
            "n": n,
            "kappa": cohen_kappa(pairs),
            "agreement": agree,
            "unknown": per_axis_unknown[axis],
        }
    report["_meta"] = {"cases": len(cases), "unparsed": unparsed}
    return report


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}" if x < 0 or x == 0 else f"{x:.3f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--anchors", required=True, type=Path, help="directory of anchor JSON files")
    args = ap.parse_args()
    if not args.anchors.is_dir():
        print(f"error: anchors dir not found: {args.anchors}", file=sys.stderr)
        return 2
    cases = load_anchors(args.anchors)
    if not cases:
        print(f"error: no anchor JSON files in {args.anchors}", file=sys.stderr)
        return 2
    report = calibrate(cases)
    meta = report["_meta"]
    print(f"rubric judge calibration — {meta['cases']} anchors, {meta['unparsed']} unparsed")
    print(f"{'axis':<6}{'n':>4}{'kappa':>10}{'agree':>10}{'unknown':>10}")
    for axis in _AXES:
        r = report[axis]
        agree = "n/a" if r["agreement"] is None else f"{r['agreement']:.3f}"
        print(f"{axis:<6}{r['n']:>4}{_fmt(r['kappa']):>10}{agree:>10}{r['unknown']:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
