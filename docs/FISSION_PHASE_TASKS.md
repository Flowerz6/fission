# Fission — Phase Task Breakdown (concrete, against the current codebase)

> Engineering scope for [the roadmap](FISSION_ROADMAP.md). Tasks reference **real files**.
> Effort: **S** ≤1 day · **M** a few days · **L** ~1–2 weeks · **XL** multi-week.
> Verified against code this session: `storage/models.py`, `storage/repository.py`,
> `dashboard/app.py` (`/hitl`, `overview.py`, `drift.py`), `judges/`, `core/orchestrator.py`,
> `attacks/`, `cli.py`. **No ML-training deps exist yet** (no torch/transformers) — that's
> the main new capability.

---

## PHASE 0 — Spine MVP + start labeling
*Objective: produce a calibrated `ASR ± CI` for one Run and start the gold set. Mostly activating existing plumbing.*

| # | Task | Where | What | New deps | Effort |
|---|------|-------|------|----------|--------|
| 0.1 | `GoldLabel` table | `storage/models.py` (new class beside `HitlItem:233`) | schema from [Spine §2](FISSION_MEASUREMENT_SPINE.md); auto-created via existing `create_all` | — | S |
| 0.2 | Repo fns | `storage/repository.py` + export in `storage/__init__.py` | `insert_gold_label`, `gold_labels_for_run`, `count_gold_labels` (mirror `enqueue_hitl:222`) | — | S |
| 0.3 | Stratified sampler | new `measurement/sampler.py` | uncertainty = L0↔L2 disagreement + near-threshold + judge confidence; + uniform slice (reuse `judges/ensemble.py` spread logic) | — | M |
| 0.4 | Activate enqueue | `cli.py` new cmd `advtest review build --run-id` | run sampler over a finished Run → `enqueue_hitl` the picks (today only the dormant ensemble enqueues) | — | S–M |
| 0.5 | Review UI | `dashboard/app.py` `/hitl:717` + `templates/hitl.html` | show oracle verdict + evidence + snapshot + signals; `POST /hitl/{id}/resolve` → write `GoldLabel` + resolve item; **blind-review** fraction toggle | — | M |
| 0.6 | PPI metric | new `measurement/ppi.py` | `ppi_asr(full_oracle_labels, gold_labels) -> (estimate, ci_lo, ci_hi)` (prediction-powered proportion) | `scipy`/`numpy` | M |
| 0.7 | Trend view | extend `dashboard/overview.py` (exists) | per-Run ASR ±CI, oracle κ/α, FP rate, CI width | — | M |

**Exit (G0):** one Run reports `ASR = x% ± y% (95% CI)`; gold set growing. **You also learn your real labels/week here.**

---

## PHASE 1 — Prove the L1 loop closes  *(the GO/NO-GO)*
*Objective: answer "does self-improvement work at all?" Largely OFFLINE — train + evaluate, not live.*

| # | Task | Where | What | New deps | Effort |
|---|------|-------|------|----------|--------|
| 1.1 | Training stack | new `training/` pkg + poetry extra `train` | torch + transformers + peft + datasets; GPU (you have local-vLLM infra already) | **torch, transformers, peft, datasets** | M (setup) |
| 1.2 | L1 classifier tier | new `judges/attack_success_classifier.py` | wrap a pretrained classifier (DeBERTa-v3 head, or HarmBench-cls) in classifier mode; load from checkpoint; emit verdict | (1.1) | M |
| 1.3 | SFT script | `training/sft_l1.py` | fine-tune L1 on `(response_snapshot → expert_verdict)` from `GoldLabel`; emit versioned checkpoint | (1.1) | L |
| 1.4 | Promotion gate K1+K2 | `training/promotion_gate.py` | macro-F1 vs held-out gold (K1); **L1↔L0 agreement not dropped** (K2, the anti-reward-hack audit) | — | M |
| 1.5 | Proof harness | new `eval/proof_experiment.py` | K-round label-accumulation sim → retrain → plot F1 trend + L0 agreement + frozen-baseline ablation | matplotlib | M |

**Exit (G1):** GO if L1 F1 ↑ over K rounds, L0 agreement flat, beats baseline → build the rest. NO-GO → fall back to (b).

---

## PHASE 2 — Expand oracle + Explorer  *(only after GO)*
*Objective: widen self-improvement; automate variant generation.*

| # | Task | Where | What | New deps | Effort |
|---|------|-------|------|----------|--------|
| 2.1 | L2 DPO | `training/dpo_l2.py` | **needs an OPEN judge** (can't fine-tune Claude) — adopt Prometheus-2 / local judge; DPO on expert-override preference pairs | **trl**, open judge model + GPU | XL |
| 2.2 | Full promotion gate | `training/promotion_gate.py` | add K3 bias panel (CALM probes), K4 length-bias residual, K5 family-disjoint | — | M |
| 2.3 | Intent Verifier (G2) | new `judges/intent_verifier.py` | binary "same effect, same mechanism?" gate on Explorer variants | — | M |
| 2.4 | Explorer | new `attacks/explorer.py` (evolve `tap_crescendo` strategist) | `mutation_surface`, G1 validity / G2 (2.3) / G3 quality gates, MAP-Elites coverage report | — | L–XL |
| 2.5 | Playbook + Extractor | `storage/models.py` + new `playbook/extractor.py` | the report's canonical unit: versioned `Playbook` + LLM that compiles seed/corpus → Playbook (replaces hand-YAML) | — | L |

**Exit:** L2 improves under the full gate; Explorer variants pass gates and grow coverage without flooding noise.

---

## PHASE 3 — Scale + autonomy + governance  *(productionize)*
*Objective: run continuously, unattended, in-scope.*

| # | Task | Where | What | New deps | Effort |
|---|------|-------|------|----------|--------|
| 3.1 | Target Runner service | new `services/target_runner/` | decouple browser from orchestrator (the **attack driver→planner split**); remote target API; headless container pool | container infra | XL |
| 3.2 | Action-level Replayer | new `adapters/.../agent_replayer.py` | replace scripted `ui_mode/invoker._run_side_panel_flow` with an LLM observe→decide→act→verify loop over DOM/a11y | — | L–XL |
| 3.3 | Browser farm | infra | containerized headless Chrome pool, per-session isolation, **selector-drift detection** + alerts | k8s/containers | XL |
| 3.4 | Governance | `storage/models.py` + `core/` gate | `Engagement` model; **scope-enforcement gate** (target must be in-scope); provenance (oracle version per Finding); promotion **rollback + kill-switch** | — | M–L |
| 3.5 | Prod data plane | `storage/` | Alembic migrations; Postgres default; object store (S3/GCS) for screenshots/replay bundles; OTel/Prometheus | alembic, boto3/gcs, otel | L |

**Exit:** continuous, autonomous Runs that stay in-scope and keep pace with Gemini drift.

---

## Cross-cutting (slot where needed, not a phase)
- **Migrations/Postgres:** SQLite is fine through Phase 0–1; move to Postgres + Alembic when multi-writer/continuous (Phase 3, task 3.5).
- **Governance scope-gate (3.4):** pull *forward* the moment you test a **client's** systems rather than your own.
- **Integrity checks:** inter-rater κ + blind-review anchoring run continuously from Phase 0.

## Where the real difficulty (and risk) concentrates
1. **The ML training stack (1.1/1.3/2.1)** — entirely new muscle (SFT then DPO). Phase 1's SFT on a small classifier is *tractable*; Phase 2's **L2 DPO needs an open, fine-tunable judge** (Claude can't be tuned) — that's the heaviest model decision.
2. **The browser farm + Replayer agent (3.1–3.3)** — the biggest infra lift and the moat's operational backbone.
3. **Everything else is small or activation** — Phase 0 is mostly wiring existing plumbing (`GoldLabel` + activate `enqueue_hitl` + extend `/hitl` + PPI). That's the cheap, decisive start.
