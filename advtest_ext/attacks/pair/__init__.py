"""PAIR (Chao 2023) — twenty-query refinement loop.

PAIR runs an attacker LLM against the target with a judge in the
loop: each round, the attacker proposes a payload, the target
responds, the judge scores, the attacker sees the score and
refines. Defaults to 20 rounds (chao2023pair).

Reference: Chao, Patrick et al. "Jailbreaking Black Box Large
Language Models in Twenty Queries." arXiv:2310.08419, 2023.
See FOUNDATIONS.md ("Attack techniques: Ship these").
"""

from advtest_ext.attacks.pair.attack import PairAttack, PairIndirectAttack
from advtest_ext.attacks.pair.generator import PairGenerator, PairRound
from advtest_ext.attacks.pair.prompts import (
    ATTACKER_FIRST_USER_MSG,
    ATTACKER_FOLLOWUP_USER_MSG,
    ATTACKER_SYSTEM_PROMPT,
)

__citations__: list[str] = ["chao2023pair"]

__all__ = [
    "ATTACKER_FIRST_USER_MSG",
    "ATTACKER_FOLLOWUP_USER_MSG",
    "ATTACKER_SYSTEM_PROMPT",
    "PairAttack",
    "PairGenerator",
    "PairIndirectAttack",
    "PairRound",
]
