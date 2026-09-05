# Phase 5 — Auditability & Compliance

> The phase that earns the word "auditable". For a tool that runs live attacks against a
> third party's product, being clean is not enough — every result must be traceable to the
> code and data that produced it, every action must be provably in-scope, and the sensitive
> content must be governed, not just tidy.

**Objective:** a reviewer can trace any finding back to its inputs, confirm any run stayed
within an authorized engagement, and see a governed home for the adversarial content.

**Exit gate:** provenance, scope enforcement, and data-retention are enforced in code and
documented, not merely aspirational.

## Why this is separate from hygiene

Phases 1–4 make the repo tidy and releasable. Auditability is a different property: it is
about *trust in results and actions over time*. A red-team platform that cannot prove what
it did, against whom, with which oracle version, is a liability regardless of how clean its
tree is. This phase is where the product roadmap's governance items (roadmap tasks 3.4/3.5)
meet the repo's compliance needs.

## Tasks

| # | Task | Where | What | Effort |
|---|---|---|---|---|
| 5.1 | Sensitive-content handling policy (F4) | `SECURITY.md`, `seed_data/`, `advtest_ext/attacks/payloads/` | Write the policy the earlier phases point to: what content is offensive (payloads, guardrail-bypass seeds, exfil guides), where the canonical copy lives, who may access it, and the rule for adding more. Classify each seed source (e.g. real-engagement transcripts vs public benchmark) by sensitivity. | M |
| 5.2 | Result provenance | `advtest_ext/storage/`, dashboard | Every Finding should record the versions that produced it: oracle/judge version, attack config, code revision (git SHA), and dataset version. The gold-set schema already freezes `oracle_verdict`/`oracle_score` at sample time — extend that provenance to Findings so any number on the dashboard is reproducible. | L |
| 5.3 | Engagement scope enforcement | `advtest_ext/core/`, `storage/models.py` | Add the roadmap's `Engagement` model and a scope gate: a run against a target not in an authorized, in-window engagement is refused. This is the single most important compliance control for a tool that touches a client's live systems. Pull it forward the moment a client's systems are tested. | L |
| 5.4 | Immutable audit log | `advtest_ext/storage/` | Record who ran what, against which target, under which engagement, when. Append-only, tamper-evident. Distinct from application logs — this is the record an auditor reads. | M |
| 5.5 | Data-retention & deletion | ops, `storage/` | Define retention windows for run artifacts, screenshots, transcripts, and expert labels, and a deletion path (including a client's right to have their data purged). Ties to Phase 4.7's backup policy — retention and backup must agree. | M |
| 5.6 | Reproducibility contract | `docs/`, `configs/` | Document how to reproduce a published result: pinned deps (`poetry.lock`), the config, the dataset version, the code SHA, and the seed for any sampling (`review_seed` already exists). A reviewer should be able to re-run and get the same ASR ±CI. | M |
| 5.7 | Oracle-change provenance | `advtest_ext/judges/`, storage | The flywheel changes the oracle between runs. Record which oracle version graded each run so cross-run comparisons are honest (the spine already freezes per-sample predictions; make the oracle *version* first-class and queryable). | M |
| 5.8 | Kill-switch & rollback | `core/`, ops | A documented, tested way to halt all live runs immediately, and to roll back a promoted oracle. Required before autonomous/continuous operation (roadmap Phase 3). | M |

## Sequencing

5.1 is a document and should land early — it is the policy the whole repo references. 5.3
(scope enforcement) is the highest-stakes control; build it before any client engagement,
even if internal-only testing does not strictly need it yet. 5.2/5.7 (provenance) make
results trustworthy and pair naturally. 5.4/5.5/5.8 harden the platform for continuous,
audited operation.

## The compliance backbone (what a reviewer will check)

| Question a reviewer asks | Answered by |
|---|---|
| "Who may use this, and how?" | LICENSE (2.1) + SECURITY.md responsible-use (2.2) |
| "Where is the dangerous content and who governs it?" | Handling policy (5.1) |
| "Was this run authorized and in-scope?" | Engagement model + scope gate (5.3) + audit log (5.4) |
| "Can I trust this ASR number?" | PPI ±CI (built) + result provenance (5.2) + oracle-version provenance (5.7) |
| "Can I reproduce it?" | Reproducibility contract (5.6) |
| "How long is client data kept, and can it be deleted?" | Retention & deletion policy (5.5) |
| "Can you stop and roll back?" | Kill-switch & rollback (5.8) |

## Guardrails

- **Scope enforcement is not optional once a client is involved.** Treat 5.3 as a hard gate,
  not a feature. A run outside an authorized engagement should be impossible, not just
  discouraged.
- **Provenance is append-only.** Audit records (5.4) and frozen oracle predictions must never
  be editable after the fact, or the audit trail is worthless.
- **The measurement spine already gives you a head start.** Frozen `oracle_verdict`,
  `review_seed`, and immutable `GoldLabel`s are provenance primitives — 5.2/5.6/5.7 extend
  them rather than starting from scratch.

## Definition of done

- [ ] A written, referenced sensitive-content handling policy exists and classifies each
      seed source by sensitivity.
- [ ] Every Finding records oracle version, config, code SHA, and dataset version.
- [ ] An `Engagement` model + scope gate refuses out-of-scope runs.
- [ ] An append-only audit log records who/what/when/against-whom.
- [ ] Retention windows and a deletion path are documented and implemented.
- [ ] A reproducibility contract lets a reviewer re-derive a published result.
- [ ] A tested kill-switch halts live runs and rolls back a promoted oracle.
