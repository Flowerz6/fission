"""Many-shot Jailbreaking (Anil et al. 2024).

Reference: Anil, Cem et al. "Many-Shot Jailbreaking." NeurIPS 2024.
See FOUNDATIONS.md ("Attack techniques: Ship these"); the long-context
power-law is one of the pitch-deck graphs.
"""

from advtest_ext.attacks.msj.attack import MsjAttack, MsjIndirectAttack
from advtest_ext.attacks.msj.generator import (
    MsjDemonstration,
    MsjGenerator,
    MsjPrompt,
)

__citations__: list[str] = ["anil2024msj"]

__all__ = [
    "MsjAttack",
    "MsjDemonstration",
    "MsjGenerator",
    "MsjIndirectAttack",
    "MsjPrompt",
]
