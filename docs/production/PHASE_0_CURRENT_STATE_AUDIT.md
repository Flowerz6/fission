# Phase 0 — Current-State Audit

> The honest baseline. Findings were verified against the repo on this branch
> (`measurement-spine-and-oracle-grading`). Each finding has a severity, the evidence
> that grounds it, and the phase that fixes it. Nothing here is fixed yet — Phase 0 is
> assessment only.

**Objective:** produce a triaged list of everything standing between the current repo and
a production-clean, auditable one, so the later phases execute against facts, not guesses.

**Exit gate:** every finding is triaged — fix (assigned to a phase), accept (with reason),
or defer (with a trigger).

## What is already good (do not rebuild)

- **CI is real and green.** `.github/workflows/ci.yml` runs ruff lint, ruff format check,
  mypy, and pytest across Python 3.11 and 3.12. `mypy` runs in `strict` mode.
- **CD exists and is safely gated.** `cd.yml` builds and pushes an image to GHCR on merge
  to `main`; the Cloud Run deploy job stays skipped until `DEPLOY_ENABLED=true`.
- **Secrets hygiene is sound.** No secrets are tracked. `.env` is git-ignored, `.env.example`
  documents the contract, and `.gitignore` blocks key/token/service-account patterns.
- **Pre-commit mirrors CI.** `.pre-commit-config.yaml` pins the same ruff/mypy from
  `poetry.lock`, avoiding tool drift.
- **The test suite is substantial.** 80 test files covering orchestrator, adapters, judges,
  the measurement spine, and the dashboard.

These are the marks of a codebase that was engineered, not vibe-coded. The gaps below are
about the repo *around* the code.

## Findings

Severity: **H** blocks a clean/auditable handoff · **M** should fix before release ·
**L** polish.

| # | Severity | Finding | Evidence | Fixed in |
|---|---|---|---|---|
| F1 | H | Large uncommitted working tree: 83 modified, 41 untracked files, including new feature modules (`_cli_calibrate.py`, `adapters/calendar/`, `adapters/slides/`, `attacks/tap_search.py`, `delivery/`, `guides/mutation_runtime.py`) and their tests. Work is unreviewable and at risk until committed. | `git status --porcelain` | Phase 1 |
| F2 | H | History contains "large snapshot" commits. The latest, `773a480`, bundles the measurement spine, guides corpus, oracle grading, and dashboard changes in one commit — hard to review, bisect, or revert. | `git show --stat 773a480` | Phase 1 (going forward) |
| F3 | H | No `LICENSE`. Ownership and permitted use are undefined — a blocker for any external handoff, and doubly so for a repo carrying offensive-security content. | `ls LICENSE*` → none | Phase 2 |
| F4 | H | No `SECURITY.md` and no documented handling policy for the adversarial content. The repo ships working IPI/jailbreak payloads (`seed_data/Gemini_Guardrail_Bypass_*`, `Internal_Tool_disclosure`, `attacks/payloads/`, `guides/*secret_exfil*`). | `seed_data/` listing; `.gitignore` does not cover `seed_data/` | Phase 2 & 5 |
| F5 | M | Untracked `.docx` pilot deliverables sit at repo root and are **not** git-ignored (`.gitignore` covers `*.pdf` but not `*.docx`), so a `git add .` would commit them. | `Fission_Cross_App_Pilot.docx`, `Fission_Indirect_Prompt_Injection_Pilot.docx`; `.gitignore` | Phase 1 |
| F6 | M | Local `.db` files and backups clutter the tree: `advtest.db` (5.2 MB), two `advtest.db.backup*`, `backups/` (16 MB), `out/` (56 MB). All are git-ignored, but they bloat the working copy and are easy to leak by accident. | `ls -la`; `du -sh backups out` | Phase 1 |
| F7 | M | README package layout is stale: it omits `agents/`, `calibration/`, `delivery/`, and `guides/`, which all exist in `advtest_ext/`. A reviewer's first map of the code is wrong. | `README.md` "Layout"; `ls advtest_ext` | Phase 2 |
| F8 | M | No `CONTRIBUTING.md`, `CODEOWNERS`, or `CHANGELOG.md`. No stated branch/PR/commit conventions, no review ownership, no record of what changed between versions. | `ls` → none | Phase 2 |
| F9 | M | No release process. `version = "0.0.1"` is static, there are no git tags, and no releases. Nothing ties a running image back to a source revision beyond the commit SHA. | `git tag` → empty; `pyproject.toml` | Phase 4 |
| F10 | M | `docs/` holds 20 markdown files plus `.dot`/`.pdf` design artifacts with overlapping scope and no index. A reviewer cannot tell which docs are current. | `ls docs/` | Phase 2 |
| F11 | L | Stale remote feature branches remain after their PRs merged: `feat/guide-normalizer-hardening`, `feat/guide-run-wiring`, `feat/guide-to-tap-crescendo-mapper`, `feat/self-healing-selectors`. | `git branch -a` | Phase 1 |
| F12 | L | Minor code debt: 8 `TODO/FIXME/HACK/XXX` markers and 5 `print()` calls in `advtest_ext/` (shipped paths should log, not print). | `grep -rn` in `advtest_ext` | Phase 3 |
| F13 | L | `.DS_Store` present under `seed_data/`; macOS cruft not fully covered where new dirs are added. | `seed_data/.DS_Store` | Phase 1 |
| F14 | L | No coverage gate. `pytest-cov` is a dev dependency but CI does not measure or enforce coverage, so regressions in tested-ness are invisible. | `ci.yml`; `pyproject.toml` | Phase 3 |
| F15 | L | No root `CLAUDE.md` / agent-guidance file, despite heavy agent-assisted development. Onboarding context lives only in per-user memory. | `ls CLAUDE.md` → none | Phase 2 |

## Triage summary

| Disposition | Findings |
|---|---|
| Fix — Phase 1 | F1, F2, F5, F6, F11, F13 |
| Fix — Phase 2 | F3, F4, F7, F8, F10, F15 |
| Fix — Phase 3 | F12, F14 |
| Fix — Phase 4 | F9 |
| Fix — Phase 5 | F4 (governance half) |

No findings are marked *accept* or *defer* at this stage; revisit if any fix proves
disproportionate to its value.

## The one thing to internalise

The code is in good shape; the **repo state** is not. The highest-value, lowest-risk move
is Phase 1 — get the working tree committed in reviewable units and clear the root — because
everything else (governance files, release tags, audit trails) is only trustworthy once the
tree is a faithful record of the code.
