# Variant Diversity for TAP — design note

> **Problem.** In TAP tree mode, if the LLM attacker invents node variants freely it
> tends to *paraphrase* the seed rather than branch — the well-documented **mode-collapse**
> failure of automated red-teaming. This note records the design decision for structured,
> diverse variant generation, grounded in the 2024–2026 literature, and what is implemented
> versus planned.

## Decision

Keep TAP in **tree mode**, but make each node a **deliberate move along a named mutation
axis** rather than a free-text reword, and gate every child through the **semantic novelty
verifier** already in the codebase. This is the **Rainbow Teaming** quality-diversity
pattern (MAP-Elites over behavioural descriptors), which is the right, well-supported
*baseline*. Two upgrades from the current frontier are layered on top (see Roadmap).

## Landscape (why this direction)

- **Quality-diversity / MAP-Elites** — Rainbow Teaming (Meta, 2024) populates an archive of
  adversarial prompts across hand-defined descriptors (risk × style); RainbowPlus (2025)
  adds many-per-cell + cheaper fitness. This is our lineage.
- **The critique** — Quality-Diversity Red-Teaming (QDRT, 2025) shows hand-defined
  descriptors cause **coverage gaps** and miss emergent attacks: *emergent diversity beats
  engineered diversity*. Implication: hand operators are a good **prior**, not the whole
  diversity signal.
- **Mode collapse is the central named problem**; novelty rewards and GFlowNets help but
  still collapse. The newest idea, Active Attacks (2026), co-evolves an adaptive
  environment — powerful but needs victim retraining, so it is inspiration only for a
  black-box target.
- **Multi-turn crescendo is now the dominant break path** (Crescendo, ActorAttack, TROJail
  trajectory-optimization). Our live win (Sheets role-confusion) is a crescendo, so the
  high-value variants are diverse **escalation trajectories**, not single prompts.
- **Self-evolving skill libraries** — RedEvoAgent (2026) distils winning trajectories into
  human-readable "attack skills" and evolves them with a **validation ratchet** (keep only
  improvements). This is exactly our guides + promotion gate, learned instead of authored.

Full source list at the bottom.

## What is implemented (now)

1. **Typed mutation operators on `sheets_skill_leak`** (`guides/sheets_skill_leak.yaml`).
   Six operators across real dimensions — `persona`, `turn_order`, `escalation_speed`,
   `framing` (×2: proof-pressure and probe-target), `wording` — each with discrete
   `cell_values` (the concrete alternatives to explore). These are the "paths" the branch
   strategist mutates along.
2. **Novelty gate ON for variants.** `run_guide(..., semantic_validation_required=True,
   checker_role="CLAUDE_JUDGE", hard_invariant_validation=False)`. The gate
   (`attacks/tap_crescendo.py`, `_CANDIDATE_CHECK_*`) rejects any child that "merely
   paraphrases … unless the selected dimension is wording," or that fails to materially
   change its named dimension. `hard_invariant_validation` stays off so the verbatim seed
   root still runs (the guide's invariants are mechanism descriptions, used as the gate's
   boundary, not prompt-checkable pre-conditions).
3. **Tree driver** — `scratchpad/run_sheets_tree.py` (`DEPTH`/`BRANCH` env), `replay_mode=
   "both"` so the root replays Bikash's seed verbatim and children branch as gated variants.

The guide's `mutation_operators` (6), `strategy_brief` (Bikash's approach), and `invariants`
(4) all verified to flow into the attack config.

## Roadmap — the two frontier upgrades (planned, against exact seams)

**Upgrade 1 — effect-space (behavioural) novelty, not just engineered cells (QDRT).**
Today the MAP-Elites cell is `slug(feature_value)` (`attacks/tap_search.py`, `ArchiveElite.cell`)
— an engineered descriptor. Add a **behavioural descriptor** from the decomposed oracle's
`(attended, complied, harmful_effect)` signature (`judges/decomposed_ipi.py`) and fold it
into the cell key, e.g. `cell = f"{feature_value}@{effect_bucket}"`. Then coverage is
measured in *effect space*: two variants on the same authored axis that produce different
effects are both retained, and same-effect rewrites are dedup-pressured. Requires wiring the
decomposed judge as a descriptor scorer in the node-eval path and versioning the persisted
archive schema — a test-gated change.

**Upgrade 2 — skill-distillation ratchet (RedEvoAgent → our flywheel).** When a variant
wins under the promotion gate, **distil it back into a new `mutation_operator`/guide** so the
authored operator set grows from experience instead of by hand. The promotion gate is the
"validation ratchet" (keep only improvements). This is the measurement-spine → flywheel work
already in the roadmap; this note gives it literature backing.

## For our situation

Single-shot indirect injection on Gemini-in-Gmail is broadly defended as of 2026-09
([[fission-tip-vs-gemini-intent-defense]]); multi-turn crescendo on Sheets still works. So
point variant exploration at **crescendo trajectories on the surfaces that still work**
(Sheets), score novelty in the decomposed-oracle effect space, and route winners back
through the promotion gate. That is defensibly current and reuses machinery we already have.

## Sources

- Rainbow Teaming — arXiv:2402.16822 · RainbowPlus — arXiv:2504.15047 · QDRT — arXiv:2506.07121
- Active Attacks — arXiv:2509.21947 · Learning diverse attacks (GFlowNet, ICLR 2025) — arXiv:2405.18540
- OpenAI diverse RL red-teaming — arXiv:2412.18693
- TAP — arXiv:2312.02119 · Graph of Attacks with Pruning (2025) — ACL WOAH 2025.woah-1.44
- Crescendo — arXiv:2404.01833 · TROJail — arXiv:2512.07761 · ActorAttack (SafeMTData, in `seeds_corpus/`)
- RedEvoAgent — arXiv:2608.27439 · Promptware "Invitation Is All You Need" — arXiv:2508.12175
- IPI in the Wild — arXiv:2604.27202
