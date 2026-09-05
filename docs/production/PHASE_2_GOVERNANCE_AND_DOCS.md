# Phase 2 — Governance & Docs

> Make the repo explain, license, and govern itself. A reviewer should be able to answer
> "what is this, who may use it, how do I contribute, and where do I report a
> vulnerability" from files in the root, and every doc they read should match the code.

**Objective:** the standard governance files exist and are accurate; the README and `docs/`
tree tell the truth about the current code.

**Exit gate:** `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, and `CODEOWNERS` exist; README
layout matches `advtest_ext/`; `docs/` has an index and no doc contradicts the code.

## Tasks

| # | Task | File | What | Effort |
|---|---|---|---|---|
| 2.1 | Choose and add a license (F3) | `LICENSE` | Decide the terms deliberately — this repo carries offensive-security tooling and payloads, so a permissive OSS license may be inappropriate. Options: proprietary "all rights reserved", a source-available license, or a restricted-use license. Get the owner's decision; do not default to MIT. Reflect the choice in `pyproject.toml` metadata. | M |
| 2.2 | Security & responsible-use policy (F4) | `SECURITY.md` | State: how to report a vulnerability in the tool; that the repo contains working IPI/jailbreak content; that use is limited to authorized security testing under engagement scope; the pointer to the data-handling rules (Phase 5). This is the single most important governance file for a repo like this. | M |
| 2.3 | Contribution guide (F8) | `CONTRIBUTING.md` | Document the real workflow: Poetry setup, `make test`/`ruff`/`mypy`, pre-commit, Conventional Commits, branch-per-PR, and the "no snapshot commits / tests alongside code" rules from Phase 1. Include the sensitive-content rule: never add raw payloads without following the handling policy. | S |
| 2.4 | Ownership (F8) | `CODEOWNERS` (`.github/`) | Assign review ownership by area (`advtest_ext/core/`, `judges/`, `adapters/`, `dashboard/`). Even a single-owner file makes review routing explicit and auditable. | S |
| 2.5 | Changelog (F8, feeds Phase 4) | `CHANGELOG.md` | Start a Keep-a-Changelog file. Seed it from `git log` (the history is already Conventional-Commit shaped). From here, every release section is generated from commits. | S |
| 2.6 | Fix README layout drift (F7) | `README.md` | Add the missing package dirs to the Layout block: `agents/`, `calibration/`, `delivery/`, `guides/`. Verify every other line against `ls advtest_ext`. Add a one-line "adversarial content — handle per policy" pointer to `SECURITY.md`. | S |
| 2.7 | Index and de-dup `docs/` (F10) | `docs/README.md` | Add a `docs/` index that lists each doc with a one-line purpose and a status (current / historical / superseded). Mark clearly which of the 20 existing docs are living design docs vs point-in-time artifacts. Move rendered `.pdf`/`.dot` build outputs to an ignored location or a clearly-labelled `docs/rendered/`. | M |
| 2.8 | Agent-onboarding file (F15) | `CLAUDE.md` (root) | Capture the non-obvious repo conventions in-repo (not just in per-user memory): the Poetry/Make workflow, the "no in-session benchmark runs" rule, the sensitive-content rule, and the measurement-spine mental model. Keeps agent-assisted work consistent and auditable. | S |
| 2.9 | Reconcile product-roadmap docs | `docs/FISSION_*` | The roadmap/build-map/phase-tasks docs were written before the measurement spine landed; the spine is now largely built (per the last commit). Add a dated "status as of" banner to each so a reader knows they describe intent, not current state. (Restructuring them is a separate task the user opted out of here.) | M |

## Sequencing

2.1 (license) gates external sharing, so do it first. 2.2 (security policy) is the
highest-value file for this repo's risk profile — do it second, and let Phase 5 deepen it.
2.3–2.6 are quick and independent. 2.7 and 2.9 (docs reconciliation) are the larger,
judgement-heavy items; schedule them after the quick wins.

## Notes on the license decision (2.1)

This is a genuine decision for the owner, not a formality. A conventional permissive license
would let anyone redistribute the jailbreak/IPI payloads and adapters. Weigh:

- **Proprietary / all-rights-reserved** — simplest guard; no third-party rights granted.
- **Source-available, restricted use** — allows inspection and authorized-testing use while
  forbidding redistribution and hostile use.
- **Dual-track** — permissive for the harness/framework, restricted for the payload corpus.

Whatever is chosen, `SECURITY.md` (2.2) must reinforce it with a responsible-use statement.

## Definition of done

- [ ] `LICENSE` present; `pyproject.toml` license field matches.
- [ ] `SECURITY.md` covers vulnerability reporting **and** responsible-use of the payloads.
- [ ] `CONTRIBUTING.md` documents the real dev + commit + sensitive-content workflow.
- [ ] `CODEOWNERS` and `CHANGELOG.md` exist.
- [ ] README Layout block matches `ls advtest_ext` exactly.
- [ ] `docs/README.md` indexes and status-marks every doc; roadmap docs carry a dated banner.
- [ ] Root `CLAUDE.md` captures the non-obvious conventions.
