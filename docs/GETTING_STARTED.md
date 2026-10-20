# Getting Started

> Cold laptop to a live run and a dashboard, in order. Do the sections top to bottom the
> first time. After that, the **Daily loop** at the bottom is all you need.
>
> Signing in the test accounts is its own step — [ACCOUNTS_AND_AUTH.md](ACCOUNTS_AND_AUTH.md).
> To find code, see [CODEBASE_MAP.md](CODEBASE_MAP.md). For how the system fits together,
> [architecture/README.md](architecture/README.md).

## 0. Prerequisites

- **Python 3.11–3.14** and **Poetry**.
- **An Anthropic API key** — this drives the attacker and judge LLMs (`ANTHROPIC_API_KEY`).
- For browser (`ui_mode`) runs: **Google Chrome/Chromium** and a Google test account with
  Gemini enabled. For API-only runs: a Gemini API key.
- Optional, for free local development: a **vLLM** server (see `scripts/start_vllm.sh`).

## 1. Install

```bash
poetry install                 # core only
poetry install --extras all    # + openai, gemini, pdf, playwright (recommended)
poetry install --extras ui_mode && poetry run playwright install chromium  # browser runs
```

`make install` is equivalent to the core install plus the pre-commit hook.

## 2. Configure the environment

Everything is read from environment variables. There is a documented contract:

```bash
cp .env.example .env      # then fill in what you need
```

`.env` is git- and docker-ignored; never commit it. Almost everything has a safe default,
so a bare dashboard runs with nothing set. The variables that matter most:

| Variable | Needed for | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | any live attack/judge | The attacker + judge LLMs. |
| `GEMINI_API_KEY` | API-mode runs against Gemini | Not needed for `ui_mode` — there the side panel *is* the model. |
| `ADVTEST_DB_URL` | pointing the dashboard + runs at one DB | Default `sqlite:///./advtest.db`. |
| `ADVTEST_DASHBOARD_TOKEN` | any shared/prod dashboard | Unset = open (dev only). |
| Auth / account vars | live Workspace targets | See [ACCOUNTS_AND_AUTH.md](ACCOUNTS_AND_AUTH.md). |

The full annotated list is in [`.env.example`](../.env.example).

## 3. See it work with no external calls (30 seconds)

Seed demo data and open the dashboard — no LLM, no browser, no keys required:

```bash
make demo        # seeds three demo runs, starts the dashboard
# open http://localhost:8000
```

This is the fastest way to confirm the install is sound and to see what a run *looks like*
before you spend any API budget. `make seed` seeds without starting the server;
`make dashboard` starts the server without reseeding.

## 4. Run a real attack

A **run** is a YAML config (an attack × a target × a judge). Run one with the CLI:

```bash
poetry run advtest run --config configs/<name>.yaml
```

Useful configs to start from (see `configs/`):

- `demo_docs_api.yaml`, `demo_safe.yaml` — API-mode, cheapest to try.
- `smoke_workspace_gmail_ui.yaml` — a short `ui_mode` smoke run (needs a signed-in profile).
- `run_workspace_*_ui.yaml` — full browser sweeps (hours of wall-clock; read the header first).

Every run config's header comments list its own pre-flight steps and rough cost/time —
**read them before launching a sweep.** The CLI writes per-trace JSON under
`out/<run_id>/`, an aggregate `run_result.json`, and Run/Trace/Finding rows to the DB, so
the dashboard shows the run as it streams. Point both at one DB:

```bash
export ADVTEST_DB_URL=sqlite:///./advtest.db   # same value for run and dashboard
```

`advtest run` flags worth knowing: `--strategy grid|mcts`, `--output-dir`,
`--db-url ''` (disable the DB sink, file-only), and per-role model overrides
(`--attacker-model`, `--judge-model`, `--target-model`).

## 5. Run a guide (a reusable recipe)

A **guide** is human-authored JSON in `guides/` that compiles into a run — the replayable
unit of an engagement. Run one with:

```bash
poetry run python scripts/run_guide.py --help
```

The live Sheets guide has a dedicated walk-through:
[SHEETS_UI_MODE_RUNBOOK.md](SHEETS_UI_MODE_RUNBOOK.md).

## 6. The full cross-app demo

`make fission` runs the headed end-to-end story: deliver cross-inbox bait, wait for
indexing, then run the two-phase Gmail→Docs / Docs→Docs attack, all into the DB the
dashboard serves. Run `make dashboard` in one terminal, `make fission` in another. The
operator runbook is [`scripts/README_DEMO.md`](../scripts/README_DEMO.md). Accounts are
overridable: `make fission GMAIL=you@gmail.com OUTLOOK=you@outlook.com`.

## 7. Tests, lint, types

```bash
make test        # pytest (excludes gpu + integration markers)
make ruff        # lint + format check
make mypy        # strict type check
make integration # Workspace integration tests (needs real creds + ADVTEST_WORKSPACE_INTEGRATION=1)
```

## Daily loop (after first setup)

```bash
make dashboard                                   # terminal 1: the UI on :8000
poetry run advtest run --config configs/<x>.yaml # terminal 2: a run streaming into it
make test                                        # before you commit
```

## If something breaks

- **Dashboard empty:** the run and the dashboard are on different DBs. Set the same
  `ADVTEST_DB_URL` for both.
- **`ui_mode` run can't reach Gemini:** the Chrome profile isn't signed in, or the side
  panel isn't enabled for that account. See [ACCOUNTS_AND_AUTH.md](ACCOUNTS_AND_AUTH.md).
- **Two `ui_mode` runs at once corrupt the profile:** browser runs share one Chrome
  profile — hard cap of one at a time, or use the account pool.
- **Auth errors on Workspace targets:** wrong or expired token cache / mode. See the
  auth guide.
