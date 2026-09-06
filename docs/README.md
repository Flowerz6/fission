# Fission docs — index

> The front door. Every doc in `docs/` is listed here with one line on what it's for and
> when to read it. If you're returning to this project after time away, read the four
> **Start here** docs and nothing else until you need more.

## Start here (operating the tool)

| Doc | Read it when |
|---|---|
| [GETTING_STARTED.md](GETTING_STARTED.md) | You want to install, configure, and run your first attack + dashboard. |
| [ACCOUNTS_AND_AUTH.md](ACCOUNTS_AND_AUTH.md) | You need to sign in the Google/Outlook test accounts so live runs work. |
| [CODEBASE_MAP.md](CODEBASE_MAP.md) | You need to find the code that does X, or reorient in the tree. |
| [MILESTONES.md](MILESTONES.md) | You want the ordered plan — what to build next, as checkable steps. |

## Runbooks (specific live-run recipes)

| Doc | For |
|---|---|
| [SHEETS_UI_MODE_RUNBOOK.md](SHEETS_UI_MODE_RUNBOOK.md) | Running the Sheets skill-leak guide live against Gemini, step by step. |
| [../scripts/README_DEMO.md](../scripts/README_DEMO.md) | The multi-app cross-inbox demo (`make fission`) operator runbook. |
| [PILOT_DELIVERY_FORMAT.md](PILOT_DELIVERY_FORMAT.md) | How pilot deliverables are shaped for a client. |
| [PILOT_ADAPTER_ACTIONS.md](PILOT_ADAPTER_ACTIONS.md) · [PILOT_ADAPTER_BUILD_PLAN.md](PILOT_ADAPTER_BUILD_PLAN.md) | Pilot adapter action surface + build plan. |

## Architecture & design (how it works)

| Doc | For |
|---|---|
| [FISSION_ARCHITECTURE_AND_PRODUCTION.md](FISSION_ARCHITECTURE_AND_PRODUCTION.md) | The full end-to-end architecture and the gap to production. |
| [FISSION_MEASUREMENT_SPINE.md](FISSION_MEASUREMENT_SPINE.md) | The oracle grading + ASR ± CI measurement design. |
| [FISSION_FLYWHEEL.md](FISSION_FLYWHEEL.md) | The self-improvement loop (the "(c)" thesis). |
| [FISSION_DIAGRAMS.md](FISSION_DIAGRAMS.md) · `fission_*.dot` | Diagrams and their sources. |
| [FISSION_GUIDE_FORMAT.md](FISSION_GUIDE_FORMAT.md) | The guide (attack recipe) schema. |

## Product roadmap (what to build, and why)

| Doc | For |
|---|---|
| [FISSION_ROADMAP.md](FISSION_ROADMAP.md) | The phased, GO/NO-GO-gated plan to the full vision. |
| [FISSION_PHASE_TASKS.md](FISSION_PHASE_TASKS.md) | Concrete engineering tasks per phase, against real files. |
| [FISSION_BUILD_MAP.md](FISSION_BUILD_MAP.md) | Target architecture overlaid on what's built today (🟩/🟨/🟥). |
| [FISSION_IMPLEMENTATION_PLAN.md](FISSION_IMPLEMENTATION_PLAN.md) | Implementation detail behind the roadmap. |
| [FISSION_DEPLOYMENT.md](FISSION_DEPLOYMENT.md) | Deployment topology and the browser-farm effort. |

> **[MILESTONES.md](MILESTONES.md) reconciles all of the above** into one ordered, solo-sized
> checklist. Use it as the driver; use these for depth.

## Research grounding

| Doc | For |
|---|---|
| [FOUNDATIONS.md](FOUNDATIONS.md) | Threat model, the papers/benchmarks this aligns with, technique choices. |
| [FISSION_ATTACK_LANDSCAPE.md](FISSION_ATTACK_LANDSCAPE.md) | Survey of the attack space. |
| [RED_TEAM_RESEARCH_UPGRADES.md](RED_TEAM_RESEARCH_UPGRADES.md) · [TAP_ATTACK_SUCCESS_UPGRADES.md](TAP_ATTACK_SUCCESS_UPGRADES.md) | Research-driven upgrades to attacks/scoring. |
| [SEED_AUTHORING_GUIDE.md](SEED_AUTHORING_GUIDE.md) · [SEED_AUTHOR_REPORTING.md](SEED_AUTHOR_REPORTING.md) | Authoring seeds and reporting on them. |
| [../references.bib](../references.bib) | Citations referenced from code and docs. |

## Repo productionisation (clean + auditable)

A separate track from the product roadmap. See [production/README.md](production/README.md)
for the phase map. Phases 0–5 cover current-state audit, repo hygiene, governance & docs,
quality & CI, release & ops, and auditability & compliance.
