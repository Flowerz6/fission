"""AgentDojo adapter — wraps AgentDojo's task suites as advtest
``DocumentTarget`` implementations.

AgentDojo (Debenedetti et al. 2024, ``debenedetti2024agentdojo``) is
the canonical agentic-IPI benchmark: tool-using agents in simulated
environments (banking, Slack, travel, workspace) with attacker-
controlled "untrusted" content injected into tool returns. Wrapping
its suites as DocumentTargets means our 25 hiding techniques run
against AgentDojo's environments without any per-environment glue —
the existing IndirectInjectionAttack composes with whichever
AgentDojo suite the user wires.

Scope: scaffolding, not full coverage. The three subclasses below
(:class:`BankingTarget`, :class:`SlackTarget`, :class:`WorkspaceTarget`)
cover one task each, drawn from the canonical AgentDojo task suites.
Adding more is mechanical — point the constructor at a different
suite name and override ``capabilities`` if the environment supports
different block types.

Citations
---------
* debenedetti2024agentdojo — the AgentDojo benchmark itself.
"""

from advtest_ext.adapters.agentdojo.target import (
    AgentDojoTarget,
    BankingTarget,
    SlackTarget,
    WorkspaceTarget,
)

__all__ = [
    "AgentDojoTarget",
    "BankingTarget",
    "SlackTarget",
    "WorkspaceTarget",
]

__citations__: list[str] = [
    "debenedetti2024agentdojo",
]
