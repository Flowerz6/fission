# Fission v2 — Human-in-the-Loop Rebuild Plan (corrected)

> Supersedes the earlier draft. Module paths below are the ones verified against the live tree; where this doc and your own path check disagree, your path check wins.

> **OPEN DECISION (resolve before shelving Playwright):** does this pivot run *alongside* the Google IPI pilot or *supersede* it? Shelving the live automation may slow the 300-automated-success deliverable. **Keep the Playwright path reversible until this is settled.**

---

## 1. The pivot, in one paragraph

Fission changes from an **autonomous live-run attacker** into a **human-in-the-loop variant engine with structured knowledge capture**. No live automated attacks for now. The loop:

**human uploads a seed → fission generates variants (Venice model) → human manually tests them → human confirms the outcome and records *why* → that signal updates generation.**

The human becomes the oracle, replacing the automated judge on the grading path — which directly targets this project's documented pain (the oracle over-calls success; the judge gold set was inconclusive). We build in layers: Layer 0 is capture + generation; the update loop is Layer 0.5; automation/training return as later layers on top of a clean corpus.

## 2. Guiding principles

1. **Human is the oracle.** No automated success scoring on the grading path in Layer 0.
2. **Capture the delta.** The before→after edit that makes a variant land is the highest-value signal. Capture it automatically.
3. **Shared vocabulary.** `axes_applied` (generator intent) and `why_tags` (human verdict) draw from the same mutation-axes taxonomy, so the intent-vs-reality gap is measurable later.
4. **Evidence-backed confirmation.** "It worked" requires the target's actual response; designed to hold for untrusted users later.
5. **Shelve, don't delete.** Everything parked must revive cleanly.
6. **Minimise real-data egress.** The generation model is a third party; real data must not reach it. See §5 and §6.
7. **Trust scope for Layer 0 = internal seeders only.** External users are a later layer; note where the trust assumption is load-bearing.

---

## 3. Model routing (settled)

**The Venice uncensored model is the generation engine.** Default: `abliteration-abliterated-model-large-v2`.

- **Why not Claude.** Claude refuses the mutation task itself. In IPI the harmful part is not a separable content field — mutating a seed to be more evasive/coercive *is* authoring a better attack, which Claude declines. So "start on Claude and A/B down" (review rec #1) does not apply: there is nothing meaningful to route to Claude for generation. This is the real justification, not model capability.
- **Claude's role** is limited to non-harmful work only: tagging, coverage analysis, summaries that don't require harmful content — and, optionally, any mutation axis that is *purely structural* (reorder, swap delivery surface, change host vector) with no increase in harm. Do not engineer a Claude/Venice split for offensive axes.
- **Privacy consequence — load-bearing.** `abliteration-abliterated-model-large-v2` is a proprietary-API model (routes to Abliteration.ai), *not* covered by Venice's self-hosted no-logging. Since all generation flows through it, the redaction gate (§6) is mandatory, not optional.
- **Do NOT retire the self-hosted RunPod `Qwen2.5-32B-abliterated-v2` path yet.** Keep it as the **private fallback** for any flow that must not egress. The v2-vs-Qwen choice is decided later by an edit-rate A/B on the generation task (§9), not assumed now.
- **Text-only.** The Venice model rejects image/video input (HTTP 400). Image-based injection vectors need a separate vision-capable path.

The Venice swap touches multiple role entries in `configs/dev.yaml` (currently hardwired to the RunPod endpoint + Qwen across several roles, ~lines 9–28), not a single line.

---

## 4. Shelve (behind `ADVTEST_MODE`)

Introduce an env var **`ADVTEST_MODE`** (values `assist` default / `live`), consistent with `ADVTEST_DB_URL` / `ADVTEST_WORKSPACE_INTEGRATION` — **not** a bare yaml key. Enforcement is two-part, because a config value won't stop a standalone script:
- (a) the assist flow **never imports** the live drivers; and
- (b) each standalone live script (the `fission_e2e.py`-style entry points with the "NOT run in-session" headers) **checks the mode at startup and refuses unless `live`.**

Write `docs/SHELVED.md` recording what's parked and how to revive it.

**Shelve:** the Playwright live driver; `advtest_ext/judges/` *on the grading path* (but keep it running in shadow — §7); `judges/signals/exfil_sink.py`; the auto-exec loop in `strategy/loop.py`; the pilot M/N-over-live-episodes validation. For the crescendo logic (`attacks/tap_crescendo.py`, `attacks/pyrit_wrappers/crescendo.py`): separate the mutation reasoning (keep) from auto-execution (shelve).

**Explicit keep/shelve decision for the measurement subsystems adjacent to judges:**
- **Keep the `scoring/` compute path** — `scoring/failure.py:classify_failure` + `objectives.py` produce the verdict the shadow judge needs. Shelve only `scoring/`'s *promotion to grading authority*, not the compute.
- **Keep `calibration/`** (feeds shadow-judge disagreement analysis).
- Shelve `drift/` / `observe/` hooks that assume live runs.

---

## 5. Keep and refactor into the core

- **Mutation engine** — `advtest_ext/mutate/` + the reasoning brain in `advtest_ext/strategy/` (`orchestrator.py`, `attacker.py`, `frontier.py`). Refactor from "called inside the live loop" to a standalone on-demand variant generator driven by the Venice model.
- **Mutation axes taxonomy** (`docs/MUTATION_AXES_TAXONOMY.md`) — single source of truth for both generation axes and confirmation `why_tags`.
- **Lineage / diversity tracking** — keep; now steers which thin cells to point testers at.
- **Storage** — `storage/` (SQLAlchemy) + existing `strategy/models.py` + the Guide config-object model. This is the base for the schema migration (§6).
- **Export** — `delivery/` + `export_pilot_dataset.py` (already ~80% of a training record); retarget to emit the human-graded corpus.
- **Dashboard** (FastAPI/HTMX) — home for the test/confirm flow (§7).

---

## 6. Schema — a MIGRATION onto existing models

**Source of truth: `docs/FISSION_V2_SCHEMA_MIGRATION.md`** (authored against the real models — field-by-field mapping, new/extended tables, vocabulary bindings). The logical shapes in the earlier draft are superseded by that mapping. Settled decisions it must encode:

- **Seed ≈ `Guide`** (~90% reuse): `id`/`provenance`/`canary`/`carrier_payload` map to existing `Guide` fields; `attack_class` → `Guide.threat_class`. `ext` typed block is unnecessary — `Guide` is already specificity-graded.
- **`intended_outcome` = `PILOT_HARM_TYPES` verbatim** (`delivery.harm_taxonomy`). Do **not** introduce a parallel enum — the exporter trusts that label; renamed spellings break it silently.
- **Verdict enum = `{success, partial, refused, ineffective, error}`** (resolved). The repo-wide live `Verdict` (`repository.py:_VERDICTS`, `GoldLabel.expert_verdict`, `Trace.verdict`) has no slot for "ran cleanly, no refusal, no harm"; that concept already exists in `observe/` (`ObservationOutcome` / `FailureClass.variance`). The migration doc adds **`ineffective`** as a first-class human-outcome value with a lossless mapping across `Verdict` / `ObservationOutcome` / `FailureClass`, without widening the live `Verdict` (so `GoldLabel`/PPI are untouched). Note: `observe/` currently collapses `observed_no_effect → valid_refusal` — the human oracle un-collapses it, which is the point.
- **`target` stays on the closed `_App` enum** (`GuideTarget.app`) — the runner/registry is keyed on it. Drop the provider-agnostic string (aspirational); extend `_App` when a real new provider is added.
- **Variant = extend `GuideVariant`**, reusing lineage (`guide_id`), content-hash dedup, and provenance; **add** `payload` / `axes_applied[]` / `generator` / `generation` columns. Do **not** merge status vocabularies: `GuideVariant` today means "proven winning variant" (`candidate→verified_by_reproduction→canonical`); the assist lifecycle is `untested→testing→confirmed→failed→abandoned`. Add an **`origin`/`lifecycle` discriminator** (harvested vs generated) and keep the two status vocabularies separate under it — don't cram both into one enum.
  - **Variant is a full seed-shaped object, not a bare payload** (`docs/FISSION_CAPTURE_SPEC.md §4.5`): any seed field may differ from the parent, and variants are **recursive** (a confirmed variant can seed more). Axes are **two-tier** — content (`_MutationDimension`) and structural (`surface`/`carrier_type`/`flow`/`topology`, a new enum). A variant renders through the same seeder writeup as a seed, with parent-diffs highlighted.
- **Confirmation = a NEW sibling table keyed on `variant_id`** — borrow `GoldLabel`'s shape (`frozen`, oracle+expert verdicts, `reviewer_id`, `blind`, `review_seconds`) but do **not** overload `GoldLabel`: it's load-bearing for the unbiased ASR/PPI estimate and assist-mode rows would pollute the gold set. Reuse `GoldLabel.blind` to hide the shadow verdict during human review (anchoring guard).
- **`judge_verdict_shadow` = `GoldLabel.oracle_verdict`** pattern (`+oracle_score`, `oracle_layers_fired`) — reuse exactly.
- **`axes_applied` / `why_tags` bind to the code SSOT** `advtest_ext.guides.models._MutationDimension` (persuasion_principle, pretext_scenario, persona, escalation_trajectory, format_mimicry, carrier_placement, wording + legacy) — not just the doc. `taxonomy_version` records revised-vs-legacy so old tags don't drift.
- **`delivery_vector`** is structural (`Guide.setup_steps`/`GuideStep.operation` + `carrier_placement`), not a flat field — don't add one.
- **`taxonomy_version`** is genuinely new — add it.
- **`SeedReliability`: re-source, don't shelve** — recompute `n_success`/`n_attack_failed` + discard-at-N from confirmations instead of live sessions, keeping seed-retirement working without live runs.
- **Evidence checklist driven by `Guide.success.signals`** (`canary_appears`/`exfil_sink`/`doc_diff` + `Guide.canary`) — the confirm UI renders the seed's own declared success signals; no parallel evidence vocabulary.

### Redaction gate (first-class component — the central safeguard)
Because all generation (and the exemplars that feed it) goes to the third-party Venice model:
- **Evidence-at-ingest:** redact `evidence.target_response` on save — strip live PII, real-account canaries, captured secrets — before it is stored in a form any generator can later read.
- **Exemplar-selection:** the §8 few-shot selector must pull only redacted content into generator prompts. Never let raw evidence reach a Venice call.
- **Seed-at-upload:** scrub real captured data (e.g. Burp-captured cookies/keys) before a seed is accepted.
This satisfies `data-handling.md` (no confidential data to third-party services without an approved agreement) and underwrites the untrusted-users trust story.


**Caveat — structured secrets vs. unstructured victim data (record before Layer 0.5).** The shipped
regex redactor (`advtest_ext/redaction/`) is strong for **structured** secrets — API keys, cookies,
cards, SSNs, emails, IPs — so **channel 1 (seed-at-upload) is well covered**: a seed's accidental leak
is almost always a structured secret pasted into the carrier. It does **not** reliably catch
**unstructured** victim data — real document titles, owners/teams, email subjects, free-text PII —
which is exactly what an exfil `target_response` (channel 2) contains; a regex cannot enumerate "a real
doc title." Therefore, **before Layer 0.5 reuses redacted evidence as Venice few-shot exemplars**, do
one of: **(a)** keep `target_response` out of Venice-bound prompts entirely — exemplars are
carrier-only, and `target_response` serves the human oracle and at-rest training, never egress; or
**(b)** add an NER/classifier layer on the evidence channel before any exemplar from it can egress.
**Phase 1 is unaffected** — it egresses the carrier genome + mutation critiques (`why_tags` /
`what_changed_and_why`), never `target_response`.

### Schema notes (fold in)
- **State machine:** define the mapping from `confirmation.outcome` → `variant.status`; decide who maps `partial`. Allow **multiple confirmations per variant** (testers can disagree) with an explicit aggregation rule.
- **diff:** unified-diff, computed server-side from initial-vs-saved; store **both raw payloads** (`raw_before`/`raw_after`), not just the diff, so it's recomputable.
- **canary:** `success` requires `canary_observed`, but `canary` is nullable — define the fallback oracle (reviewer-confirmed response) for seeds with no canary.
- **Immutability:** confirmations are **append-only** (trust story for untrusted users).
- **Taxonomy versioning:** stamp every record with `taxonomy_version` so `why_tags` don't drift in meaning as tags evolve.

---

## 7. Human test & confirm flow

In the dashboard, per variant the tester records: **outcome** ∈ `{success, partial, refused, ineffective, error}`; **evidence** (paste the target's actual response, flag canary; required for success — this is the trust guard); **edit** (payload is editable; on save the UI auto-captures the unified diff and both raw payloads); **why-tags** (multi-select from the axes taxonomy) + one optional free-text box; **what changed & why** (short reasoning for a successful edit). Keep the form fast — friction is the main threat to data quality; tags + auto-diff carry the load, free text is the escape hatch.

**Shadow judge:** keep the decomposed judge running silently on each variant and record its verdict in `judge_verdict_shadow` next to the human's. The human verdict is authoritative; the judge is off the grading path but its disagreement set is free, high-value calibration data (and keeps the oracle warm for Layer 2 revival).

**Seeding / review page:** the seeder fills a **fields-only** form (`docs/FISSION_DASHBOARD_SPEC.md`); on Save, the fields render into a natural-language **writeup** styled like the team's PDF submissions (`docs/FISSION_WRITEUP_SPEC.md`, ref `fission_seed_doc.html`) for the human to read, edit as prose, and confirm. Reproduction **sequence is derived** (no drag-to-reorder canvas); the flow is a **reusable object picked by default**; the payload and recorded flow values are `locked` (verbatim), prose is editable, uncaptured fields show as amber validation markers. Variants reuse the same writeup.

---

## 8. Update loop (Layer 0.5 — build AFTER capture is proven)

No weight training. Confirmed, **redacted** records feed back three ways: few-shot exemplars (redacted only) injected into the Venice generator prompt, scoped by provider + attack_class; an axis-weight table biasing high-yield axes per provider/surface; coverage steering surfacing thin cells. A pile of clean confirmations with no loop is already valuable; a loop on thin data isn't — so this ships after §6/§7/store/export.

---

## 9. Layering roadmap

- **Layer 0 (now):** schema migration, Venice generation service, test/confirm capture + redaction gate + shadow judge, store, export. Internal users.
- **Layer 0.5:** the update loop (§8).
- **Layer 1:** measure generator-intent vs human-reality gap; auto-tune exemplar selection and axis weights; richer coverage planning; mine the judge-vs-human disagreement set via `calibration/`.
- **Layer 2:** revive Playwright/judges/exfil_sink behind `live`; and/or fine-tune on the human-graded corpus (the redacted rationale data is the training set); harden evidence/trust model if external users arrive. Also the natural home for the v2-vs-Qwen capability decision at scale.

---

## 10. Ordered task list

1. Confirm the path map from §3–§5 against the tree; resolve the pilot-doc reference (root `Fission_Indirect_Prompt_Injection_Pilot.pdf/.docx`). Settle the §4 keep/shelve decision for `scoring/`, `calibration/`, `drift/`, `observe/`.
2. Add `ADVTEST_MODE` (env, `assist` default); implement two-part enforcement (assist never imports live drivers; live scripts refuse at startup). Write `docs/SHELVED.md`.
3. Gate the Playwright driver, judges-on-grading-path, `exfil_sink`, and `strategy/loop.py` auto-exec behind `live`. Separate crescendo mutation reasoning from execution.
4. **Migrate** the schema per `docs/FISSION_V2_SCHEMA_MIGRATION.md` (§6): extend `Guide`/`GuideVariant`, add the `Confirmation` sibling table keyed on `variant_id`, bind vocabularies (`PILOT_HARM_TYPES`, `{success,partial,refused,error}`, `_MutationDimension`, `_App`), add the `origin` discriminator, `taxonomy_version`, `raw_before/after`, append-only confirmations, and re-source `SeedReliability` from confirmations.
5. Build the **redaction gate** on seed-upload, evidence-at-ingest, and exemplar-selection. Treat it as a first-class, tested component.
6. Refactor `mutate/` + `strategy/` into a standalone Venice-driven variant generator; tag outputs with `axes_applied`. Keep the self-hosted Qwen path wired as private fallback.
7. Build the test/confirm flow in the dashboard (§7), including auto-diff capture and required evidence on success; wire the shadow judge. Build the **fields-only seeder form + generated writeup/review page** (`docs/FISSION_DASHBOARD_SPEC.md §2/§2b`, `docs/FISSION_WRITEUP_SPEC.md`): flow picked-by-default, per-step config values saved, `locked` payload/flow values, editable prose with overrides, amber markers for uncaptured fields. Variants render through the same writeup with parent-diffs.
8. Implement the confirmation store with full lineage (seed → variant → confirmation) and the state-machine/aggregation rules.
9. Retarget the export pipeline to emit the redacted, human-graded corpus.
10. Smoke-test end to end; confirm nothing in the `assist` path touches a live target, and no un-redacted evidence can reach a Venice call.
11. **Only then** build the Layer 0.5 update loop (§8).
