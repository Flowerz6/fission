# advtest_ext Makefile.
#
# Lives alongside pyproject.toml so it can run from
# `cd advtest_ext && make <target>`. The repo-root Makefile is the
# upstream PyRIT build; do not modify it. This file is the
# advtest-shaped local developer interface.

.PHONY: demo fission test benchmark integration pitch dashboard seed install \
        ruff mypy clean help

POETRY := poetry
PY := $(POETRY) run python
ADVTEST := $(POETRY) run advtest
REPO_ROOT := $(shell git rev-parse --show-toplevel 2>/dev/null || pwd/..)
DB_URL ?= sqlite:///./advtest.db

help:
	@echo "advtest_ext Makefile targets:"
	@echo "  make install        - poetry install + pre-commit hook"
	@echo "  make demo           - seed demo data, start the dashboard, print URL"
	@echo "  make seed           - just (re)seed the demo data"
	@echo "  make dashboard      - start the dashboard (no seed)"
	@echo "  make fission        - run the unified Gmail->Docs + Docs->Docs phased run (live)"
	@echo "  make test           - run pytest"
	@echo "  make benchmark      - run all three benchmark scripts (prompts first)"
	@echo "  make integration    - run the Workspace integration tests"
	@echo "  make pitch          - full pitch-prep: seed + dashboard + open browser"
	@echo "  make ruff           - ruff lint + format check"
	@echo "  make mypy           - mypy strict type check"
	@echo "  make clean          - drop the local SQLite DB + out/ tree"

install:
	$(POETRY) install --with dev --extras "pdf"

# ---------------------------------------------------------------------
# Demo / dashboard
# ---------------------------------------------------------------------

seed:
	@echo "Seeding demo data into $(DB_URL)..."
	$(PY) $(REPO_ROOT)/scripts/seed_demo_data.py --db-url $(DB_URL)

dashboard:
	@echo "Starting dashboard at http://localhost:8000 ..."
	@[ -f "$(REPO_ROOT)/.env" ] && echo "Loading env from $(REPO_ROOT)/.env" || echo "No .env found — live-test launching will be disabled"
	set -a; [ -f "$(REPO_ROOT)/.env" ] && . "$(REPO_ROOT)/.env"; set +a; \
		ADVTEST_DB_URL=$(DB_URL) $(POETRY) run uvicorn \
			advtest_ext.dashboard.app:create_app --factory \
			--host 0.0.0.0 --port 8000

# One-keystroke FULL end-to-end demo (headed browsers):
#   Act 0  deliver Outlook->Gmail bait + Gmail->Outlook ack (UI senders)
#   Act 1  wait for Gmail to index
#   Act 2  unified two-phase attack (Gmail->Docs, then Docs->Docs)
# Everything streams to the SAME DB the dashboard serves, under one
# run_id. Run `make dashboard` in another terminal first, then `make
# fission` here and narrate. Overridable:
#   make fission GMAIL=you@gmail.com OUTLOOK=you@outlook.com
#   make fission SKIP_DELIVER=1        # bait already delivered
GMAIL ?= f04438179@gmail.com
OUTLOOK ?= testuser961206@outlook.com
SKIP_DELIVER ?=
fission:
	@ADVTEST_DB_URL=$(DB_URL) $(PY) $(REPO_ROOT)/scripts/fission_e2e.py \
		--gmail $(GMAIL) --outlook $(OUTLOOK) --db-url $(DB_URL) \
		$(if $(SKIP_DELIVER),--skip-deliver,)

demo: seed
	@echo ""
	@echo "Dashboard starting at http://localhost:8000 ..."
	@echo "Three demo runs are seeded under run_id prefix demo_seed_*."
	@echo ""
	@ADVTEST_DB_URL=$(DB_URL) $(POETRY) run uvicorn \
		advtest_ext.dashboard.app:create_app --factory \
		--host 0.0.0.0 --port 8000

# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------

test:
	$(POETRY) run pytest

integration:
	@echo "Running Workspace integration tests."
	@echo "  ADVTEST_WORKSPACE_INTEGRATION=1 must be set with real"
	@echo "  Workspace credentials available (Docs/Gmail/Sheets + Drive)."
	ADVTEST_WORKSPACE_INTEGRATION=1 $(POETRY) run pytest -m integration -v

# ---------------------------------------------------------------------
# Benchmarks (prompted; CLAUDE.md forbids in-session execution)
# ---------------------------------------------------------------------

benchmark:
	@echo "============================================================"
	@echo "advtest benchmarks — three separate runs."
	@echo "Each is metered (cost + wall clock); will print a"
	@echo "'TO RUN MANUALLY' block. Confirm before running."
	@echo "============================================================"
	@printf "Proceed? [y/N] " && read ans && [ "$$ans" = "y" ]
	$(PY) -m advtest_ext.benchmarks.known_vulnerable
	$(PY) -m advtest_ext.benchmarks.hardened_local
	$(PY) -m advtest_ext.benchmarks.gemini_flash
	@echo ""
	@echo "After running each printed command, regenerate RESULTS.md:"
	@echo "  $(PY) -m advtest_ext.benchmarks.results"

# ---------------------------------------------------------------------
# Pitch prep
# ---------------------------------------------------------------------

pitch: seed
	@echo "Pitch prep: seeded data + dashboard launching ..."
	@( sleep 2 && \
	   ( command -v xdg-open >/dev/null && xdg-open http://localhost:8000 \
	     || command -v open >/dev/null && open http://localhost:8000 \
	     || echo "Open http://localhost:8000 in a browser." ) ) &
	@ADVTEST_DB_URL=$(DB_URL) $(POETRY) run uvicorn \
		advtest_ext.dashboard.app:create_app --factory \
		--host 0.0.0.0 --port 8000

# ---------------------------------------------------------------------
# Lint / type
# ---------------------------------------------------------------------

ruff:
	$(POETRY) run ruff check advtest_ext tests
	$(POETRY) run ruff format --check advtest_ext tests

mypy:
	$(POETRY) run mypy advtest_ext tests

# ---------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------

clean:
	@echo "Removing local SQLite databases and out/ tree."
	rm -f advtest.db benchmark_*.db *.db-journal
	rm -rf out/
