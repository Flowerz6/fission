# Phase 3 — Quality Gates & CI

> Turn quality from something the team *does* into something the pipeline *enforces*. The
> foundation is already strong (strict mypy, ruff, pytest on 3.11/3.12); this phase closes
> the remaining gaps and makes regressions impossible to merge silently.

**Objective:** CI enforces coverage, lint, format, and types on every PR; shipped code
paths carry no debug/print/TODO debt; the test suite is fast and its markers are respected.

**Exit gate:** a PR that drops coverage, adds a lint/type error, or introduces a `print()`
in a shipped path cannot go green.

## What already holds (keep it)

- `ci.yml`: ruff lint + ruff format check + strict mypy + pytest, matrixed on 3.11 and 3.12.
- `pyproject.toml`: mypy `strict`, ruff rule set `E,F,W,I,B,UP,N,RUF`, sensible per-file
  relaxations for `tests/`.
- Pytest markers `integration` and `gpu` keep paid/GPU tests opt-in.

## Tasks

| # | Task | Where | What | Effort |
|---|---|---|---|---|
| 3.1 | Add a coverage gate (F14) | `ci.yml`, `pyproject.toml` | `pytest-cov` is already a dep. Add `--cov=advtest_ext --cov-report=term-missing --cov-fail-under=<N>` to the CI pytest step. Set `N` to the current measured level (baseline it first — don't guess), then ratchet up over time. Ratchet down is never allowed. | S |
| 3.2 | Clear print/TODO debt (F12) | `advtest_ext/` | Replace the 5 `print()` calls in the package with `structlog` (already a dependency). Triage the 8 `TODO/FIXME/HACK/XXX` markers: fix, or convert to tracked issues with a link. Add a ruff rule or CI grep to fail on new `print(` in `advtest_ext/`. | S |
| 3.3 | Guard the test-suite runtime | CI | Measure wall-clock of the default `pytest` run. If it is slow, split fast unit tests (every PR) from slower suites (nightly or labelled), keeping the `integration`/`gpu` markers as the boundary. Fast feedback keeps the gate credible. | M |
| 3.4 | Dependency & supply-chain hygiene | `ci.yml`, deps | Add a scheduled job for `pip-audit` (or Dependabot for the Poetry lock + GitHub Actions). Pin the vendored dashboard JS (`cytoscape.min.js`, `dagre.min.js`) with a source + version comment, or document why they are vendored rather than fetched. | M |
| 3.5 | Verify the extras matrix installs | `ci.yml` | CI installs the default deps only. Add a job (or matrix leg) that runs `poetry install --extras all` and imports the optional-backed modules, so a broken `openai`/`gemini`/`ui_mode`/`postgres`/`gcs` path is caught before a user hits it. | M |
| 3.6 | Make the Makefile the single entry point | `Makefile` | Confirm `make test`, `ruff`, `mypy`, `install` match what CI runs, so "green locally" means "green in CI". Document any divergence. Add a `make check` that runs the full local gate in one command. | S |
| 3.7 | Security lint for the tool itself | `ci.yml` | Add `bandit` (or ruff's `S` rules) over `advtest_ext/`, tuned to ignore the intentional adversarial-payload modules. The harness that drives attacks should itself be free of accidental unsafe patterns (subprocess, eval, unsafe deserialization) outside the deliberate ones. | M |

## Sequencing

3.1 and 3.2 are quick, high-signal, and land first (they directly clear Phase 0 findings).
3.6 is a cheap consistency check to do alongside them. 3.3–3.5 and 3.7 are larger CI
additions; sequence them by how often each failure mode actually bites — coverage and extras
installs first, supply-chain and security lint next.

## Guardrails

- **Baseline before you gate.** For 3.1, measure real coverage first and set the floor there.
  A gate set above reality just breaks CI on day one and gets disabled.
- **Do not lint the payloads into blandness.** The adversarial modules (`attacks/payloads/`,
  `attacks/indirect_injection/`, the seed corpus) legitimately contain patterns a security
  linter flags. Scope 3.7's rules to exclude them explicitly, with a comment saying why.
- **Keep opt-in tests opt-in.** Never move `integration`/`gpu` tests into the default PR run;
  they need paid APIs or a GPU and would make the gate flaky and expensive.

## Definition of done

- [ ] CI fails on a coverage drop below the baselined floor.
- [ ] No `print()` in `advtest_ext/`; TODO markers are fixed or issue-linked; new prints are
      blocked by a rule.
- [ ] An `--extras all` install + import job runs in CI.
- [ ] Dependency scanning runs on a schedule; vendored JS is pinned and sourced.
- [ ] `make check` runs the exact local equivalent of the CI gate.
- [ ] A security linter runs over the harness, scoped around the intentional payloads.
