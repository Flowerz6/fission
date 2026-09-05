# Phase 1 — Repo Hygiene

> Make the working tree a faithful, reviewable record of the code, and make the root
> carry only what belongs in source control. This is the foundation every later phase
> stands on.

**Objective:** a clean `git status`, a clean repo root, and a `.gitignore` that can't be
defeated by a careless `git add .`.

**Exit gate:** the working tree is committed in logical units on a branch; `git status` is
clean; the root directory contains only source, config, and top-level docs; no local data
artifact can be committed by accident.

## Why first

Findings F1 and F2 make everything downstream untrustworthy: you cannot write an honest
CHANGELOG, tag a release, or audit provenance while 124 files of real feature work sit
uncommitted and the last commit is an unreviewable snapshot. Fix the record first.

## Tasks

| # | Task | Where | What | Effort |
|---|---|---|---|---|
| 1.1 | Inventory the working tree | repo root | Split the 41 untracked files into: real features (`_cli_calibrate.py`, `adapters/calendar/`, `adapters/slides/`, `attacks/tap_search.py`, `core/search_calibration.py`, `delivery/`, `guides/mutation_runtime.py`, `guides/stage.py`, `dashboard/live_tests.py`, `dashboard/presentation.py`) with their tests; generated collateral (`*.docx`, `*.pdf`); and data (`seed_data/`, `guides/*.json`). Decide tracked vs ignored for each. | S |
| 1.2 | Commit features in logical units | branch off current | Group the 83 modified + feature-untracked files into themed commits (e.g. "slides+calendar adapters", "tap_search attack", "delivery/pilot export", "guides mutation runtime", "dashboard live-tests view"). One concern per commit, tests alongside code. | M |
| 1.3 | Fix the `.docx` gap (F5) | `.gitignore` | Add `*.docx` (and `*.pptx`, `*.xlsx` if any) beside the existing `*.pdf` rule so generated deliverables can't be committed. Move the existing root `.docx`/`.pdf` deliverables into a git-ignored `out/deliverables/` or delete if regenerable. | S |
| 1.4 | Clear local data from root (F6) | root, `.gitignore` | Move `advtest.db`, `advtest.db.backup*`, and `backups/` out of the working copy (or into an ignored `var/` dir). Add `backups/` to `.gitignore` explicitly. Confirm `out/` (56 MB) stays ignored. None of these should ever be near a commit. | S |
| 1.5 | Sensitive-content placement decision (F4, links Phase 5) | `seed_data/`, `.gitignore` | Decide whether `seed_data/` (raw guardrail-bypass / tool-disclosure material) is tracked at all. If tracked, it must be under the handling policy from Phase 2/5. If not, ignore it and document where the canonical copy lives. Do not commit it silently either way. | M |
| 1.6 | Kill OS/editor cruft (F13) | `.gitignore`, tree | Confirm `.DS_Store` is ignored (it is) and remove the tracked/stray one under `seed_data/`. Sweep for `__pycache__`, `.pyc`, `scripts/.fontcache/` — add `scripts/.fontcache/` to ignore. | S |
| 1.7 | Prune merged remote branches (F11) | remote | After confirming their commits are in `main`, delete `feat/guide-normalizer-hardening`, `feat/guide-run-wiring`, `feat/guide-to-tap-crescendo-mapper`, `feat/self-healing-selectors`. | S |
| 1.8 | Adopt commit conventions (F2) | going forward | Agree a convention (the history already uses Conventional Commits: `feat:`, `fix:`, `chore(scope):`). Document it in Phase 2's `CONTRIBUTING.md`; apply from 1.2 onward. No more snapshot commits. | S |

## Sequencing

1.1 → 1.2 is the spine of the phase and should happen first while the context is fresh.
1.3–1.6 (ignore rules + data cleanup) can run in parallel with 1.2 but must land *before*
any `git add -A`. 1.7 is last — only after 1.2's commits confirm nothing unique is lost.

## Guardrails

- **Do not `git add -A` blindly.** Until 1.3–1.5 land, an add-all would sweep in `.docx`
  deliverables and possibly raw sensitive seed data. Stage explicitly.
- **Confirm before deleting.** For 1.4 and 1.7, verify content is captured (in a commit, or
  a known backup location) before removing anything. Read the file/branch first.
- **No history rewrite in this phase.** The snapshot commit F2 stays in history; the fix is
  discipline going forward, not a rebase. A rewrite is a Phase 4+ decision if ever.

## Definition of done

- [ ] `git status` shows a clean tree on the working branch.
- [ ] Feature work is in themed, reviewable commits (not one snapshot).
- [ ] Repo root lists only: package dir, `tests/`, `scripts/`, `configs/`, `docs/`, and
      top-level config (`pyproject.toml`, `Makefile`, `Dockerfile`, `docker-compose.yml`,
      `README.md`, `.gitignore`, lockfiles). No `.db`, `.docx`, or stray media.
- [ ] `.gitignore` blocks `*.docx`, `backups/`, `scripts/.fontcache/`, and the sensitive
      data dir if the 1.5 decision is "untracked".
- [ ] Merged remote feature branches are deleted.
