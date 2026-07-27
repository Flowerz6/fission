# Fission — Google Demo Build Brief

**Status:** T-36h build spec
**Audience for the demo:** Google Workspace / Gemini security team
**Audience for this file:** Claude Code (agentic build) + the human running the meeting
**Source material:** `Fission_demo_analysis.pdf` (competitive landscape & SWOT, July 2026) — this file supersedes its talk track. See §2.

---

## 0. How to use this file

This is a build spec, not a strategy essay. Sections 1–3 are context you must read before writing code, because they change what the UI is allowed to say. Sections 4–7 are the work items. Sections 8–11 are content the human needs, not code.

**Rules of engagement for the agent:**

- Do not start new subsystems. Everything below is rename, surface, compute, or export against objects that already exist (Playbook, Run, Finding, oracle gates, replay bundle).
- If a work item takes longer than its budget, stop and downgrade it to a roadmap slide. Shipping four solid things beats seven half-things.
- Never invent a number. Any figure not computed from real data must be rendered with an explicit `ILLUSTRATIVE` badge in the UI, or omitted. This is the entire brand. A single unlabelled fake number in front of this audience is fatal.
- Prefer static, pre-computed JSON over live queries for anything on the demo path. Reliability beats freshness for 40 minutes.

---

## 1. Situation

Fission is pre-revenue and architecture-stage. No Run data published. Team and funding an order of magnitude below the three named competitors (Gray Swan, Alice/ex-ActiveFence, Reinforce Labs).

What we actually have that nobody else does:

1. Execution against a **deployed third-party product surface** through its real UI — Playwright driving live Workspace, multiple accounts, in-product filters and grounding and retries all traversed.
2. **The Playbook** — one canonical, versioned, replayable unit for both corpus breadth and hand-found depth.
3. A **measured oracle** — four gates, agreement statistics against expert labels, and a success rate with a confidence interval.
4. **Portable replay bundles** — findings re-verifiable without our platform.

What we do not have: Run data, runtime/blocking, SOC 2 / ZDR / DPA, breadth beyond Workspace, and a proven authorisation story for driving live accounts. Do not paper over any of these.

---

## 2. THE REFRAME — read before writing any UI copy

The existing deck's core line is *"they test the AI you built, we test the AI you bought."*

**That line inverts in a Google room and must not be used.** Google built Gemini in Workspace. To them we are testing the AI they built — which is the exact category Gray Swan already occupies, with eleven frontier system cards behind it. Every close and objection answer in the source PDF is written for an enterprise Workspace *customer*. None of them land here.

**Replacement positioning:**

> Model-level and harness-level testing is a lower bound on deployed-system robustness. Fission measures the deployed system: the real Docs sidebar, the real Gmail, a real second account, with the product safeguard stack in the loop — and reports the result with an error bar.

Three consequences for the build:

- **Never render "the AI you bought"** or any variant, anywhere in the product, slides, or seed data.
- **Never frame Gemini as the weak model.** The source PDF §A.3 suggests leading with "their own numbers say Gemini is the most vulnerable at 8.5%." Delete this. In this room it is an insult delivered to its subject.
- **Do lead with the safeguard delta.** Published system-card figures show browser-surface injection success falling from roughly 31% pre-safeguard to roughly 0.5% with the full product safeguard stack, and coding-surface roughly 7% to roughly 2%. That delta comes from product-level safeguards, not the model. It is the argument that the deployed surface is where the measurement has to happen — and it is flattering, not hostile. Cite it as published vendor research, and get the figures re-verified against the current cards before the meeting (see §11).

**Seed/demo data naming:** use a neutral fictional tenancy (`acme-corp.example`), not a real customer name and not Google's own. Never seed data that implies we have already tested Google's production tenancy.

---

## 3. Priority stack

| ID | Work item | Budget | Priority |
|----|-----------|--------|----------|
| WI-1 | Surface Inventory (rename + reshape home) | 3h | **P0** |
| WI-2 | Judge Quality panel | 6h | **P0** |
| WI-3 | Finding detail + replay bundle download | 3h | **P0** |
| WI-4 | Demo harness + recorded fallback | 8h | **P0** |
| WI-5 | Gold-set labelling tooling | 2h | P1 |
| WI-6 | Roadmap-only screens (static mocks) | 2h | P1 |

**Explicitly CUT — do not build, show as roadmap slides only:**

- Drift/version timeline (great idea, wrong week)
- Regulatory framework mapping beyond a single MITRE ATLAS technique column
- Per-gate breakdown beyond one aggregate number per gate
- Any launch-readiness go/no-go traffic light — see §4.1 rationale
- Token usage / balance widgets — Alice ships these; they make an audit tool look metered. Do not copy.
- Any new nav item not listed above

---

## 4. Work items

### WI-1 — Surface Inventory (P0, 3h)

**Problem.** Competitors have trained the buyer to expect an inventory of things under test with per-item status. Alice's WonderBuild home is exactly this: applications listed with status, assessment count, last-assessment date. Our home page does not match that vocabulary, so we read as unfinished even though we produce the underlying objects.

**Fix.** Our rows cannot be "your applications" — the customer owns none of it. Our rows are **surfaces**.

Row schema:

```
surface_id            e.g. "docs-sidebar"
display_name          e.g. "Docs — Gemini sidebar"
account_role          "single" | "cross-account (A→B)"
target_version        e.g. "Gemini in Workspace, observed 2026-07-2X"
last_run              ISO date + Run ID, linked
open_findings         int, broken out by severity
exposure_status       "Exposed" | "Mitigated" | "Not yet tested"
playbooks_run         int / int total in catalogue
```

Minimum four rows for the demo: Docs sidebar, Gmail summarise, Sheets formula assist, and one cross-account chain row (Docs → Gmail, account A → account B). The cross-account row is the whole pitch; make it visually distinct.

**Do not** add a go/no-go readiness column. Alice can ship that because their customer controls the app and can decide to launch. We would be implying we can bless the release of a product the customer does not control. Use `exposure_status` per surface instead.

**Acceptance:** home route renders the table from a JSON fixture; every row links to a Run; the cross-account row is present and visibly distinguished; no fabricated counts (zeros are fine and honest).

---

### WI-2 — Judge Quality panel (P0, 6h) — the differentiator

**Why this exists.** Every vendor ships numbers of the form "we found N exploits" or "28.4% attack success." That number is the output of a classifier — their judge — and inherits the judge's error rate. Reinforce describes their judge as calibrated and human-anchored. Alice sells severity tiers. Gray Swan's monitor API returns a 0–1 violation score with no error bar. **None of them publish how often the judge is wrong.**

This panel is the specification sheet for our oracle. It is also the honest answer to "no Run data": calibration numbers do not require a customer. We can hand-label this weekend and publish real κ and real FP rate. Those are measured, not illustrative.

**Route:** top-level nav item, sibling of Findings and Playbooks. Not a tab inside a Run, not a methodology drawer. Buried, it reads as a disclaimer; top-level, it reads as a product claim.

**Panel contents, in this order:**

**(a) Gold set provenance card**
```
n_items, date_frozen, n_annotators, annotator_qualification (one line),
label_schema ("success" | "failure" | "ambiguous"),
composition breakdown by surface and by playbook family
```
Make the gold set a visible object. If it is an assumption, the whole panel is decoration.

**(b) Agreement block — must show the human ceiling**
```
Cohen's κ      oracle vs expert consensus
Krippendorff α oracle vs expert consensus (nominal)
Krippendorff α HUMAN–HUMAN  ← the ceiling
```
Render the human–human figure adjacent and equally prominent, with the caption: *"Expert annotators agree with each other at α = X. The oracle reaches Y against their consensus."* Without the ceiling on screen, a respectable 0.77 looks mediocre to anyone who does not do this for a living. With it, near-ceiling performance reads as strong.

**(c) Error rates**
```
false_positive_rate   of items oracle called success, share experts overturned
false_negative_rate   of items experts called success, share oracle missed
n for each, and Wilson 95% intervals on both
```
FP rate is the number the talk track weaponises against competitors. We can only ask it of them if ours is on a screen.

**(d) Per-gate reject rates** — one aggregate number per gate:
```
gate_name, candidates_in, rejected, pass_rate
```
Diagnostic, not decorative. A gate passing 100% is a rubber stamp and should be flagged in the UI with a warning state; a gate killing >90% is where the selection pressure actually lives. Showing this tells a technical audience the four layers are not theatre.

**(e) Human escalation share** — proportion of findings routed to human review. Doubles as the unit-economics number.

**(f) Version stamp** — oracle version, gold set version, date computed. Every number on this panel is versioned or it is worthless.

**Statistical implementation notes:**

- Cohen's κ: `sklearn.metrics.cohen_kappa_score`.
- Krippendorff α: `krippendorff` package, `level_of_measurement='nominal'`. Handles missing labels, which Cohen's κ does not — use α as the headline where annotators are uneven.
- FP/FN intervals: Wilson score interval, not normal approximation. At n≈100 the normal approximation is visibly wrong and someone in that room will know it.
- **PPI-corrected attack success rate.** Standard rectifier for a proportion. Given labelled set `L` (size n, human label `Y_i` and oracle prediction `f(X_i)`) and unlabelled set `U` (size N, predictions only):

  ```
  theta_hat = mean(f(X_j) for j in U) - mean(f(X_i) - Y_i for i in L)
  var       = var(f over U)/N + var(f - Y over L)/n
  ci95      = theta_hat +/- 1.96 * sqrt(var)
  ```

  Implement this directly; do not pull a heavy dependency for one estimator. Unit-test against a synthetic case where the oracle is perfect (correction term → 0, θ̂ → predicted mean).
- **If time runs short, use a bootstrap CI over the labelled set instead and label it as such.** A correctly-labelled bootstrap interval is far better than a rushed PPI implementation you cannot defend under questioning.
- With n ≈ 100–150 the interval will be wide. **Show it wide.** Do not tune n up until it looks good — the credibility comes from the numbers being allowed to look unflattering. Caption it: *"n = 120. Interval is wide because the gold set is small. It will narrow, and we will publish it either way."*

**Acceptance:** panel computes every figure from a real labelled CSV, not fixtures; human–human ceiling is rendered adjacent to the oracle figure; nothing on the panel is hardcoded; the whole panel recomputes if the CSV is replaced.

---

### WI-3 — Finding detail + replay bundle download (P0, 3h)

Reinforce positions "the customer keeps every artifact" as a differentiator. Ours should be a button the buyer can click in the room, not a positioning line.

**Finding detail view:**
- Severity tier (Critical/High/Medium/Low) — match the vocabulary the market already reads
- The actual trace: what was planted, where, which account, what fired, in which app
- Per-gate oracle verdicts for this finding, each with the gate's own pass rate from WI-2 shown inline
- Playbook ID + version, Run ID, target version observed
- **Near-miss state.** Findings that fired the oracle but sat below the promotion gate must be visible and labelled `Below promotion gate`, not hidden. Borrow the idea from Gray Swan's `observe_violation` field, which preserves the original signal when a rule is observe-only. A vendor showing its near-misses reads as confident; a vendor showing only confirmed hits reads as marketing.

**Replay bundle:**
- `Download bundle` button on every finding
- Bundle = playbook + version, target/env manifest, seeded artefacts, expected oracle verdict, and a `verify.py` or `verify.sh` that re-runs and prints pass/fail
- Emit and display a **SHA-256 of the bundle** in the UI
- `README.md` inside the bundle must state plainly: this re-verifies without any Fission service or account

**Acceptance:** button produces a real archive; the hash shown in the UI matches the file on disk; the bundle's verify script runs to completion in a clean directory with no Fission credentials present. Test this last condition literally — `docker run` a clean image if you can.

---

### WI-4 — Demo harness + recorded fallback (P0, 8h)

**One chain. Rehearsed until boring.**

The chain: plant in a Doc as account A → Gemini sidebar processes it → action fires in account B's Gmail. This is the single artefact none of the three competitors can produce. Gray Swan's IPI scenarios include email assistants and computer-use agents, but simulated, in their harness. Ours is the real product with a real second account.

**Build:**
- `demo/run_chain.py` — single command, no arguments, deterministic ordering
- Split view: left = live Playwright viewport; right = oracle gates evaluating in real time, each gate turning green/red as it resolves
- Total runtime under 3 minutes. Time it. If it runs over, cut a step.
- Explicit failure state — if a step times out, show `STEP FAILED — falling back to recording` rather than hanging on a spinner

**Fallback, non-negotiable:**
- Full screen recording of a successful run, cued and ready in a second window
- Announce it at the top of the demo: *"this drives a live product over a real network, so I have a recording cued if the network disagrees with me."* Saying this upfront converts a possible fumble into evident professionalism. Saying it after a failure converts it into an excuse.
- Record at least two clean takes tonight, before you are tired.

**Acceptance:** ten consecutive successful runs, or the recording becomes the primary and the live run becomes the optional encore. Ten. Not three.

---

### WI-5 — Gold-set labelling tooling (P1, 2h)

Minimal CLI or single-page tool to get 100–150 attempts labelled fast, because WI-2 is blocked on it.

- Input: sampled attempts from existing runs, stratified across surfaces and playbook families
- Two independent labellers minimum, blind to each other, third for tie-break — this is what makes the human–human ceiling computable, so it is not optional
- Schema: `item_id, labeller_id, label ∈ {success, failure, ambiguous}, rationale (free text, one line)`
- Output: single CSV that WI-2 consumes directly
- Ship the labelled CSV inside the repo so the numbers are reproducible

**Do this first on the timeline** (see §6) even though it is P1 — WI-2 cannot compute anything without it.

---

### WI-6 — Roadmap-only screens (P1, 2h)

Static, obviously-mocked screens for the cut items. Grey them, watermark `ROADMAP — NOT BUILT`, and be explicit in the meeting. Do not let these render inside the live app where they could be mistaken for shipped features.

- Vendor-version drift timeline (a single Playbook re-run across target versions — green, green, red after a silent update)
- MITRE ATLAS technique mapping column
- Multi-surface expansion beyond Workspace

---

## 5. Competitor UI reference

Researched from live vendor sites and docs, July 2026. Use as design reference; do not copy visual identity.

**Alice (ex-ActiveFence) — the most complete dashboard story, study this one**
- WonderBuild home: application inventory — apps with status, assessment count, last-assessment date; framed as tracking every application from first test to production sign-off, with visibility into outstanding issues and findings with regulatory implications
- Threat Severity Breakdown widget pairing counts with technique names (e.g. 1 Low: model behaviour probing; 4 Medium: indirect prompt injection via data; 2 High: model poisoning; 3 Critical: indirect prompt injection via data)
- Finding detail = the actual transcript, labelled unsafe against two named policies simultaneously, with an explanation
- WonderCheck header strip: apps monitored, live tests, token usage, drift detected, tokens balance, plus schedule-a-test and last-assessments
- Drift panel: "17 apps monitored / 8,420 live tests / 3 applications affected by drift"
- Guardrails mapped to EU AI Act, ISO 42001, NIST, MITRE ATLAS, OWASP, with every decision logged
- **Steal:** the inventory shape, severity-with-technique pairing, transcript-as-finding-detail
- **Avoid:** token balance widgets, launch-readiness go/no-go

**Gray Swan — thin public console, strong verdict object**
- Console spine: Activity, Cygnal, Policies. Shade is sales-led enterprise with no self-serve path; its public artefact is a report (severity-rated findings, reproducible test cases, remediation guidance, re-test after fix)
- Monitor API returns a structured verdict: `violation` 0–1, `observe_violation`, `violated_rules` indices, `violated_rule_descriptions`, `mutation` bool, `ipi` bool, `applied_policies`, `block_message`
- **Steal:** `observe_violation` semantics (WI-3 near-miss state), `ipi` as a first-class typed field rather than a free-text category
- **Note:** their score carries no error bar anywhere. This is the gap WI-2 occupies.

**Reinforce Labs — could not fetch, site blocks automated access**
- From secondary sources: engagement scorecard with letter grades per assistant, per-turn rubric scored against the customer's own failure definition, described as calibrated and human-in-the-loop anchored; customer keeps every artifact
- **Have a human open the site manually before citing their figures in the room.** Do not put their numbers on a slide sourced only from snippets.

---

## 6. The 36 hours

| Window | Work | Owner |
|--------|------|-------|
| H0–H2 | Label the gold set (WI-5 tooling + labelling session). Everything else is blocked on this. | Human + 2nd labeller |
| H0–H3 | In parallel: rewrite talk track, three win claims, four objection answers per §2 and §8. Zero engineering, highest return on the list. | Human |
| H3–H9 | WI-2 Judge Quality panel | Agent |
| H3–H11 | WI-4 demo harness; **record two clean fallback takes by H11** | Agent + human |
| H9–H12 | WI-1 Surface Inventory | Agent |
| H12–H15 | WI-3 finding detail + replay bundle | Agent |
| H15–H17 | WI-6 roadmap mocks | Agent |
| H17–H20 | Ten consecutive demo runs. Fix or downgrade to recording. | Human |
| H20–H24 | Draft the authorisation ask (§9). Rehearse objections out loud, twice. | Human |
| H24+ | **Sleep.** A rehearsed presenter on six hours beats an exhausted one with one more feature. | Human |

If anything slips, cut in this order: WI-6, then WI-1, then WI-3's near-miss state. **Never cut WI-2 or the WI-4 recording.**

---

## 7. Definition of done

- [ ] Judge Quality panel computes κ, α, human–human α, FP and FN with Wilson intervals, per-gate reject rates and escalation share, all from a checked-in labelled CSV
- [ ] PPI-corrected ASR with 95% CI renders in the format `X.X% ±Y.Y (PPI, 95% CI)` — the interval is on the number itself, never in a footnote or methodology tab
- [ ] Zero unlabelled illustrative figures anywhere in the app. Grep for hardcoded percentages before you call it done.
- [ ] Surface Inventory renders four rows including the cross-account chain
- [ ] Replay bundle downloads, hash matches, verify script runs clean with no Fission credentials
- [ ] Demo chain runs ten consecutive times, or recording is primary
- [ ] Fallback recording exists, is cued, and has been watched end to end
- [ ] No instance of "the AI you bought" or Gemini-is-weakest framing in any surface, slide, or seed file
- [ ] Authorisation ask drafted as a concrete paragraph, not an intention

---

## 8. Talk track (human, not code)

**Open, 30 seconds.** Published system-card research shows browser-surface injection success falling from roughly 31% to roughly 0.5% once the full product safeguard stack is applied. That delta is the product, not the model. Nobody — including internal teams — is measuring it end to end through the real UI, across accounts, with a calibrated judge. That is the gap Fission is built for.

**Three things we win on, rewritten for a builder audience:**

1. **Surface.** Model-level and harness-level results are a lower bound on deployed-system robustness. We measure the deployed system with the safeguard stack in the loop. Ask anyone else to show a cross-account plant firing from a Doc sidebar in the real product.
2. **Unit of work.** A corpus entry and a hand-found exploit become the same versioned object, so a 2026 exploit is still runnable against a 2027 Gemini. Nobody else has that artefact.
3. **Measurement.** We publish the judge's error rate. Everyone else says trust our findings. [Show Judge Quality panel.]

**Where we are behind, and what to say:**

- *No Run data.* Concede in one sentence. Then: here is the measured accuracy of the instrument that will produce them, and here is the empty Run 1 panel it will populate. We shipped the instrument before the readings. Hold us to the four numbers.
- *No runtime product.* Do not fight it. Position as the discovery layer that exports findings into whatever guard already runs. Complement, not competitor.
- *Scale and compliance.* Offer a scoped pilot, self-hosted, with a named certification roadmap. Do not pretend the posture exists.

**Questions to expect in this specific room:**

- *"How is this different from what Gray Swan already does for us?"* — Their harness simulates email assistants and computer-use agents. Ours drives the shipped product with a second real account. Different question, different tool, and they are compatible.
- *"We test this internally."* — Almost certainly at model and harness level. Ask whether the internal number carries an error bar and a judge false-positive rate. That is the moment the room turns — but ask it as a genuine question, not a gotcha.
- *"What is your proof?"* — The replay bundle. Hand it over, let their team re-verify without touching our platform.
- *"What about ToS and authorisation?"* — See §9. Do not improvise this.

---

## 9. The authorisation ask (draft before the meeting)

Our own honest read calls the authorisation and ToS story for driving live Workspace accounts unproven. In this room it is the first hard question and it can end the meeting — but it also flips, because Google is the one party on earth that can authorise it.

Walk in with a written paragraph requesting, specifically:

- A scoped test tenancy, isolated from production data
- Written authorisation to drive accounts within it via browser automation
- Agreed rate limits and a defined testing window
- A named technical contact for escalation
- Agreed disclosure handling for anything found

Improvising this loses the meeting. Having it drafted turns the hardest question into evidence of seriousness.

---

## 10. Guardrails on the numbers

Publishing our own error rate hands people a stick. Mitigations, all mandatory:

- Always render the human–human ceiling next to the oracle figure
- Version the panel per oracle release; never silently restate a past number
- Never let a marketing pass round anything up. The moment the numbers are softened, the entire differentiator collapses.
- Small n is disclosed in the caption, not hidden by tuning

---

## 11. Open questions — resolve before the meeting

1. **Is Google evaluating Fission as a vendor for their own red-team org, or as a partner needing their blessing to operate?** This changes the emphasis materially. If the latter, §9 stops being a defensive answer and becomes most of the meeting. Find out in advance if there is any way to.
2. **Re-verify the safeguard-delta figures** (roughly 31% → 0.5% browser, roughly 7% → 2% coding) against the current published system cards. They are quoted from a July 2026 internal brief and this audience will know them better than we do. Getting one wrong in front of them costs more than the point is worth.
3. **Reinforce Labs figures** are secondhand — site blocks automated access. Verify manually or drop them from any slide.
4. Which four numbers exactly does Run 1 commit to publishing? Name them on a slide and invite the room to hold us to them.
