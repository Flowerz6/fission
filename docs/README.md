# Fission docs — index

> The front door. Every doc in `docs/` is listed here with one line on what it's for.
> Returning after time away? Read the three **Start here** docs and the architecture
> onboarding guide; reach for the rest only when you need the depth.

> 🧭 **New engineer?** The canonical onboarding layer is
> **[architecture/README.md](architecture/README.md)** — the system stage by stage, with
> diagrams. Start there.

## Start here (operating the tool)

| Doc | Read it when |
|---|---|
| [GETTING_STARTED.md](GETTING_STARTED.md) | You want to install, configure, and run your first attack + dashboard. |
| [ACCOUNTS_AND_AUTH.md](ACCOUNTS_AND_AUTH.md) | You need to sign in the Google/Outlook test accounts so live runs work. |
| [CODEBASE_MAP.md](CODEBASE_MAP.md) | You need to find the code that does X, or reorient in the tree. |

## Architecture (how it fits together)

| Doc | For |
|---|---|
| [architecture/README.md](architecture/README.md) | The onboarding guide — the whole system, stage by stage (01–09), with diagrams. |
| [INTEGRATION_CONTRACT.md](INTEGRATION_CONTRACT.md) | The typed measurement-layer contract (`AttackTrace`/signals/verdicts) between the orchestrator and the oracle. |
| [FISSION_MEASUREMENT_SPINE.md](FISSION_MEASUREMENT_SPINE.md) | The oracle grading + ASR ± CI measurement design the code implements (cited by `core/`, `storage/`, `judges/`). |

## Authoring attacks & seeds

| Doc | For |
|---|---|
| [FISSION_GUIDE_FORMAT.md](FISSION_GUIDE_FORMAT.md) | The guide (attack recipe) schema. |
| [SEED_AUTHORING_GUIDE.md](SEED_AUTHORING_GUIDE.md) | How to author seeds. |
| [SEED_GENERATOR_PLAN.md](SEED_GENERATOR_PLAN.md) | The seed-generator design + coverage worklist toward the pilot set. |
| [SEARCH_TREE_DESIGN.md](SEARCH_TREE_DESIGN.md) | The attack search tree: category fan-out, cover/payload split, run-level dedup. |
| [MUTATION_AXES_TAXONOMY.md](MUTATION_AXES_TAXONOMY.md) | The mutation-axis taxonomy the attack search fans out over. |

## Running live & delivering

| Doc | For |
|---|---|
| [SHEETS_UI_MODE_RUNBOOK.md](SHEETS_UI_MODE_RUNBOOK.md) | Running a Sheets guide live against the Gemini side panel, step by step. |
| [WS3_LIVE_VALIDATION_RUNBOOK.md](WS3_LIVE_VALIDATION_RUNBOOK.md) | The live-account validation runbook. |
| [../scripts/README_DEMO.md](../scripts/README_DEMO.md) | The multi-app cross-inbox IPI flow — operator runbook. |
| [PILOT_DELIVERY_FORMAT.md](PILOT_DELIVERY_FORMAT.md) | How pilot deliverables are shaped for a client. |

## Results & verification

| Doc | For |
|---|---|
| [SUCCESS_VERDICTS.md](SUCCESS_VERDICTS.md) | Full-corpus, payload-grounded success-label verification. |
| [SUCCESSFUL_ATTACKS_REPORT.md](SUCCESSFUL_ATTACKS_REPORT.md) | Confirmed successful attacks on Gemini-in-Workspace. |

## Research grounding

| Doc | For |
|---|---|
| [FOUNDATIONS.md](FOUNDATIONS.md) | Threat model, the papers/benchmarks this aligns with, technique choices. |
| [../references.bib](../references.bib) | Citations referenced from code and docs. |
