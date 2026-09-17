# Plan: structured seeds, seed lifecycle, reliable execution, models

Grounded in a full read of the current code (Sept 2026). Each workstream states the
goal, what exists today, the gap, the concrete tasks, and why. Sequenced by
dependency. Marks what is actionable now vs blocked on a live browser or a decision.

## Where things stand (the facts this plan builds on)

- **Guides are the runnable unit** (`guides/*.json`, Pydantic `Guide`), loaded through a
  single chokepoint `advtest_ext/guides/corpus.py`. The `Seed`/`Recipe` DB pipeline is a
  separate, largely parallel track. A guide's `exemplar_turns` (its seed chain) is the
  "initial seed"; the TAP tree's branches are the mutations.
- **Success is already execution-only.** Nothing static claims success; it is computed
  from run traces, and "verified" already requires reproduction across fresh sessions
  (`reverify.py` + `GuideVariant`). Requirement 4 is satisfied in principle; the work is
  to make it explicit and enforced.
- **Bot-detection is not distinguished from other failures.** Every DOM/automation
  failure, including Google's "unusual activity" interstitial, collapses into one
  `SidePanelUnavailable` → verdict `error`. Only a human eyeballing the saved
  `failures/<trace_id>.png` can tell bot-detection from selector drift today.
- **There is no persistent per-seed reliability ledger.** The dashboard seed ledger
  aggregates traces on the fly by `behavior_id` (= guide id); nothing durable records
  attempts / successes / bot-detections / attack-fails per seed, and there is no
  discard/retire state.
- **Single-app multi-turn is the reliability template** (idempotent render, warm
  session, per-node reset, self-cleanup). Multi-app adds staging fragility: account
  juggling, API-vs-UI staging mismatch, a flat 5s settle instead of adaptive indexing
  wait, `setup_verification` populated on zero guides, and no teardown of planted
  artifacts. The primitives to fix all of this already exist; they are just unwired.
- **All models are local qwen 7b** in `configs/local.yaml` / `dev.yaml`. The three roles
  are the strategist/attacker (`CLAUDE_ATTACKER`), the judge (`CLAUDE_JUDGE`/`JUDGE_MODEL`),
  and the on-mechanism checker (`CHECKER_MODEL`).

---

## Workstream 1 — Structured YAML seeds (foundation) — DONE

**Status (2026-09-15).** Complete and verified. Guide loading now dispatches on suffix
(`corpus.load_guide`/`load_guides`, YAML-wins dedup) and the same widening is in the playbook
picker (`dashboard/live_tests.py`) and seed ingest (`scripts/ingest_seeds.py`). The converter
`scripts/seed_capture_to_yaml.py` is round-trip checked (content-hash equal on reload) and has
migrated all seven Bikash-authored guides to `guides/<id>.yaml`; the redundant JSON was removed
so YAML is the single source. The one guide writer (`scripts/generate_cells.py`) is now
format-aware via `corpus.resolve_guide_path`, so generated cells always land in the file that
actually loads. Integrity is enforced by `tests/test_guide_corpus_formats.py` (every guide
loads, no diverging dual-format twin, every guide round-trips through YAML losslessly). The
authoring doc gained §9.1 mapping the "FISSION SEED CAPTURE" header onto Guide YAML fields and
the execution-only-success note. Remaining corpus guides (non-Bikash) stay JSON and can
migrate with the same converter when convenient; both formats coexist safely.

**Update (Drive import + completeness).** Bikash's four newer Drive submissions were pulled from
the shared Drive folder (downloaded locally) and authored as complete guides:
`gmail_ai_overview_owner_note_guidelines_star`, `slides_narrative_scaffolding_crescendo_persona_forgery`,
and `slides_serialized_spec_crescendo_tool_registry_disclosure` (all runnable), plus
`drive_crossapp_email_injection_tool_registry_disclosure` (track-only: `google_drive` was added to
the app enum but has no adapter yet). To answer "does the YAML have everything the system needs",
a completeness checker (`advtest_ext/guides/completeness.py` + `scripts/check_guides.py`) encodes
the operational bar — deterministic signal, seed chain, resolvable setup ops, provenance — as
errors vs warnings; the corpus is now held to **zero errors** by a test. The checker also found and
fixed a pre-existing broken guide (`docs_language_override`: no signal, no seed chain).

**Carrier provisioning rule.** A seed that needs a planted carrier but does not provision it cannot be
used — the run isn't self-contained or reproducible. The checker enforces this: `carrier_not_provisioned`
(ERROR) when a guide declares a carrier (`carrier_payload`/`_source`) but has no plant `setup_step`
(`send_email` / `open_document` / `set_document_body` / `open_deck` / `set_speaker_notes`); and
`carrier_provisioning_unverified` (WARNING) for an indirect guide with no staged plant that likely relies
on an out-of-band manual plant. The Drive seed hit the ERROR (its carrier email was never archived, so it
can't be staged) and was **moved out of the active `guides/` corpus** into its `seed_data/Seeds/` capture
folder — converted to YAML and tracked, but not enrolled as usable until the carrier is recovered + staged
and a Drive adapter exists. Live validation for the cross-app carriers is written up in
`docs/WS3_LIVE_VALIDATION_RUNBOOK.md`.

**Goal.** The initial seed is authored and stored in YAML: a structured, human-diffable,
trackable format. (Requirement 3.)

**Today.** Guides/seeds are JSON. YAML is unused for them, but `pyyaml` is a dependency
and the exact `yaml.safe_load` + `model_validate` pattern is already used for run configs
(`cli.py:load_run_config`). Guide loading has one chokepoint (`corpus.py`).

**Tasks.**
1. Add YAML support to guide loading: in `corpus.py`, branch `load_guide` on suffix
   (`.yaml`/`.yml` → `Guide.model_validate(yaml.safe_load(text))`, else JSON) and widen
   `load_guides` to glob yaml too. Widen the same globs in `dashboard/live_tests.py`
   (playbook picker) and `scripts/ingest_seeds.py` (seed corpus).
2. Define the YAML seed authoring shape = the `Guide` schema (no new model needed). Add a
   short authoring doc mapping Bikash's "FISSION SEED CAPTURE" header fields onto Guide
   fields.
3. Converter `scripts/seed_capture_to_yaml.py`: read a "FISSION SEED CAPTURE" `.md` (and
   an existing formatted JSON guide) and emit a `guides/<id>.yaml`. Round-trip test:
   yaml-load == json-load for the same guide (content hash equal, since `content_hash`
   uses `model_dump(mode="json")` and is format-agnostic).

**Why.** Everything downstream keys off the guide; making the author-time artifact YAML
is a loader-layer change, low risk, and unblocks tracking and review. Actionable now.

---

## Workstream 2 — Seed lifecycle: execution-only success, reliability ledger, discard rule — DONE (code); live heuristic pending

**Status (2026-09-15).** Built and tested (28 new tests). Delivered:
- **Failure taxonomy** (`adapters/_ui_mode_common/errors.py`): `AntiAutomationDetected`
  subclass of `SidePanelUnavailable`, a conservative `looks_like_anti_automation` DOM/URL
  marker scan, and `classify_error_kind`. Each exception carries `error_kind`, so the
  orchestrator labels the trace (`_error_trace`) with `bot_detected | selector_drift |
  attack_error` via `getattr` — no core→adapters import.
- **Bot-wall detection wired** into the Sheets invoker (the mature single-app path) via
  `raise_side_panel_failure(page, exc, msg)`, which sniffs the live page and raises the
  right subclass. Rolling this to the other six invokers is one line each and is the piece
  that still needs a real interstitial to validate.
- **Durable ledger** (`storage/models.py:SeedReliability`, self-creating table) +
  `core/reliability.py`: `summarize_seed_reliability` / `recompute_seed_reliability`
  reduce each run to one session outcome from the seed's root (replay) traces and tally
  success / attack_failed / bot_detected / error. Recompute is idempotent and wired into
  run completion (`guides/run.py`).
- **Discard rule N=10** (decision 1): retire when `n_attack_failed >= 10` and
  `n_success == 0`; bot-detections and harness errors are inconclusive and never retire a
  seed. **Manual override** via `set_seed_status` (respected across recomputes).
- **Operator CLI** `scripts/seed_reliability.py`: recompute/report the whole corpus,
  backfill an existing DB, and pin a status by hand. Verified against the dev DB.
- **Execution-only success** is enforced end to end: capture `outcome:` is provenance only
  (documented in `SEED_AUTHORING_GUIDE.md` §9.1); validated status comes only from the
  ledger + reverification.

Remaining: roll `raise_side_panel_failure` to the other invokers and validate the marker
set against a real Google interstitial; optional dashboard surfacing of the ledger.

**Goal.** A human seed is validated only by execution, tracked across sessions, and
discarded after N failed sessions; a failure is classified bot-detected vs attack-failed,
with a manual override for ambiguous cases. (Requirements 1 and 4.)

**Today.** No durable per-seed ledger; `error` (harness/bot) is separated from `refused`
(attack fail) at the verdict level but bot-vs-drift is not; `error` traces are excluded
from ASR so a bot-heavy seed looks like nothing happened; no retire state. The
`cell_performance` "prune after N tries with no win" pattern is the closest analog.

**Tasks.**
1. **Failure sub-type.** Add `AntiAutomationDetected(SidePanelUnavailable)` and a DOM/URL
   heuristic where the failure artifact is already captured (invokers) that scans for
   "verify it's you" / unusual-activity / captcha markers. Record an `error_kind`
   (`bot_detected | selector_drift | attack_error`) on the trace (new column or in
   `raw_response`/metadata).
2. **Manual override.** Reuse the HITL pattern so an operator can mark an ambiguous
   `error` failure as bot-detected vs attack-failed after viewing the artifact.
3. **Persistent per-seed ledger.** New table keyed by seed/guide id with `n_sessions`,
   `n_success`, `n_bot_detected`, `n_attack_failed`, `last_run`, populated at run
   completion (adapt `cell_performance_for_runs` aggregation, but persisted and counting
   bot vs attack deliberately, not dropping `error`).
4. **Discard rule + status.** Add a `retired` status and the rule: retire when
   `n_attack_failed >= N` with `n_success == 0`; bot-detections do NOT count toward the
   attack-fail total (a bot-blocked seed is inconclusive, not failed). N configurable,
   default 10 (your TBD).
5. **Enforce execution-only success.** Bikash captures carry `outcome: success` in their
   header; on import that becomes provenance, never a validated status. A seed's
   validated/ verified state comes only from the ledger + reverification.

**Why.** This turns "does this seed still work" into a durable, queryable fact and stops
the corpus filling with dead seeds, while not punishing seeds that only fail because
Google blocked the bot. Ledger/rule are actionable now; the bot-DOM heuristic needs live
validation against a real interstitial.

---

## Workstream 3 — Reliable execution: single-app hardening + multi-app parity — IN PROGRESS

**Status (2026-09-15).** Started with the staging-reliability fixes that are verifiable offline:
- **Account preflight** (`stage.preflight_setup_accounts`, wired into `run_guide.py`): a
  multi-account guide now fails loudly up front if a non-recipient account's OAuth token is
  missing, naming the role, the expected path, and the env var — instead of silently planting
  as the default identity and "passing" while testing nothing. Tested.
- **Adaptive settle**: the run path already calls `verify_target_setup` (a readiness probe with
  backoff) after staging; the flat `setup_settle_s` sleep is now skipped when a guide configures
  an adaptive probe (`setup_verification.probe_prompt`), so the blind wait is a fallback, not a
  tax on every run.
- **Carrier-provisioning steps audited**: the two new Slides seeds now stage their deck via
  `slides.open_deck` with the planted body + a `setup_verification` probe (they were missing it);
  the Gmail seed already had a `send_email` plant + probe.

Deliberately **not** done blind (needs live accounts to validate): populating
`setup_verification` probe patterns on the nine older cross-app guides the completeness checker
flags — a wrong pattern fails a run closed incorrectly, which is worse than the current warning,
so these patterns must be tuned against a live interstitial/plant. Also remaining and
live-blocked: the single-app selector-drift pre-run health gate, seeder `teardown()` wiring, and
the Outlook empty `thread_id` fix.

**Goal.** Any single-app multi-turn exploit reliably executes, and multi-app is as
reliable as single-app. (Requirement 2 and next-step 3.)

**Today.** Single-app targets are mature. Multi-app fragility is all in the staging layer,
and the fixes already exist unwired.

**Tasks.**
1. **Single-app regression harness.** A repeatable check that each single-app guide's seed
   replays and reaches the side panel (selector-drift preflight before a run; the
   `selector_heal` tooling exists but is operator-run). Wire a pre-run health gate.
2. **Multi-app parity, wire the existing primitives into the run path:**
   - Populate `setup_verification` on every cross-app guide (currently zero) so a run
     aborts before TAP if the plant is not groundable.
   - Replace the flat `setup_settle_s` sleep with adaptive `wait_until_retrievable`
     (`corpus/indexing.py`) gating on the planted entity.
   - Add a preflight that each account's profile + token is present and logged in; make
     `build_setup_account_pool` fail loudly instead of silently dropping to one account.
   - Call seeder `teardown()`/trash for planted artifacts after a run so plants don't
     poison later runs.
   - Fix Outlook UI `send_email` empty `thread_id` (or restrict Outlook to non-threaded
     plants).

**Why.** Multi-app reliability is integration + guide authoring, not new architecture.
Actionable as code now; final proof needs live accounts.

---

## Workstream 4 — Models and mutation strategy

**Goal.** Three explicit roles: mutation-strategy model = local 70b; evaluator = LLM
judge; attacker = LLM attacker. Decide the mutation strategy set. (Models + next-step 4.)

**Today.** All roles are local qwen 7b. Typed mutation operators already exist on
`sheets_skill_leak` (persona, turn_order, escalation_speed, framing x2, wording) with a
novelty gate and a cell generator.

**Tasks.**
1. **Roles → models.** Point the strategist/attacker role at a local 70b, keep judge and
   checker on suitable models. One-line config change per role once a 70b is pulled on the
   Ollama server. (Blocked on: which 70b, and is it pulled.)
2. **Mutation strategy set (decision).** Recommend a canonical set: `persona`,
   `escalation_order`, `escalation_speed`, `framing`, `probe_target`, `wording` (already
   defined), plus `paraphrase` as a general reword axis. Standardize these across guides
   so every seed mutates along the same named axes.
3. Roll the typed operators out to the other guides (only `sheets_skill_leak` has them
   today), so mutation works corpus-wide.

**Why.** The strategist quality bounds the whole search; a 70b is the single biggest
quality lever. Standard axes make mutation uniform and the cell generator/performance gate
applicable to every guide.

---

## Next steps (your list) mapped to the workstreams

- **Convert Bikash's Drive seeds to YAML** → WS1 task 3. Local captures in
  `seed_data/POC v3/` convert now; Drive-only ones need Drive access or a local export.
- **Every original seed runs and reproduces in Fission** → WS3 task 1 + WS2 ledger. Needs
  live runs (browser + accounts + credit or local model).
- **Multi-app as reliable as single-app** → WS3 task 2.
- **Decide the mutation strategy** → WS4 task 2.

## Decisions (settled 2026-09-15)

1. **N sessions before discard = 10.** Retire when `n_attack_failed >= 10` with `n_success == 0`;
   bot-detections do not count toward the attack-fail total.
2. **Strategist 70b — deferred.** A 70b will be run on vast.ai later; keep the role config
   point-able (`configs/*.yaml`) but do not build this now. Roles stay on local qwen 7b.
3. **Mutation strategy set — deferred.** Not a focus now; `sheets_skill_leak`'s typed operators
   remain the reference. Revisit with WS4.
4. **Drive access = connect the Google Drive connector** and pull Bikash's captures directly,
   then convert with `scripts/seed_capture_to_yaml.py`.

## Suggested order (revised after decisions)

WS1 (done) → connect Drive + import remaining Bikash seeds → WS2 (lifecycle: ledger + discard
rule N=10 + bot-vs-attack classification) → WS3 wiring → live validation. WS4 deferred until the
vast.ai 70b is available.
