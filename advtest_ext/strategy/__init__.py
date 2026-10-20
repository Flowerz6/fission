"""PIMiner-style learning layer for fission's iterative IPI attacker.

This package ports the design of **PIMiner** ("Agent Against Agent: An
Agentic System for Automatic Prompt Injection Red Teaming", Wang et al.,
arXiv 2608.05108; MIT-licensed reference implementation at
github.com/Wang-Yanting/PIMiner) onto fission's existing spine.

What we keep from PIMiner:
  * the **strategy-library** format (one Markdown file per named attack
    mechanism, with router/digester-parsed H2 sections) — see
    ``library/_TEMPLATE.md``;
  * the **router → iterative attacker → digester** loop and its
    **three-level memory** (long-term library, intra-dataset experience,
    intra-sample attempt history);
  * the attacker's ``<analysis>`` + ``<injection>`` contract and the
    discipline that each new iter derives a *structurally different* move
    from the observed failure.

What we drop:
  * PIMiner drives its agents as out-of-process ``claude -p`` sessions
    over a ``run_dir`` of JSON, wired by slash-commands. fission has
    in-process :class:`~advtest_ext.providers.base.Provider` objects, so
    we call the attacker/router/digester directly — no subprocess, no
    ``run_dir`` plumbing.

The one seam that varies by environment is the **rollout** (PIMiner's
"reward"): given a sample and an injection string, run the target and
return ``(utility, security, final_output, trajectory)``. AgentDojo, an
offline deterministic reward, and the live Workspace adapter + layered
oracle each implement :class:`~advtest_ext.strategy.targets.AttackTarget`.
"""

from __future__ import annotations

from advtest_ext.strategy.attacker import ProviderAttacker, ScriptedAttacker
from advtest_ext.strategy.backends.agentdojo_backend import AgentDojoTarget
from advtest_ext.strategy.backends.workspace_backend import WorkspaceTarget
from advtest_ext.strategy.digester import (
    DigestDecision,
    Digester,
    DigestReport,
    ProviderDecider,
    ScriptedDecider,
)
from advtest_ext.strategy.export import build_dataset_from_strategy_runs
from advtest_ext.strategy.library import Strategy, cold_start_doc, discover
from advtest_ext.strategy.loop import (
    Rollout,
    Step,
    assemble_strategy_doc,
    iterate_sample,
    load_strategies,
    select_strategy_doc,
)
from advtest_ext.strategy.models import Attempt, RolloutResult, Sample
from advtest_ext.strategy.orchestrator import (
    IterativeAttack,
    RunResult,
    SampleOutcome,
)
from advtest_ext.strategy.persist import lineage_id_for, persist_run
from advtest_ext.strategy.replay import (
    ReplayStat,
    persist_replays,
    replay_candidate,
    replay_run,
)
from advtest_ext.strategy.router import COLD_START_ID, ColdStartRouter, ProviderRouter
from advtest_ext.strategy.seed import (
    render_seed_strategy,
    seed_library,
    strategy_id_for_guide,
)
from advtest_ext.strategy.targets import AttackTarget, OfflineMarkerTarget

__all__ = [
    "COLD_START_ID",
    "AgentDojoTarget",
    "AttackTarget",
    "Attempt",
    "ColdStartRouter",
    "DigestDecision",
    "DigestReport",
    "Digester",
    "IterativeAttack",
    "OfflineMarkerTarget",
    "ProviderAttacker",
    "ProviderDecider",
    "ProviderRouter",
    "ReplayStat",
    "Rollout",
    "RolloutResult",
    "RunResult",
    "Sample",
    "SampleOutcome",
    "ScriptedAttacker",
    "ScriptedDecider",
    "Step",
    "Strategy",
    "WorkspaceTarget",
    "assemble_strategy_doc",
    "build_dataset_from_strategy_runs",
    "cold_start_doc",
    "discover",
    "iterate_sample",
    "lineage_id_for",
    "load_strategies",
    "persist_replays",
    "persist_run",
    "render_seed_strategy",
    "replay_candidate",
    "replay_run",
    "seed_library",
    "select_strategy_doc",
    "strategy_id_for_guide",
]

__citations__: list[str] = [
    "wang2026piminer",  # Agent Against Agent (PIMiner), arXiv 2608.05108
]
