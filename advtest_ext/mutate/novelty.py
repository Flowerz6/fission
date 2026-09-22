"""Anti-paraphrase novelty gate — two levels.

* **CELL:** reject a variant whose Signature already has sufficient ESS, unless
  it's an explicit replication request. New cells are always admissible — the
  whole point of the engine is reaching unvisited strategy regions.
* **INDIVIDUAL:** within a cell, reject a new wording only when BOTH its textual
  AND its semantic similarity to an existing individual exceed threshold (either
  alone is gameable — a thesaurus beats textual, a template beats semantic).
  Wording is the ONLY place similarity is checked.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from advtest_ext.guides.cell_generator import _similarity

if TYPE_CHECKING:  # pragma: no cover - typing only
    from advtest_ext.archive.fingerprint import Archive
    from advtest_ext.archive.signature import Signature

SimilarityFn = Callable[[str, str], float]


@dataclass(frozen=True)
class NoveltyDecision:
    admit: bool
    reason: str


def cell_admissible(
    signature: Signature,
    archive: Archive,
    *,
    ess_threshold: float = 8.0,
    replication: bool = False,
) -> NoveltyDecision:
    """A new variant's cell is admissible unless it's already well-sampled."""
    cell = archive.cells.get(signature.cell_key())
    if cell is None:
        return NoveltyDecision(admit=True, reason="new cell")
    if replication:
        return NoveltyDecision(admit=True, reason="explicit replication request")
    ess = cell.posterior.effective_sample_size()
    if ess >= ess_threshold:
        return NoveltyDecision(admit=False, reason=f"cell already sampled (ESS {ess:.1f} >= {ess_threshold})")
    return NoveltyDecision(admit=True, reason=f"cell below ESS threshold (ESS {ess:.1f})")


def individual_admissible(
    new_wording: str,
    existing_wordings: Iterable[str],
    *,
    semantic_similarity: SimilarityFn,
    textual_similarity: SimilarityFn = _similarity,
    textual_threshold: float = 0.8,
    semantic_threshold: float = 0.8,
) -> NoveltyDecision:
    """Reject a near-duplicate wording only when textual AND semantic similarity
    both exceed threshold against some existing individual."""
    for existing in existing_wordings:
        textual = textual_similarity(new_wording, existing)
        semantic = semantic_similarity(new_wording, existing)
        if textual > textual_threshold and semantic > semantic_threshold:
            return NoveltyDecision(
                admit=False,
                reason=f"near-duplicate wording (textual {textual:.2f}, semantic {semantic:.2f})",
            )
    return NoveltyDecision(admit=True, reason="novel wording")


__all__ = ["NoveltyDecision", "SimilarityFn", "cell_admissible", "individual_admissible"]
