"""Attack plugins.

Priority shipping list (see FOUNDATIONS.md for full rationale):
  - PAIR — payload generator, composes with IPI.
  - Crescendo — multi-turn escalation (PyRIT supports natively; we wrap it).
  - Many-shot Jailbreaking — long-context attack.
  - Best-of-N — sampling/automation scaling.
  - PAP — persuasion taxonomy templates.

This Prompt-5 cut ships the AttackPlugin abstraction and two reference
plugins (StaticPayloadAttack, TemplateAttack), plus ComposedAttack
which performs the cross-product foundation for "PAIR generates
payload, indirect-injection delivers it." More plugins land in later
prompts.

Each individual attack plugin records its own ``references`` list of
bib keys; the package-level ``__citations__`` below is the union
across the priority shipping set plus what the reference plugins use.
"""

from advtest_ext.attacks.base import (
    AttackAttempt,
    AttackPlugin,
    AttackPluginClass,
    ComposedAttack,
)
from advtest_ext.attacks.static import StaticPayloadAttack
from advtest_ext.attacks.template import TemplateAttack

__citations__: list[str] = [
    # Priority shipping list.
    "chao2023pair",
    "russinovich2024crescendo",
    "anil2024msj",
    "hughes2024bon",
    "zeng2024pap",
    # Critical perspective informing orchestrator composition logic.
    "bhagwatkar2025firewalls",
    # Aspirational MCTS payload search.
    "agentvigil2025",
    # Reference-plugin foundations.
    "greshake2023ipi",  # naïve direct-IPI templates / static payloads
    "promptware_nassi2025",  # Workspace-specific static payload
    "geminijack_2025",  # Workspace-specific static payload
    "zhang2025asb",  # authority-impersonation template
]

__all__ = [
    "AttackAttempt",
    "AttackPlugin",
    "AttackPluginClass",
    "ComposedAttack",
    "StaticPayloadAttack",
    "TemplateAttack",
]
