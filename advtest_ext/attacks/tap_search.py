"""Search-policy primitives for mechanism-preserving TAP exploration.

This module is deliberately provider-agnostic.  It turns a Guide's prose
``mutation_surface`` into typed dimensions, validates proposed candidates
before they can reach a live target, and maintains a small MAP-Elites-style
archive used by the TAP scheduler.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class MutationDimension(StrEnum):
    # Revised taxonomy (docs/MUTATION_AXES_TAXONOMY.md).
    PERSUASION_PRINCIPLE = "persuasion_principle"
    PRETEXT_SCENARIO = "pretext_scenario"
    PERSONA = "persona"
    ESCALATION_TRAJECTORY = "escalation_trajectory"
    FORMAT_MIMICRY = "format_mimicry"
    CARRIER_PLACEMENT = "carrier_placement"
    WORDING = "wording"
    # Legacy axes — retained until the Signature/DB clean-break migration lands so
    # the un-migrated corpus keeps validating. New guides use the axes above.
    FRAMING = "framing"
    TURN_ORDER = "turn_order"
    ESCALATION_SPEED = "escalation_speed"


_DIMENSION_TERMS: dict[MutationDimension, tuple[str, ...]] = {
    MutationDimension.PERSUASION_PRINCIPLE: (
        "persuasion",
        "persuade",
        "authority",
        "consistency",
        "reciprocity",
        "social proof",
        "commitment",
        "expert",
    ),
    MutationDimension.PRETEXT_SCENARIO: (
        "pretext",
        "scenario",
        "cover story",
        "assessment",
        "audit",
        "debugging",
        "incident",
        "framing",
        "frame",
    ),
    MutationDimension.PERSONA: ("persona", "role", "authorial", "sender"),
    MutationDimension.ESCALATION_TRAJECTORY: (
        "escalation",
        "trajectory",
        "gradual",
        "turn order",
        "order",
        "sequence",
        "backtrack",
        "speed",
        "split across turns",
    ),
    MutationDimension.FORMAT_MIMICRY: (
        "format",
        "mimic",
        "tool call",
        "system tag",
        "schema block",
        "delimiter",
        "error trace",
    ),
    MutationDimension.CARRIER_PLACEMENT: (
        "carrier",
        "placement",
        "document body",
        "email",
        "cell",
        "data channel",
        "payload",
    ),
    MutationDimension.WORDING: ("wording", "phrasing", "phrase", "label", "name", "token", "host"),
    # Legacy free-text fallbacks (map onto the retained legacy enum members).
    MutationDimension.FRAMING: ("cover", "pretense"),
    MutationDimension.TURN_ORDER: ("reorder", "beats"),
    MutationDimension.ESCALATION_SPEED: ("one turn", "compression", "slow burn"),
}


def infer_dimension(description: str) -> MutationDimension:
    """Map a legacy free-text mutation boundary onto one stable dimension."""
    lowered = description.lower()
    scored = [
        (sum(term in lowered for term in terms), dimension)
        for dimension, terms in _DIMENSION_TERMS.items()
    ]
    score, dimension = max(scored, key=lambda item: item[0])
    return dimension if score else MutationDimension.WORDING


@dataclass(frozen=True)
class MutationOperator:
    dimension: MutationDimension
    boundary: str
    name: str = "legacy"
    mutable_fields: tuple[str, ...] = ()
    allowed_transformations: tuple[str, ...] = ()
    forbidden_changes: tuple[str, ...] = ()
    dependent_invariants: tuple[str, ...] = ()
    cell_values: tuple[str, ...] = ()


def typed_operators(
    surface: list[str], explicit: list[dict[str, Any]] | None = None
) -> list[MutationOperator]:
    operators: list[MutationOperator] = []
    for index, spec in enumerate(explicit or []):
        try:
            dimension = MutationDimension(str(spec.get("dimension")))
        except ValueError:
            continue
        operators.append(
            MutationOperator(
                dimension=dimension,
                boundary=str(spec.get("description") or spec.get("name") or dimension.value),
                name=str(spec.get("name") or f"{dimension.value}-{index}"),
                mutable_fields=tuple(str(item) for item in spec.get("mutable_fields", [])),
                allowed_transformations=tuple(
                    str(item) for item in spec.get("allowed_transformations", [])
                ),
                forbidden_changes=tuple(str(item) for item in spec.get("forbidden_changes", [])),
                dependent_invariants=tuple(
                    str(item) for item in spec.get("dependent_invariants", [])
                ),
                cell_values=tuple(str(item) for item in spec.get("cell_values", [])),
            )
        )
    if operators:
        return operators
    return [
        MutationOperator(infer_dimension(item), item.strip(), name=f"legacy-{index}")
        for index, item in enumerate(surface)
        if item.strip()
    ]


@dataclass
class MutationCandidate:
    strategy: str
    dimension: MutationDimension
    mutation: str
    operator_name: str = "legacy"
    feature_value: str = "unspecified"
    plan_steps: tuple[str, ...] = ()
    expected_effect: str = ""
    artifact_patch: dict[str, Any] = field(default_factory=dict)
    preserved_invariants: tuple[str, ...] = ()
    # The planner's diagnose-then-adapt findings (outcome / active_guardrail /
    # what_passed / foothold) — recorded so the adaptation reasoning is auditable.
    diagnosis: dict[str, Any] = field(default_factory=dict)
    valid: bool = True
    rejection_reasons: list[str] = field(default_factory=list)

    @property
    def cell(self) -> str:
        value = re.sub(r"[^a-z0-9]+", "-", self.feature_value.lower()).strip("-")
        return f"{self.dimension.value}:{self.operator_name}:{value or 'unspecified'}"

    def as_metadata(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "mutation": self.mutation,
            "operator_name": self.operator_name,
            "feature_value": self.feature_value,
            "plan_steps": list(self.plan_steps),
            "expected_effect": self.expected_effect,
            "artifact_patch": self.artifact_patch,
            "preserved_invariants": list(self.preserved_invariants),
            "diagnosis": dict(self.diagnosis),
            "valid": self.valid,
            "rejection_reasons": list(self.rejection_reasons),
            "cell": self.cell,
        }


_CANDIDATE_KEY_HINTS = ("operator_name", "dimension", "strategy", "plan_steps")


def extract_candidate_json(text: str) -> dict | None:
    """Parse the candidate JSON object from a strategist reply, tolerant of code
    fences and prose preambles. A strong planner (e.g. Claude) routinely wraps the
    JSON in ```json … ``` or a sentence, which a bare ``json.loads`` rejects — that
    dropped every field (empty plan_steps / expected_effect, default operator) and
    made the planner split look like it wasn't helping. Tries a direct parse, then
    the first balanced ``{...}`` block that looks candidate-shaped."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (TypeError, json.JSONDecodeError):
        pass
    from advtest_ext.attacks._attacker_json import iter_balanced_json_blocks

    parsed: list[dict] = []
    for block in iter_balanced_json_blocks(text):
        try:
            obj = json.loads(block)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            parsed.append(obj)
    for obj in parsed:
        if any(hint in obj for hint in _CANDIDATE_KEY_HINTS):
            return obj
    return parsed[0] if parsed else None


def parse_candidate(
    raw: str,
    *,
    operators: list[MutationOperator],
    invariants: list[str],
    fallback_index: int = 0,
) -> MutationCandidate:
    """Parse a structured strategist response, retaining plain-text compatibility."""
    default = (
        operators[fallback_index % len(operators)]
        if operators
        else MutationOperator(MutationDimension.WORDING, "wording")
    )
    text = (raw or "").strip()
    payload = extract_candidate_json(text)
    if not isinstance(payload, dict):
        # Legacy/manual suggestions are trusted inputs, but still become typed
        # candidates and therefore participate in archive duplicate checks.
        return MutationCandidate(
            strategy=text or "try a different framing",
            dimension=default.dimension,
            mutation=default.boundary,
            operator_name=default.name,
            feature_value=default.cell_values[0] if default.cell_values else "manual",
            preserved_invariants=tuple(invariants),
        )
    operator_name = str(payload.get("operator_name") or default.name)
    # The OPERATOR is the source of truth for the dimension — never the attacker's
    # separately self-reported ``dimension`` field. Trusting the latter let a reply
    # claim dimension=persona while naming operator=escalation_speed, which produced
    # corrupted cell keys like ``persona:escalation_speed:…``. Bind the dimension to
    # the named operator when it exists; otherwise fall back (validate_candidate then
    # rejects it as an unknown scheduled operator).
    named = next((operator for operator in operators if operator.name == operator_name), None)
    if named is not None:
        dimension = named.dimension
    else:
        dimension_raw = str(payload.get("dimension") or default.dimension.value)
        try:
            dimension = MutationDimension(dimension_raw)
        except ValueError:
            dimension = default.dimension
    preserved = payload.get("preserved_invariants")
    return MutationCandidate(
        strategy=str(payload.get("strategy") or "").strip(),
        dimension=dimension,
        mutation=str(payload.get("mutation") or "").strip(),
        operator_name=operator_name,
        feature_value=str(payload.get("feature_value") or "unspecified"),
        plan_steps=tuple(str(item) for item in payload.get("plan_steps", []))
        if isinstance(payload.get("plan_steps"), list)
        else (),
        expected_effect=str(payload.get("expected_effect") or ""),
        artifact_patch=dict(payload.get("artifact_patch") or {})
        if isinstance(payload.get("artifact_patch"), dict)
        else {},
        preserved_invariants=tuple(str(item) for item in preserved)
        if isinstance(preserved, list)
        else (),
        diagnosis=dict(payload.get("diagnosis"))
        if isinstance(payload.get("diagnosis"), dict)
        else ({"note": str(payload["diagnosis"])} if payload.get("diagnosis") else {}),
    )


def strategy_similarity(left: str, right: str) -> float:
    def words(value: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2}

    a, b = words(left), words(right)
    if not a or not b:
        return 0.0
    token_jaccard = len(a & b) / len(a | b)

    def trigrams(value: str) -> dict[str, int]:
        normalized = re.sub(r"\s+", " ", value.lower()).strip()
        grams: dict[str, int] = {}
        for index in range(max(0, len(normalized) - 2)):
            gram = normalized[index : index + 3]
            grams[gram] = grams.get(gram, 0) + 1
        return grams

    left_grams, right_grams = trigrams(left), trigrams(right)
    shared = set(left_grams) & set(right_grams)
    dot = sum(left_grams[item] * right_grams[item] for item in shared)
    left_norm = math.sqrt(sum(value * value for value in left_grams.values()))
    right_norm = math.sqrt(sum(value * value for value in right_grams.values()))
    cosine = dot / (left_norm * right_norm) if left_norm and right_norm else 0.0
    return max(token_jaccard, cosine)


def validate_candidate(
    candidate: MutationCandidate,
    *,
    operators: list[MutationOperator],
    invariants: list[str],
    prior_strategies: list[str],
    duplicate_threshold: float,
) -> MutationCandidate:
    """Fail closed on boundary/invariant violations before live execution."""
    reasons: list[str] = []
    allowed = {operator.dimension for operator in operators}
    matched = next(
        (
            operator
            for operator in operators
            if operator.dimension is candidate.dimension
            and operator.name == candidate.operator_name
        ),
        None,
    )
    if not candidate.strategy:
        reasons.append("empty strategy")
    if not candidate.mutation:
        reasons.append("candidate did not describe a concrete mutation")
    if allowed and candidate.dimension not in allowed:
        reasons.append(f"mutation dimension {candidate.dimension.value!r} is not allowed")
    if operators and matched is None:
        reasons.append("candidate did not use the scheduled named operator")
    if operators and not candidate.plan_steps:
        reasons.append("candidate did not provide an executable mutation plan")
    if operators and not candidate.expected_effect:
        reasons.append("candidate did not state an expected mechanism-level effect")
    if invariants and set(candidate.preserved_invariants) != set(invariants):
        reasons.append("candidate did not preserve every immutable invariant")
    if any(
        strategy_similarity(candidate.strategy, prior) >= duplicate_threshold
        for prior in prior_strategies
        if prior
    ):
        reasons.append("semantic duplicate of an attempted strategy")
    if matched is not None:
        unknown_paths = set(candidate.artifact_patch) - set(matched.mutable_fields)
        if unknown_paths:
            reasons.append(f"artifact patch touches immutable fields: {sorted(unknown_paths)}")
        if candidate.artifact_patch and not matched.mutable_fields:
            reasons.append("operator does not permit artifact mutation")
        forbidden_blob = " ".join(
            [candidate.strategy, candidate.mutation, *candidate.plan_steps]
        ).lower()
        if any(change.lower() in forbidden_blob for change in matched.forbidden_changes):
            reasons.append("candidate includes a forbidden change")
        missing_dependencies = set(matched.dependent_invariants) - set(
            candidate.preserved_invariants
        )
        if missing_dependencies:
            reasons.append("candidate omitted operator-dependent invariants")
    candidate.valid = not reasons
    candidate.rejection_reasons = reasons
    return candidate


@dataclass
class ArchiveElite:
    candidate: MutationCandidate
    score: float
    node_id: str


class DiversityArchive:
    """A compact MAP-Elites archive: best result retained per mutation cell."""

    def __init__(
        self,
        *,
        duplicate_threshold: float = 0.78,
        path: str | Path | None = None,
        scope: str = "default",
    ) -> None:
        if not 0.0 <= duplicate_threshold <= 1.0:
            raise ValueError("duplicate_threshold must be in [0, 1]")
        self.duplicate_threshold = duplicate_threshold
        self.path = Path(path) if path else None
        self.scope = scope
        self._attempted: list[str] = []
        self._elites: dict[str, ArchiveElite] = {}
        self._rejections: list[dict[str, Any]] = []
        self._cell_attempts: dict[str, int] = {}
        self._successful_cells: set[str] = set()
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            root = json.loads(self.path.read_text(encoding="utf-8"))
            data = root.get(self.scope, {}) if isinstance(root, dict) else {}
        except (OSError, json.JSONDecodeError):
            return
        self._attempted = [str(item) for item in data.get("attempted", [])]
        self._rejections = [dict(item) for item in data.get("rejections", [])]
        self._cell_attempts = {
            str(key): int(value) for key, value in data.get("cell_attempts", {}).items()
        }
        self._successful_cells = {str(item) for item in data.get("successful_cells", [])}
        for cell, raw in data.get("elites", {}).items():
            candidate = parse_candidate(
                json.dumps(raw.get("candidate", {})), operators=[], invariants=[]
            )
            self._elites[str(cell)] = ArchiveElite(
                candidate=candidate,
                score=float(raw.get("score", 0.0)),
                node_id=str(raw.get("node_id", "")),
            )

    def _persist(self) -> None:
        if self.path is None:
            return
        root: dict[str, Any] = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    root = loaded
            except (OSError, json.JSONDecodeError):
                pass
        root[self.scope] = {
            "attempted": self._attempted,
            "rejections": self._rejections[-500:],
            "cell_attempts": self._cell_attempts,
            "successful_cells": sorted(self._successful_cells),
            "elites": {
                cell: {
                    "candidate": elite.candidate.as_metadata()
                    | {"strategy": elite.candidate.strategy},
                    "score": elite.score,
                    "node_id": elite.node_id,
                }
                for cell, elite in self._elites.items()
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(root, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    @property
    def attempted_strategies(self) -> list[str]:
        return list(self._attempted)

    @property
    def elites(self) -> dict[str, ArchiveElite]:
        return dict(self._elites)

    def is_duplicate(self, strategy: str) -> bool:
        return any(
            strategy_similarity(strategy, prior) >= self.duplicate_threshold
            for prior in self._attempted
        )

    def record_attempt(self, strategy: str) -> None:
        if strategy and strategy not in self._attempted:
            self._attempted.append(strategy)
            self._persist()

    def record_rejection(self, candidate: MutationCandidate) -> None:
        self._rejections.append(
            {
                "strategy": candidate.strategy,
                "cell": candidate.cell,
                "reasons": list(candidate.rejection_reasons),
            }
        )
        self._persist()

    def update(
        self,
        candidate: MutationCandidate,
        *,
        score: float,
        node_id: str,
        succeeded: bool = False,
    ) -> bool:
        self.record_attempt(candidate.strategy)
        self._cell_attempts[candidate.cell] = self._cell_attempts.get(candidate.cell, 0) + 1
        if succeeded:
            self._successful_cells.add(candidate.cell)
        current = self._elites.get(candidate.cell)
        if current is None or score > current.score:
            self._elites[candidate.cell] = ArchiveElite(candidate, score, node_id)
            self._persist()
            return True
        self._persist()
        return False

    def cell_attempts(self, cell: str) -> int:
        return self._cell_attempts.get(cell, 0)

    def coverage(self, operators: list[MutationOperator]) -> float:
        """Fraction of declared cells with an elite, regardless of outcome."""
        possible = {
            f"{operator.dimension.value}:{operator.name}:"
            f"{re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-') or 'unspecified'}"
            for operator in operators
            for value in (operator.cell_values or ("unspecified",))
        }
        return len(set(self._elites) & possible) / len(possible) if possible else 0.0

    def successful_coverage(self, operators: list[MutationOperator]) -> float:
        """Fraction of declared cells with a search-level successful node."""
        possible = {
            f"{operator.dimension.value}:{operator.name}:"
            f"{re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-') or 'unspecified'}"
            for operator in operators
            for value in (operator.cell_values or ("unspecified",))
        }
        return len(self._successful_cells & possible) / len(possible) if possible else 0.0

    def report(self, operators: list[MutationOperator]) -> dict[str, Any]:
        possible = {
            f"{operator.dimension.value}:{operator.name}:"
            f"{re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-') or 'unspecified'}"
            for operator in operators
            for value in (operator.cell_values or ("unspecified",))
        }
        attempted_cells = set(self._cell_attempts)
        return {
            "scope": self.scope,
            "coverage": self.coverage(operators),
            "search_coverage": self.coverage(operators),
            "search_success_coverage": self.successful_coverage(operators),
            "successful_cells": sorted(self._successful_cells),
            "possible_cells": sorted(possible),
            "attempted_cells": sorted(attempted_cells),
            "unexplored_cells": sorted(possible - attempted_cells),
            "cell_attempts": dict(self._cell_attempts),
            "elite_scores": {cell: elite.score for cell, elite in sorted(self._elites.items())},
            "rejected_count": len(self._rejections),
            "rejections": list(self._rejections[-50:]),
        }


class PartialSignalCalibrator:
    """Beta-Bernoulli partial-signal calibration learned from reviewed traces."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.stats: dict[str, dict[str, float]] = {}
        if self.path and self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.stats = {
                        str(name): {str(key): float(value) for key, value in values.items()}
                        for name, values in raw.items()
                        if isinstance(values, dict)
                    }
            except (OSError, json.JSONDecodeError, ValueError):
                pass

    def fit(self, reviews: list[tuple[set[str], bool]]) -> None:
        names = {name for fired, _success in reviews for name in fired}
        for name in names:
            positives = sum(1 for fired, success in reviews if name in fired and success)
            negatives = sum(1 for fired, success in reviews if name in fired and not success)
            alpha, beta = 1.0 + positives, 1.0 + negatives
            total = alpha + beta
            self.stats[name] = {
                "mean": alpha / total,
                "variance": (alpha * beta) / (total * total * (total + 1.0)),
                "n": float(positives + negatives),
            }
        self._persist()

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.stats, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def weight(self, name: str, default: float) -> tuple[float, float]:
        stat = self.stats.get(name)
        if not stat:
            return default, 1.0
        uncertainty = math.sqrt(max(0.0, stat.get("variance", 0.0)))
        conservative = max(0.0, stat.get("mean", default) - 1.64 * uncertainty)
        return conservative, uncertainty


class JudgeCalibrator:
    """Reliability calibration for the primary heuristic judge score.

    The per-turn judge score is the entire search gradient: ranking,
    pruning, and the TextGrad refine trigger all key off it. A judge that
    systematically under- or over-scores replies distorts every downstream
    decision. This learns a monotonic map from a raw judge score to the
    empirical success rate observed in that score band across reviewed
    traces, so the gradient tracks reality instead of the judge's raw
    calibration. The map is isotonic (non-decreasing), so it never inverts
    the judge's ordering, only rescales it. Identity (a no-op) until
    fitted, so an unconfigured run behaves exactly as before.
    """

    def __init__(self, path: str | Path | None = None, *, bins: int = 10) -> None:
        self.path = Path(path) if path else None
        self.bins = max(2, bins)
        # Fitted control points as (raw_score, calibrated_score), ascending
        # in both coordinates. Empty => identity.
        self.curve: list[tuple[float, float]] = []
        if self.path and self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    self.curve = [
                        (float(point[0]), float(point[1]))
                        for point in raw
                        if isinstance(point, (list, tuple)) and len(point) == 2
                    ]
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                self.curve = []

    def fit(self, reviews: list[tuple[float, bool]]) -> None:
        """Learn the calibration curve from ``(raw_judge_score, was_success)``
        pairs. Buckets raw scores into fixed-width bins, computes a
        Laplace-smoothed success rate per non-empty bin, then applies
        Pool-Adjacent-Violators so the resulting map is monotonic."""
        clean = [(max(0.0, min(1.0, float(score))), bool(ok)) for score, ok in reviews]
        if not clean:
            return
        width = 1.0 / self.bins
        buckets: dict[int, list[bool]] = {}
        for score, ok in clean:
            index = min(self.bins - 1, int(score / width))
            buckets.setdefault(index, []).append(ok)
        centers: list[float] = []
        rates: list[float] = []
        weights: list[float] = []
        for index in sorted(buckets):
            outcomes = buckets[index]
            positives = sum(1 for ok in outcomes if ok)
            # Laplace smoothing keeps a sparsely-populated bin from snapping
            # to a hard 0 or 1 on one or two samples.
            rate = (positives + 1.0) / (len(outcomes) + 2.0)
            centers.append((index + 0.5) * width)
            rates.append(rate)
            weights.append(float(len(outcomes)))
        calibrated = _pool_adjacent_violators(rates, weights)
        self.curve = list(zip(centers, calibrated, strict=True))
        self._persist()

    def calibrate(self, raw: float) -> float:
        """Map a raw judge score to its calibrated value by piecewise-linear
        interpolation over the fitted control points. Identity when unfitted."""
        if not self.curve:
            return raw
        value = max(0.0, min(1.0, float(raw)))
        if value <= self.curve[0][0]:
            return self.curve[0][1]
        if value >= self.curve[-1][0]:
            return self.curve[-1][1]
        for (x0, y0), (x1, y1) in zip(self.curve, self.curve[1:], strict=False):
            if x0 <= value <= x1:
                if x1 == x0:
                    return y1
                frac = (value - x0) / (x1 - x0)
                return y0 + frac * (y1 - y0)
        return value

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps([list(point) for point in self.curve], indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)


def _pool_adjacent_violators(values: list[float], weights: list[float]) -> list[float]:
    """Isotonic regression via the Pool-Adjacent-Violators algorithm.

    Returns the least-squares non-decreasing fit to ``values`` with the
    given ``weights``, expanded back to one value per input point. Used to
    enforce that a higher raw judge score never maps to a lower calibrated
    success rate."""
    # Each block tracks (pooled weighted mean, total weight, point count).
    blocks: list[tuple[float, float, int]] = []
    for value, weight in zip(values, weights, strict=True):
        mean, total, size = value, max(weight, 1e-9), 1
        while blocks and blocks[-1][0] >= mean:
            prev_mean, prev_weight, prev_size = blocks.pop()
            combined = prev_weight + total
            mean = (prev_mean * prev_weight + mean * total) / combined
            total = combined
            size += prev_size
        blocks.append((mean, total, size))
    result: list[float] = []
    for mean, _total, size in blocks:
        result.extend([mean] * size)
    return result


class SearchCheckpointStore:
    """Atomic checkpoints reusable only under an explicitly attested identity.

    Identity values are caller-supplied immutable versions or fingerprints, not
    display names. Missing identity (including legacy checkpoints) disables reuse;
    a scope alone never establishes that an experiment is still the same.
    """

    REQUIRED_IDENTITY_FIELDS = frozenset({"guide", "models", "evaluator", "config", "environment"})

    def __init__(
        self,
        path: str | Path | None,
        scope: str,
        *,
        identity: dict[str, str] | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.scope = scope
        self.identity = dict(identity or {})
        self.identity_complete = self.REQUIRED_IDENTITY_FIELDS.issubset(self.identity) and all(
            isinstance(value, str) and bool(value.strip()) for value in self.identity.values()
        )
        self.state: dict[str, Any] = {
            "nodes": {}, "operator_cursor": 0, "identity": self.identity,
        }
        if self.path and self.path.exists():
            try:
                root = json.loads(self.path.read_text(encoding="utf-8"))
                saved = root.get(scope) if isinstance(root, dict) else None
                if (
                    self.identity_complete
                    and isinstance(saved, dict)
                    and saved.get("identity") == self.identity
                ):
                    self.state = dict(saved)
            except (OSError, json.JSONDecodeError):
                pass

    def restore_node(self, node: dict[str, Any]) -> bool:
        if not self.identity_complete:
            return False
        saved = self.state.get("nodes", {}).get(str(node.get("id")))
        if not isinstance(saved, dict):
            return False
        node.update(saved)
        return True

    def find_child(self, parent_id: str, depth: int, variant: int) -> dict[str, Any] | None:
        if not self.identity_complete:
            return None
        suffix = f"-{variant}"
        for node in self.state.get("nodes", {}).values():
            if (
                isinstance(node, dict)
                and node.get("parent_id") == parent_id
                and int(node.get("depth", -1)) == depth
                and str(node.get("id", "")).endswith(suffix)
            ):
                return dict(node)
        return None

    def save_node(self, node: dict[str, Any], *, operator_cursor: int) -> None:
        if self.path is None:
            return
        serializable = {key: value for key, value in node.items() if key not in {"runtime_handle"}}
        self.state.setdefault("nodes", {})[str(node["id"])] = serializable
        self.state["operator_cursor"] = operator_cursor
        root: dict[str, Any] = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    root = loaded
            except (OSError, json.JSONDecodeError):
                pass
        root[self.scope] = self.state
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(root, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)


class CausalControlTracker:
    """Pair controls and variants to estimate score deltas by mutation cell."""

    def __init__(self) -> None:
        self.controls: dict[str, float] = {}
        self.variants: list[dict[str, Any]] = []

    def record_control(self, kind: str, score: float) -> None:
        self.controls[kind] = score

    def record_variant(self, candidate: MutationCandidate, score: float) -> None:
        baseline = self.controls.get("strict_seed", self.controls.get("natural", 0.0))
        self.variants.append(
            {
                "cell": candidate.cell,
                "score": score,
                "baseline": baseline,
                "delta": score - baseline,
            }
        )

    def report(self) -> dict[str, Any]:
        return {"controls": dict(self.controls), "variants": list(self.variants)}


def calibrated_partial_score(
    base_score: float,
    fired: list[str],
    *,
    weights: dict[str, float],
    default_floor: float,
    success_threshold: float,
) -> tuple[float, dict[str, float]]:
    """Apply distinct, explicit partial-signal weights without creating success."""
    applied = {name: max(0.0, min(1.0, float(weights.get(name, default_floor)))) for name in fired}
    partial_component = (
        min(max(applied.values()), max(0.0, success_threshold - 0.01)) if applied else 0.0
    )
    return max(base_score, partial_component), applied


__all__ = [
    "CausalControlTracker",
    "DiversityArchive",
    "JudgeCalibrator",
    "MutationCandidate",
    "MutationDimension",
    "MutationOperator",
    "PartialSignalCalibrator",
    "SearchCheckpointStore",
    "calibrated_partial_score",
    "infer_dimension",
    "parse_candidate",
    "strategy_similarity",
    "typed_operators",
    "validate_candidate",
]
