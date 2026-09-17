# Codebase Map

> Where everything lives and what it does. This is the navigation guide — read it
> when you need to find the code that does X, or to reorient after time away.
> Keyed to the current tree (199 package modules, 80 test files).
>
> Companion docs: [GETTING_STARTED.md](GETTING_STARTED.md) (how to run) ·
> [ACCOUNTS_AND_AUTH.md](ACCOUNTS_AND_AUTH.md) (how to sign in) ·
> [FISSION_ARCHITECTURE_AND_PRODUCTION.md](FISSION_ARCHITECTURE_AND_PRODUCTION.md)
> (the deep architecture write-up).

## The one-paragraph mental model

You define a **run** in a YAML config (an attack × a target × a judge, over a set of
behaviours). The **orchestrator** executes each cell: an **attack** crafts adversarial
content, an **adapter** delivers it to a **target** (a live Gemini side panel over a
browser, or an API model), and a **judge** grades whether the injection succeeded. Every
attempt becomes a **Trace**; successful ones become **Findings**. Everything is written to
a database and rendered by the **dashboard**. A **guide** is a reusable, human-authored
attack recipe that compiles down into a run.

```
config.yaml ─▶ orchestrator ─▶ attack ─▶ adapter ─▶ target (Gemini/API)
                    │                                      │
                    ▼                                      ▼
                 judge  ◀────────────────────────── response
                    │
                    ▼
             Trace + Finding ─▶ storage (DB) ─▶ dashboard
```

## Top-level directories

| Path | What it is |
|---|---|
| `advtest_ext/` | The Python package — all the product code. |
| `configs/` | Run configs and provider (model) configs, both YAML. |
| `guides/` | Human-authored attack recipes (JSON). The reusable, replayable unit. |
| `seeds_corpus/` | Normalised seed corpus — real engagement chains + public-dataset entries. |
| `seed_data/` | Raw engagement material (POC findings, transcripts). Sensitive. |
| `scripts/` | Operational + demo scripts (setup, e2e runs, exports, calibration). |
| `tests/` | Unit tests. No GPU or live LLM by default. |
| `docs/` | Design, research, operating, and roadmap docs (see [docs/README.md](README.md)). |
| `out/` | Run artifacts — traces, screenshots, replays. Git-ignored. |
| `Makefile` | The local developer interface (`make demo`, `make fission`, `make test`). |

## Inside `advtest_ext/` — subsystem by subsystem

### `core/` — the engine
The orchestrator and the data model. Start here to understand control flow.
- `orchestrator.py` — runs a config: `GridOrchestrator` (exhaustive sweep) and
  `MctsOrchestrator` (guided search). Splits into `orch_budget.py`, `orch_summary.py`,
  `orch_trace_meta.py`.
- `models.py` — the core Pydantic types: `RunConfig`, `RunResult`, `Budget`, `SummaryStats`.
- `recipe.py`, `recipe_runner.py`, `recipe_operations.py` — the recipe engine (multi-step runs).
- `account_pool.py` — the multi-account model (attacker / recipient / bystander roles →
  credentials + browser profiles). See [ACCOUNTS_AND_AUTH.md](ACCOUNTS_AND_AUTH.md).
- `ppi.py`, `sampling.py`, `uncertainty.py`, `agreement.py` — the measurement spine
  (prediction-powered ASR ± CI, stratified sampling, inter-rater agreement).
- `surface.py`, `seed.py`, `seed_behaviors.yaml` — behaviour/surface definitions.

### `attacks/` — how adversarial content is crafted
One module or package per technique. `base.py` is the interface.
- Single-shot / search: `static.py`, `template.py`, `tap_chain.py`, `tap_crescendo.py`,
  `tap_search.py`, `multi_turn_refine.py`, `tip_encoding.py`.
- Technique packages: `pair/`, `bon/`, `msj/`, `indirect_injection/`.
- `payloads/` — the actual injection strings/templates (sensitive content).
- `pyrit_wrappers/` — optional interop with upstream PyRIT.

### `adapters/` — the targets (where the attack is delivered)
Each target has an **api_mode** (calls a model API) and a **ui_mode** (drives a real
browser via Playwright). `_ui_mode_common/` holds the shared browser driver;
`_auth.py` is the shared credential resolver.
- `gmail/`, `google_docs/`, `sheets/`, `slides/`, `calendar/` — Google Workspace surfaces.
- `outlook/` — Microsoft mail (used as a cross-app bait sender).
- `agentdojo/`, `tool_use_agent/` — tool-using-agent targets (unauthorized-action testing).

### `judges/` — did the attack succeed?
The layered oracle. `base.py` is the interface; `ensemble.py` combines tiers.
- Pattern / heuristic (L0): `heuristic.py`, `signals/`.
- LLM judges (L2): `ipi_verdict.py`, `decomposed_ipi.py`, `strong_reject.py`,
  `retrieval_verdict.py`, `retrieval_verdict_judge.py`.
- Model-based classifiers: `harmbench.py`, `llama_guard.py`, `shield_gemma.py`.
- Human-in-the-loop (L3): `hitl.py`.

### `providers/` — the LLMs behind the scenes
Abstraction over model backends with cost tracking. `base.py`, `factory.py`.
- `anthropic.py` + `anthropic_cached.py` (the attacker/judge), `gemini.py` (target),
  `openai_provider.py`, `local_vllm.py` (free local dev via vLLM), `cost.py`.

### `guides/` — reusable attack recipes
A guide is human-authored JSON (see `guides/*.json`) that compiles into a run.
- `models.py` — the `Guide` schema (objective, target, strategy, `key_moves`,
  `mutation_surface`, `invariants`, provenance).
- `corpus.py`, `normalize.py` — load and normalise guides.
- `to_tap_crescendo.py` — compile a guide into an executable attack.
- `run.py`, `stage.py`, `mutation_runtime.py` — execute guides, stage multi-account setup,
  mutate guides at runtime.

### `document/` — the format-independent document model
- `document.py`, `blocks.py`, `elements.py` — the `AbstractDocument` model.
- `target.py` — the `DocumentTarget` ABC every Workspace adapter implements.

### `corpus/` — planting poisoned content for retrieval-IPI
- `docs_seeder.py`, `gmail_seeder.py` — plant poisoned docs/emails a target will retrieve.
- `indexing.py`, `artifact.py` — indexing + artifact handling.

### `storage/` — persistence
- `models.py` — SQLAlchemy tables (Run, Trace, Finding, HitlItem, …).
- `repository.py` — the query/write API. `database.py` — engine/session factories.
- `jsonl.py` — JSONL backup sink. `replay.py` — replay bundles. `artifacts.py` — pluggable
  artifact store (local disk or GCS).

### `dashboard/` — the FastAPI + Jinja + HTMX web UI
App factory: `app.py:create_app`. This is what `make dashboard` serves on port 8000.
- Views: `overview.py`, `tree.py`, `transcript.py`, `presentation.py`, `drift.py`,
  `seed_ledger.py`, `references.py`, `registry.py`, `live_tests.py`,
  `intercepted_extract.py`.
- Reports: `report.py`, `report_docx.py`, `transcript_report.py`.
- `templates/`, `static/` — Jinja templates and assets. (Not React — see the memory note.)

### `delivery/` — client deliverable export
- `export.py`, `schema.py`, `journeys.py`, `harm_taxonomy.py` — the pilot export pipeline
  (turns findings into the client report dataset).

### `calibration/` + `agents/`
- `calibration/` — ingest external datasets and calibrate the oracle (`datasets.py`, `ingest.py`).
- `agents/extractor.py` — LLM that compiles seeds into guides/playbooks.

### CLI (`cli.py` + `_cli_*.py`)
Entry point `advtest` (defined in `pyproject.toml`). Commands:
- `run` — execute a RunConfig YAML (`--config`, `--strategy grid|mcts`, model overrides).
- `ingest-seed`, `import-run`, `calibrate`, `rescore`, `run-recipe`.

Name resolution (attack/target/judge strings → classes) lives in `_cli_factories.py`;
provider/DB resolution in `_cli_providers.py`. To add an attack or target, append to the
registry there.

## Where do I go to change X?

| I want to… | Go to |
|---|---|
| Add a new attack technique | `attacks/` + register in `_cli_factories.py` |
| Add a new target surface | `adapters/<surface>/` (api_mode + ui_mode) + register |
| Change how success is judged | `judges/` (pattern → `signals/`/`heuristic.py`; LLM → `ipi_verdict.py`) |
| Change what the dashboard shows | `dashboard/<view>.py` + `templates/` |
| Author a new attack recipe | `guides/*.json` (schema in `guides/models.py`) |
| Change the DB schema | `storage/models.py` + `storage/repository.py` |
| Add/tune a model backend | `providers/` + a `configs/*.yaml` provider profile |
| Define a new run | a new `configs/run_*.yaml` |
| Fix authentication / accounts | `adapters/_auth.py`, `core/account_pool.py` |
