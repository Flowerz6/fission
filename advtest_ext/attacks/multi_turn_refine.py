"""TAP-style trajectory-level refinement for multi-turn conversations.

Crescendo optimises one turn at a time, online — it can't undo a bad
early turn. This engine optimises the WHOLE conversation chain instead:

  1. Replay a candidate chain against the target → per-turn scores.
  2. A diagnoser identifies the turn k where it drifted/failed
     (credit assignment over the per-turn scores + transcript).
  3. A refiner rewrites the chain FROM turn k (regen-from-break):
     turns 1..k-1 are kept verbatim (they worked), turns k.. are
     regenerated as a fresh, possibly different-length tail.
  4. Replay the candidate in a CLEAN conversation; keep it only if it
     scores better (hill-climb). Stop on threshold or patience.

This is the "tree of attacks with pruning" idea (Mehrotra 2024) lifted
from single prompts to whole conversation chains: the node is a chain,
expansion is a refinement at the failure turn, the score prunes.

The engine is deliberately I/O-free: the live replay and the two LLM
calls are injected as ``replay_fn`` / ``diagnose_fn`` / ``refine_fn``,
so the loop logic is unit-testable without a browser or a model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# Injected-callable signatures (documentation only; not enforced):
#   replay_fn(chain: list[str], round_idx: int) -> Awaitable[ChainResult]
#   diagnose_fn(result: ChainResult)            -> Awaitable[Diagnosis]
#   refine_fn(prefix, diagnosis, prev_failed_tails) -> Awaitable[list[str]]
ReplayFn = Callable[[list[str], int], "Awaitable[ChainResult]"]
DiagnoseFn = Callable[["ChainResult"], "Awaitable[Diagnosis]"]
RefineFn = Callable[[list[str], "Diagnosis", list[list[str]]], "Awaitable[list[str]]"]


@dataclass
class Diagnosis:
    """Credit-assignment verdict over one replayed chain."""

    failure_turn: int  # 1-based turn where the chain drifted/failed
    reason: str = ""


@dataclass
class ChainResult:
    """Outcome of replaying one chain against the live target."""

    chain: list[str]
    score: float
    per_turn_scores: list[float] = field(default_factory=list)
    transcript: str = ""
    fired: dict[str, bool] = field(default_factory=dict)


def aggregate_score(per_turn_scores: list[float]) -> float:
    """A chain's score is the best any single turn achieved — the
    exploit succeeds if it lands on ANY turn (the handshake usually
    fires on the last). ``0.0`` for an empty chain."""
    return max(per_turn_scores) if per_turn_scores else 0.0


# ----------------------------------------------------------------------
# Robust JSON extraction — LLMs wrap JSON in prose / code fences.
# ----------------------------------------------------------------------


def _extract_json(text: str) -> Any:
    """Best-effort: parse the first JSON object or array in ``text``.

    Tries the whole string, then a fenced ```json block, then the first
    balanced ``{...}`` / ``[...]`` span. Raises ``ValueError`` if none
    parse — callers decide the fallback.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")
    # 1. whole string
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2. fenced code block
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 3. first balanced object / array span
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("no parseable JSON found")


def parse_diagnosis(text: str, *, default_turn: int = 1) -> Diagnosis:
    """Parse a diagnoser reply into a :class:`Diagnosis`.

    Expects ``{"failure_turn": <int>, "reason": "<str>"}``. On any parse
    miss, falls back to ``default_turn`` so the loop degrades to
    "refine from the start" rather than crashing.
    """
    try:
        obj = _extract_json(text)
    except ValueError:
        return Diagnosis(failure_turn=default_turn, reason="(unparseable diagnosis)")
    if not isinstance(obj, dict):
        return Diagnosis(failure_turn=default_turn, reason="(diagnosis not an object)")
    raw_turn = obj.get("failure_turn", default_turn)
    try:
        turn = int(raw_turn)
    except (TypeError, ValueError):
        turn = default_turn
    reason = str(obj.get("reason", "")).strip()
    return Diagnosis(failure_turn=turn, reason=reason)


def parse_chain_messages(text: str) -> list[str]:
    """Parse a refiner reply into a list of user messages.

    Accepts ``{"messages": [...]}`` or a bare JSON array of strings.
    Non-string / empty items are dropped. Returns ``[]`` on a parse
    miss so the caller can skip the round instead of crashing.
    """
    try:
        obj = _extract_json(text)
    except ValueError:
        return []
    if isinstance(obj, dict):
        obj = obj.get("messages", [])
    if not isinstance(obj, list):
        return []
    out: list[str] = []
    for item in obj:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


# ----------------------------------------------------------------------
# Hill-climb over conversation chains
# ----------------------------------------------------------------------


async def hill_climb(
    *,
    seed_chain: list[str],
    replay_fn: ReplayFn,
    diagnose_fn: DiagnoseFn,
    refine_fn: RefineFn,
    max_rounds: int = 6,
    patience: int = 2,
    threshold: float = 0.85,
    log: Callable[[str], None] = print,
) -> ChainResult:
    """Refine ``seed_chain`` until it scores ``>= threshold``, runs out
    of ``max_rounds``, or fails to improve for ``patience`` rounds.

    Regen-from-break + variable length: each round diagnoses the current
    best, keeps turns ``1..k-1``, and asks ``refine_fn`` for a fresh
    tail from turn ``k`` (any length). Failed tails per break-turn are
    fed back so the refiner varies rather than repeating itself.
    """
    if max_rounds < 0:
        raise ValueError("max_rounds must be >= 0")

    best = await replay_fn(seed_chain, 0)
    log(f"[seed] score={best.score:.2f} turns={len(best.chain)}")
    if best.score >= threshold:
        log("[seed] already at threshold")
        return best

    stale = 0
    failed_tails: dict[int, list[list[str]]] = {}
    round_idx = 1
    while round_idx <= max_rounds and stale < patience:
        diag = await diagnose_fn(best)
        # Clamp to a valid 1-based turn index over the current best.
        k = max(1, min(diag.failure_turn, len(best.chain)))
        prefix = best.chain[: k - 1]
        log(f"[round {round_idx}] break@turn{k}: {diag.reason[:120]}")

        tail = await refine_fn(prefix, diag, failed_tails.get(k, []))
        if not tail:
            stale += 1
            log(f"[round {round_idx}] refiner produced no tail; stale={stale}")
            round_idx += 1
            continue

        candidate = [*prefix, *tail]
        cand = await replay_fn(candidate, round_idx)
        if cand.score > best.score:
            log(f"[round {round_idx}] improved {best.score:.2f} -> {cand.score:.2f}")
            best = cand
            stale = 0
            if best.score >= threshold:
                log(f"[round {round_idx}] threshold {threshold:.2f} reached")
                break
        else:
            stale += 1
            failed_tails.setdefault(k, []).append(tail)
            log(
                f"[round {round_idx}] no gain ({cand.score:.2f} <= {best.score:.2f}); stale={stale}"
            )
        round_idx += 1

    log(f"[done] best score={best.score:.2f} turns={len(best.chain)}")
    return best


__all__ = [
    "ChainResult",
    "DiagnoseFn",
    "Diagnosis",
    "RefineFn",
    "ReplayFn",
    "aggregate_score",
    "hill_climb",
    "parse_chain_messages",
    "parse_diagnosis",
]
