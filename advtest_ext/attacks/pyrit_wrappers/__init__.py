"""Wrappers around (PyRIT-pioneered) attack techniques, re-implemented
against ``advtest_ext``'s ``Provider`` / ``Judge`` / ``DocumentTarget``
abstractions so they slot into the same composition model as the
homegrown attacks (PAIR, BoN, MSJ, IndirectInjection).

* :class:`CrescendoAttack` — multi-turn benign-to-harmful escalation
  (Russinovich 2024). PyRIT ships this; we cite the same paper.
* :class:`TapAttack` — Tree of Attacks with Pruning (Mehrotra 2024).
  Generalises PAIR with depth-wise branching + pruning.
* :class:`PapAttack` — Persuasive Adversarial Prompts (Zeng 2024).
  Not in PyRIT; ships its own taxonomy YAML. Lives here because it
  occupies the same "external technique exposed as an AttackPlugin"
  shelf.

Each wrapper composes via ``ComposedAttack``, so e.g. ``Crescendo ∘
IndirectInjection`` lets a Crescendo transcript travel as document
content (an email thread embedded via ``comment_thread`` /
``citation_block``).
"""

from advtest_ext.attacks.pyrit_wrappers.crescendo import (
    CrescendoAttack,
    CrescendoTurn,
)
from advtest_ext.attacks.pyrit_wrappers.pap import (
    PapAttack,
    PapStrategy,
    PapTemplate,
)
from advtest_ext.attacks.pyrit_wrappers.tap import TapAttack, TapNode

__all__ = [
    "CrescendoAttack",
    "CrescendoTurn",
    "PapAttack",
    "PapStrategy",
    "PapTemplate",
    "TapAttack",
    "TapNode",
]
