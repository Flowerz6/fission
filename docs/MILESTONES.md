# Milestones & Steps

> One ordered plan for a solo developer. This does not invent new scope — it sequences
> the work that the two existing planning tracks already describe, into concrete steps
> you can check off. When a step needs detail, it links to the doc that has it.
>
> **Two tracks run in parallel, and that is fine:**
> - **Product** — make the tool measurably better ([FISSION_ROADMAP.md](FISSION_ROADMAP.md),
>   [FISSION_PHASE_TASKS.md](FISSION_PHASE_TASKS.md)).
> - **Repo** — make the codebase clean and auditable ([production/README.md](production/README.md)).
>
> Working alone, do them interleaved: a repo-hygiene milestone, then a product milestone,
> so the codebase stays navigable while the capability grows.

## How to use this

- Milestones are ordered. Finish a milestone's **exit check** before moving on.
- Each milestone is sized **S** (≤ half a day) · **M** (1–2 days) · **L** (~a week).
- Check boxes as you go. This file is the single source of truth for "where am I?".
- When overwhelmed, do the **next unchecked box** and nothing else. That is the whole method.

---

## Milestone 0 — Get it running and documented  ·  S  ·  ← START HERE

*You cannot make progress on a project you cannot run. Close this first.*

- [ ] Install and run the dashboard once ([GETTING_STARTED.md](GETTING_STARTED.md)).
- [ ] Sign in one Google test account and run one `ui_mode` smoke config
      ([ACCOUNTS_AND_AUTH.md](ACCOUNTS_AND_AUTH.md)).
- [ ] Read [CODEBASE_MAP.md](CODEBASE_MAP.md) top to bottom once.
- [ ] Confirm the four operating docs are accurate to your machine; fix anything that drifted.

**Exit check:** you can go from a cold laptop to a live run and a dashboard showing it,
using only the docs — no memory required.

---

## Milestone 1 — Repo hygiene  ·  M  ·  (product-track Phase 0 prep)

*Get the working tree safe and the root clean so everything after is trustworthy.*
Full detail: [production/PHASE_1_REPO_HYGIENE.md](production/PHASE_1_REPO_HYGIENE.md).

- [ ] Commit the working tree in small, logical units (not one big snapshot).
- [ ] Confirm no secrets, `.db`, `.docx`, or `out/` artifacts are tracked (`git status`).
- [ ] Delete merged remote feature branches (F11 in the audit).
- [ ] Remove `.DS_Store` and stray root clutter.

**Exit check:** `git status` is clean; the root carries only source + config; nothing
sensitive is staged.

---

## Milestone 2 — Governance & doc accuracy  ·  M

*Make the repo explain and license itself, and kill doc drift.*
Full detail: [production/PHASE_2_GOVERNANCE_AND_DOCS.md](production/PHASE_2_GOVERNANCE_AND_DOCS.md).

- [ ] Add `LICENSE`.
- [ ] Add `SECURITY.md` + a written handling policy for the adversarial seed content.
- [ ] Update the root `README.md` package layout (it omits `agents/`, `calibration/`,
      `delivery/`, `guides/`). Point it at [docs/README.md](README.md) as the front door.
- [ ] Add `CONTRIBUTING.md` and a root `CLAUDE.md` (onboarding context for agent-assisted work).
- [ ] Confirm [docs/README.md](README.md) indexes every doc.

**Exit check:** a stranger can answer from the repo alone — what is this, who may use it,
where is the sensitive content, how do I run it.

---

## Milestone 3 — Measurement spine MVP  ·  L  ·  (product Phase 0, the prerequisite)

*Be able to measure. Produce a calibrated ASR ± CI for one run and start a gold set.*
This is mostly activating plumbing that already exists.
Full detail: [FISSION_PHASE_TASKS.md](FISSION_PHASE_TASKS.md) Phase 0 · design in
[FISSION_MEASUREMENT_SPINE.md](FISSION_MEASUREMENT_SPINE.md).

- [ ] `GoldLabel` table in `storage/models.py`; repo functions in `storage/repository.py`.
- [ ] Stratified sampler (`core/sampling.py` / new `measurement/sampler.py`).
- [ ] Activate HITL enqueue via a `review build --run-id` CLI command.
- [ ] Review UI: extend `dashboard/` `/hitl` to write `GoldLabel` (with a blind-review fraction).
- [ ] PPI metric (`core/ppi.py` already exists — wire it to a run).
- [ ] Trend view: per-run ASR ± CI on the dashboard overview.

**Exit check (G0):** one run reports `ASR = x% ± y% (95% CI)` and the gold set is growing.
You now also know your real labels/week rate, which sizes everything after.

---

## Milestone 4 — Prove the loop closes  ·  L  ·  (product Phase 1, the GO/NO-GO)

*The decisive experiment: does self-improvement work at all? Largely offline.*
Full detail: [FISSION_PHASE_TASKS.md](FISSION_PHASE_TASKS.md) Phase 1 ·
[FISSION_FLYWHEEL.md](FISSION_FLYWHEEL.md).

- [ ] Training stack behind a `train` poetry extra (torch/transformers/peft).
- [ ] L1 attack-success classifier tier (`judges/`), loadable from a checkpoint.
- [ ] SFT script that fine-tunes L1 on the gold set.
- [ ] Promotion gate: gold-set F1 (K1) + no drop in L1↔L0 agreement (K2, anti-reward-hack).
- [ ] Proof harness: K-round accumulation → retrain → plot F1 trend vs frozen baseline.

**Exit check (G1):** **GO** if L1 F1 rises over K rounds, L0 agreement stays flat, and it
beats the frozen baseline → fund the rest. **NO-GO** → fall back to shipping the measured
oracle + adaptive agent without the self-improving claim. Either outcome is a win you
bought cheaply.

---

## Milestone 5 — Quality gates & release  ·  M  ·  (repo Phases 3–4)

*Lock in quality and cut a real version.* Detail:
[production/PHASE_3_QUALITY_AND_CI.md](production/PHASE_3_QUALITY_AND_CI.md),
[production/PHASE_4_RELEASE_AND_OPS.md](production/PHASE_4_RELEASE_AND_OPS.md).

- [ ] Add a coverage gate to CI; clear the TODO/`print()` debt in shipped paths.
- [ ] Cut a tagged release that builds, deploys, and rolls back by runbook.

**Exit check:** CI enforces lint + types + coverage; a tag ties a running image to a revision.

---

## Milestone 6 — Expand oracle + Explorer  ·  XL  ·  (product Phase 2, only after GO)

*Widen self-improvement and automate variant generation.* Detail:
[FISSION_PHASE_TASKS.md](FISSION_PHASE_TASKS.md) Phase 2 · [FISSION_BUILD_MAP.md](FISSION_BUILD_MAP.md).

- [ ] L2 DPO on an open judge; full 5-check promotion gate.
- [ ] Intent Verifier gate; Explorer (quality-diversity variants) behind validity gates.
- [ ] Playbook model + Extractor (LLM compiles seeds → versioned Playbooks).

**Exit check:** L2 improves under the full gate; Explorer variants pass gates and grow
coverage without flooding noise.

---

## Milestone 7 — Scale, autonomy, governance  ·  XL  ·  (product Phase 3 + repo Phase 5)

*Run continuously, unattended, in-scope.* Detail:
[FISSION_PHASE_TASKS.md](FISSION_PHASE_TASKS.md) Phase 3,
[FISSION_DEPLOYMENT.md](FISSION_DEPLOYMENT.md),
[production/PHASE_5_AUDITABILITY_AND_COMPLIANCE.md](production/PHASE_5_AUDITABILITY_AND_COMPLIANCE.md).

- [ ] Target Runner service (decouple the browser from the orchestrator).
- [ ] Action-level Replayer agent (observe → decide → act → verify over the DOM).
- [ ] Browser farm: containerized headless Chrome pool + selector-drift detection.
- [ ] Governance: `Engagement` model + scope-enforcement gate + provenance + kill-switch.
- [ ] Prod data plane: Alembic migrations, Postgres default, object store, telemetry.

**Exit check:** continuous, autonomous runs that stay in engagement scope and keep pace
with Gemini drift.

---

## The one thing to internalise

The expensive, impressive work (the agent, the browser farm, the Explorer) is Milestones
6–7 and must not be built until **Milestone 4 says GO**. Milestones 0–3 are cheap and make
the whole project legible and measurable. Front-loading them is the difference between a
plan you can execute alone and a project that stays bigger than you.
