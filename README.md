# Fission (advtest_ext)

Document-centric adversarial testing for AI assistants embedded in productivity
suites — primarily **indirect prompt injection (IPI)** against Google Workspace
Gemini (Docs / Gmail / Sheets side panels), plus unauthorized-action testing
against tool-using agents.

Extracted from a PyRIT fork into a standalone package; it re-implements its own
attack/target/judge/provider abstractions and depends on PyRIT only as an
optional interop extra (a single `TYPE_CHECKING` import).

## Layout

```
advtest_ext/        the package
  core/             orchestrator, models, recipe engine, run config
  attacks/          PAIR, BoN, MSJ, PAP, Crescendo, TAP-Crescendo, indirect injection
  adapters/         targets: gmail, google_docs, sheets, outlook, agentdojo, tool_use_agent
                    (api_mode + ui_mode Playwright drivers under _ui_mode_common)
  judges/           heuristic, StrongREJECT, IPI-verdict, ensembles, HITL, signals
  providers/        anthropic, gemini, openai, local vLLM (+ cost tracking)
  document/         format-independent AbstractDocument model + DocumentTarget ABC
  corpus/           plant poisoned docs/emails for retrieval-IPI
  storage/          SQLAlchemy persistence, JSONL backup, replay bundles
  dashboard/        FastAPI + HTMX run/finding/tree views + reports
tests/              unit tests (no GPU / no live LLM by default)
configs/            run + provider YAML configs
docs/               FOUNDATIONS.md, FISSION_*.md (design/research), references.bib
scripts/            operational + demo scripts
```

## Setup

```bash
poetry install                 # core
poetry install --extras all    # + openai, gemini, pdf, ui_mode
poetry install --extras ui_mode && poetry run playwright install chromium  # browser UI mode
```

## Run

```bash
poetry run pytest              # tests (excludes gpu/integration markers)
poetry run advtest --help      # CLI
```

## Notes

- Adversarial seed content lives in `advtest_ext/**/*.yaml` (payloads,
  demonstrations, templates). Handle per engagement policy.
- Run artifacts (`out/`, `*.db`, exports) are git-ignored by design.
- Design + research docs are in `docs/` — start with `docs/FOUNDATIONS.md` and
  `docs/FISSION_ATTACK_LANDSCAPE.md`.
