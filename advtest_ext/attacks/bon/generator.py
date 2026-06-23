"""``BonGenerator`` — random-augmentation variant generator.

Per :func:`hughes2024bon`, BoN samples N augmented variants of a base
payload by composing a small random subset of augmentations per
variant. The augmentation chain (in order) is recorded so the replay
layer can reconstruct any single variant deterministically given the
seed.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field

from advtest_ext.attacks.bon.augmentations import (
    DEFAULT_TEXT_AUGMENTATIONS,
    TextAugmentation,
)


@dataclass
class BonVariant:
    """One augmented variant. ``index`` is its position in the BoN
    sample sequence; ``chain`` is the ordered augmentation names that
    produced it."""

    index: int
    text: str
    chain: list[str] = field(default_factory=list)


class BonGenerator:
    """Yields N augmented variants of a base text.

    Each variant picks a random subset of size 1-K from the
    augmentation pool (``K = max_chain_length``) and applies them in
    a random order. The default pool is the six text augmentations
    from Hughes 2024.
    """

    def __init__(
        self,
        *,
        augmentations: Sequence[TextAugmentation] | None = None,
        max_chain_length: int = 3,
        seed: int | None = None,
    ) -> None:
        if augmentations is not None and not augmentations:
            raise ValueError("BonGenerator needs at least one augmentation")
        self._augs = list(augmentations) if augmentations else list(DEFAULT_TEXT_AUGMENTATIONS)
        if max_chain_length < 1:
            raise ValueError("max_chain_length must be >= 1")
        self._max_chain = min(max_chain_length, len(self._augs))
        self._seed = seed

    @property
    def augmentations(self) -> Sequence[TextAugmentation]:
        return list(self._augs)

    def variants(self, base: str, n: int) -> list[BonVariant]:
        """Generate ``n`` augmented variants of ``base``. Deterministic
        given ``seed`` (the constructor argument)."""
        if n < 1:
            raise ValueError("n must be >= 1")
        rng = random.Random(self._seed)
        out: list[BonVariant] = []
        for i in range(n):
            chain_len = rng.randint(1, self._max_chain)
            chosen = rng.sample(self._augs, chain_len)
            text = base
            for aug in chosen:
                text = aug.apply(text, rng)
            out.append(
                BonVariant(
                    index=i,
                    text=text,
                    chain=[aug.name for aug in chosen],
                )
            )
        return out


__all__ = ["BonGenerator", "BonVariant"]
