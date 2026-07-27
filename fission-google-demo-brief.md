# Fission — Google Demo Brief

**Handoff document for Claude Code.**
Prepared 25 July 2026. Meeting with Google team in ~36 hours.

---

## 0. How to use this file

This is a working brief, not a spec to implement literally end-to-end. It contains:

- The strategic reframe that must happen before any code is written (§1)
- Competitor research gathered from live vendor sites on 25 July 2026 (§2)
- A prioritised build plan with an hour budget (§3)
- Detailed specs for the two screens that matter (§4, §5)
- Data schemas (§6)
- Design direction (§7)
- Talk track and objection handling (§8)
- Explicitly out of scope (§9)
- Open questions only the human can answer (§10)

**Read §1 and §10 before starting work.** §1 changes what the demo is arguing.
§10 contains a question that may change the entire emphasis of the meeting.

**Time discipline:** the deadline is real and non-negotiable. If a task in §3
is running over its hour budget, cut scope rather than extend. A rehearsed,
boring demo beats an ambitious broken one.

---

## 1. The reframe — read this first

### The problem

The existing competitive brief (`Fission_demo_analysis.pdf`) is built on one
central wedge:

> They test the AI you built. We test the AI you bought.

**That claim inverts in a Google room.** Google *built* Gemini in Workspace.
To this audience, Fission is testing the AI they built — which is exactly the
category Gray Swan already occupies, and Gray Swan is named in eleven frontier
system cards. Every close line in the current deck is aimed at an enterprise
Workspace *customer*. None of them land here.

Assume every talk-track line in the source PDF is wrong until re-checked
against this audience.

### The claim that does work

Nobody — including Google's own internal teams — is running **multi-app,
multi-account attack chains through the real product UI at scale, with a
measured oracle.**

Gray Swan's IPI benchmark includes email assistants and computer-use agents,
but they are simulated, inside Gray Swan's harness. Fission drives the real
Gmail, the real Docs sidebar, a real second account, with in-product filters,
grounding and retries all in the loop.

**Reframed positioning:**

> Deployment-surface coverage that model-level and harness-level testing
> structurally cannot reach.

This is complementary to what Google already runs, not competitive with it.
That framing is both more accurate and more sellable.

### The number that flatters instead of insults

The source PDF (§A.3) advises leading with *"their own numbers say Gemini is
the weakest model — 8.5% attack success in the IPI Arena."* **Delete this.**
Delivered to Google, it is an insult, and it invites a defensive meeting.

Use the inverse, which is the same argument pointed the other way:

- Anthropic states plainly that model-level results are a **lower bound** on
  deployed-system robustness.
- Claude Opus 4.8: browser-surface injection success reported around **31%
  pre-safeguard, ~0.5% with the full product safeguard stack** across 129
  held-out web environments.
- Coding surface: roughly **7% → 2%** with safeguards on.

That delta is produced by *product-level* safeguards, not the model. It is the
entire argument for testing the deployed product, and it credits the safeguard
engineering rather than attacking the model. It is also the one slide that
makes an API-only harness look incomplete.

### The landmine

**Authorisation and ToS for driving live Workspace accounts.** The source PDF's
own honest read calls this unproven. In a Google room it is the *first* hard
question and it can end the meeting.

It also flips into an advantage: Google is the one party on earth that can
authorise this. Walk in with a drafted ask, not an improvisation:

- Scoped test tenancy, isolated from production data
- Written authorisation covering browser automation against Workspace surfaces
- Defined rate limits and traffic markers so their abuse systems can whitelist
- A named technical contact on their side
- Data handling: what Fission retains, for how long, and where

Draft this paragraph in full before the meeting. If it is improvised live, the
meeting is lost.

---

## 2. Competitor intelligence

Gathered 25 July 2026 from live vendor sites. Confidence noted per source.

### 2.1 Alice (formerly ActiveFence) — `alice.io`

**Confidence: high.** Product pages carry annotated dashboard screenshots.

The rebrand from ActiveFence completed in 2026. Founded 2018, NYC + Tel Aviv,
250+ staff. Product line is WonderSuite: WonderBuild (pre-launch red teaming),
WonderFence (runtime guardrails), WonderCheck (post-launch continuous testing
and drift detection). Adversarial data engine branded "Rabbit Hole."

**This is the vendor to study for UI.** They are the only one of the three
publishing real screens. What their screenshots show:

| Screen | Contents |
|---|---|
| WonderBuild inventory | Applications listed with **status**, **assessment count**, **last-assessment date**. Framed as tracking "every application from first test to production sign-off, with clear visibility into outstanding issues and any findings with regulatory implications." |
| Threat Severity Breakdown | Counts paired with technique names, not bare tiers: 1 Low (model behaviour probing), 4 Medium (indirect prompt injection via data), 2 High (model poisoning), 3 Critical (indirect prompt injection via data). |
| Finding detail | The actual transcript, labelled unsafe against **two named policies simultaneously** (e.g. "WonderFence Default" and "Enterprise Security"), with a written explanation. |
| WonderCheck header strip | Apps monitored · live tests · token usage · **drift detected** · tokens balance. Plus "schedule a test" and a last-assessments list. |
| WonderCheck drift panel | "17 apps monitored, continuous monitoring · 8,420 live tests 1st–25th · 3 applications affected by drift." |
| Category view | Risk categories (Child Protection, Misinformation, Hate Speech, Privacy, Financial Safety, Health & Safety, Spam & Automation) with example flagged statements and risk levels. |

**Compliance mapping** (from the WonderFence page): EU AI Act, ISO 42001, NIST,
MITRE ATLAS, OWASP. Every decision logged, audit trail ready.

**Latency claim:** WonderFence under 150ms on the LLM page, under 100ms on the
integrations page. Inconsistent — worth noting if it ever comes up.

**Sales motion:** aimed at CISO, legal and compliance. The launch-readiness
tracking is the thing third-party reviewers single out as making it defensible
internally.

### 2.2 Gray Swan — `grayswan.ai`, `docs.grayswan.ai`

**Confidence: high for Cygnal, low for Shade.** Cygnal is documented in depth;
Shade is sales-led with no self-serve path and no public dashboard.

CMU spin-out. $40M Series A May 2026. Three products: Cygnal (runtime
protection), Shade (automated adversarial testing), Arena (crowd network,
15,000+ red-teamers, 1M+ attack trajectories used to train Cygnal and Shade).
Named in 11 frontier system cards. SOC 2 Type 2 and Cyber Essentials on their
trust centre.

> Note: sources conflict on founding year (2023 vs 2024) and headcount
> (Tracxn ~59 as of April 2026; LinkedIn ~75). Treat as approximate.

**Console structure:** three items — Activity, Cygnal, Policies. All security
events logged to the dashboard at `platform.grayswan.ai/activity`.

**Integration:** base-URL swap. Point an existing OpenAI or Anthropic SDK at
`api.grayswan.ai/cygnal` and pass a Gray Swan key in the header. Blocked
traffic returns a normal completion object with `finish_reason: "violation"`
plus a refusal string, so nothing in the caller breaks.

**The verdict object is worth copying.** `/cygnal/monitor` returns:

```json
{
  "violation": 0.92,
  "observe_violation": null,
  "violated_rules": [2, 3],
  "mutation": false,
  "ipi": true,
  "violated_rule_descriptions": [
    { "rule": 2, "name": "Rule Name", "description": "Rule text" }
  ],
  "applied_policies": [{ "id": "...", "name": "Your Policy" }],
  "block_message": "Message was blocked"
}
```

Three things to note:

1. `ipi` is a **first-class boolean** — indirect prompt injection is elevated
   to its own field, not buried in a category taxonomy.
2. `observe_violation` preserves the original signal when every violated rule
   is configured as observe-only, in which case `violation` is suppressed to
   `0.0`. This is the "watch but don't block" concept. **Steal this** (see §5).
3. `reasoning_mode` is exposed as off / hybrid / thinking — an explicit
   latency-vs-detection-quality dial handed to the user.

**Critically: there is no error bar anywhere.** The score is a bare 0–1 number
with no stated accuracy. This is the gap Fission exploits.

**Shade output** is a report: severity-rated findings, reproducible test cases,
remediation guidance, re-test after fix. No dashboard published.

### 2.3 Reinforce Labs — `reinforcelabs.ai`

**Confidence: low.** The site blocks automated fetching (robots disallowed).
Everything below is from search snippets and must be verified manually before
being cited in the room.

Palo Alto, founded 2024–25, small team, little disclosed funding. Positioning
is "CI for AI" — simulate real and adversarial users across models, chatbots
and agents, grade every turn against your policy. Their attack agent is called
**Flint**, described as having conversation diversity, adaptive steering
(pivots mid-conversation when a line of attack stalls) and self-learning
(prioritises tactics that succeed within an evaluation).

Sells the fix, not just the finding: failure-derived datasets, applied
remediation, embedded forward-deployed engineers. Customer keeps every
artifact — a deliberate contrast with vendors who retain findings.

The engagement scorecard cited in the source PDF (12 assistants, 402 probes,
28.4% ASR, 58 critical findings, letter grades) could **not** be verified from
public sources today. **Do not cite those specific numbers without a manual
check.**

> There is third-party material (a Centific case study) describing human
> annotation feeding Flint's learning loop, using a 1–4 graded scale from
> clearly safe to severe violation rather than binary pass/fail. Useful
> context, not a primary source.

### 2.4 What the market has been trained to expect on first screen

Synthesising across all three, a buyer arriving at any AI red-teaming product
now expects to see five things:

1. An inventory of things under test, with per-item status
2. Severity-graded findings, each with a reproduction
3. A trend line across runs
4. A mapping to a regulatory framework
5. A single go/no-go readiness signal

Fission already produces the underlying objects — Playbook catalogue, Findings
with replay bundles, per-Run PPI metric, yield ledger — but the vocabulary
doesn't match and there is no readiness view. **Renaming is nearly free and
closes most of the gap.**

---

## 3. The 36-hour plan

Ordered by return per hour. Cut from the bottom, never the top.

### Hours 1–3 — Reframe the narrative (no engineering)

Highest return of anything on this list, and it costs nothing but writing.

- [ ] Rewrite the close, the three win-claims, and the four objection answers
      for a builder audience (see §1 and §8)
- [ ] Delete every "AI you bought" line
- [ ] Delete the "Gemini is weakest" attack; replace with the safeguard-delta
      argument
- [ ] Draft the authorisation ask paragraph in full (§1)

### Hours 4–16 — One demo chain that works

- [ ] Pick **one** cross-account chain. Suggested: poisoned content in a Doc →
      Gemini in the Docs sidebar reads it → action fires in a second account's
      Gmail. One chain, executed perfectly.
- [ ] Harden the Playwright path: explicit waits, retry on transient selector
      failure, no reliance on timing
- [ ] **Record a screen capture as fallback and cue it before the meeting
      starts.** Live browser automation in front of a Google audience will
      break. Say up front that a recording is standing by; that reads as
      professional, not weak.
- [ ] Rehearse the chain end to end until it is boring. Target: five clean
      consecutive runs.

Acceptance: the chain runs unattended in under 3 minutes and the oracle scores
it live.

### Hours 17–22 — Judge quality, scaled down

The differentiator. Full spec in §5.

- [ ] Hand-label 100–150 attempts from existing testing (success / failure,
      two labellers where possible)
- [ ] Compute κ (Cohen's kappa) oracle-vs-expert
- [ ] Compute Krippendorff's α for human–human agreement — **this is the
      ceiling and it must appear next to κ**
- [ ] Compute false-positive rate: of everything the oracle called a success,
      what share did experts overturn
- [ ] Per-gate reject rates across the four oracle layers
- [ ] Share escalating to human review
- [ ] Build the panel (§5). Label n honestly and prominently.

Acceptance: every number on the panel is measured, not illustrative. If a
number can't be measured in time, show the empty slot with the date it will be
filled — never a placeholder that could be mistaken for real.

### Hours 23–26 — Rename and reskin

Cosmetic, cheap, closes most of the vocabulary gap (§4).

- [ ] Playbook catalogue → **Surface inventory** with the new row axis
- [ ] Findings list → severity-graded, each with a reproduction
- [ ] Replay bundle → an actual download button with a visible hash

### Remaining hours — Rehearse and sleep

- [ ] Run the full objection list out loud, twice
- [ ] Sleep. A tired presenter loses more than a missing feature does.

---

## 4. Dashboard spec — the changes that matter

Seven changes, ranked. Only the first four are in scope for 36 hours.

### 4.1 Steal the inventory, change the axis ✅ IN SCOPE

Alice's rows are the customer's applications. Fission's can't be — in the
Workspace case the customer owns none of it.

Make the rows **surface × account role × Gemini version**:

| Surface | Account role | Gemini version | Last Run | Open findings | Drift |
|---|---|---|---|---|---|
| Docs sidebar | Sender | 2.5 Pro | 2d ago | 3 (1 critical) | — |
| Gmail summarise | Recipient | 2.5 Pro | 2d ago | 1 (medium) | ⚠ changed |
| Sheets formula assist | Sender | 2.5 Flash | 6d ago | 0 | — |

This single change:

- Closes the vocabulary gap with what buyers expect
- Reframes "single target = narrow addressable market" as **complete coverage
  of a surface nobody else touches**
- Makes the multi-account dimension visible in the data model itself, which is
  the thing no competitor can display

### 4.2 Put the confidence interval in the number ✅ IN SCOPE

Everyone claims calibration. Nobody displays uncertainty.

The headline tile reads:

```
ASR  6.2% ±1.8
     PPI-corrected · 95% CI · n=142
     κ 0.77 (human ceiling 0.81) · FP 4.1%
```

**If the interval is in a methodology tab, the differentiator is gone.** It
goes on the number itself, on the first screen.

### 4.3 Judge Quality as a first-class nav item ✅ IN SCOPE

Full spec in §5.

### 4.4 Copy `observe_violation` ✅ IN SCOPE

Findings that fired the oracle but sat below the promotion gate should be
**visible and labelled**, not hidden. Add a filter chip: `Confirmed` /
`Below gate` / `All`.

A vendor showing its near-misses reads as confident. A vendor showing only
confirmed hits reads as marketing. This costs almost nothing to build and it
is entirely on-brand for the measurement positioning.

### 4.5 The vendor-drift timeline ❌ ROADMAP SLIDE ONLY

The competitors' drift is "your application changed." Fission's drift is "the
vendor changed underneath you, silently."

A chart of a single Playbook re-run across Gemini versions — green, green, red
after a silent model update — is a screenshot Gray Swan and Alice
*structurally cannot produce.*

It is the strongest visual in the product. It is also not buildable in 36 hours
without Run data. **Show it as a designed mock labelled roadmap.** Do not fake
data points.

### 4.6 Replay bundle as a button ✅ IN SCOPE (cheap)

Download, hash displayed, verify-elsewhere. Reinforce's "customer keeps every
artifact" is a positioning line; Fission's should be a control the buyer can
click in the room.

### 4.7 Regulatory mapping ❌ ROADMAP, with one exception

Alice maps five frameworks. Don't hand-wave four.

**MITRE ATLAS is the one to do for real** — IPI techniques map onto it cleanly
and honestly. One real mapping plus a dated roadmap for EU AI Act / ISO 42001 /
NIST / OWASP LLM Top 10 beats four vague ones. If ATLAS doesn't fit in the
time, roadmap all five.

### 4.8 Two things NOT to copy

- **Token usage and balance widgets.** Alice ships these. They make the product
  look metered and consumption-priced — the wrong signal entirely for an
  audit-grade instrument.
- **A launch-readiness go/no-go traffic light.** It implies Fission can bless
  the release of a product the customer doesn't control. Reframe as **exposure
  status per surface** instead. Same visual weight, defensible claim.

---

## 5. Judge Quality panel — detailed spec

### What question it answers

Every vendor in this space ships numbers of the form "we found N exploits" or
"28.4% attack success." That number is the output of a classifier — their
judge — and it inherits the judge's error rate. Reinforce calls theirs
calibrated. Alice sells severity tiers. Gray Swan returns a 0–1 violation
score. **None of them publish how often the judge is wrong.**

The buyer is being asked to accept a measurement from an uncalibrated
instrument. Fission's four-layer oracle is that instrument. This panel is its
specification sheet.

### Contents

| Element | Detail | Why |
|---|---|---|
| **Gold set** | Size, composition, label provenance, date frozen | Makes it a visible object, not an assumption |
| **κ, oracle vs expert** | Cohen's kappa | The headline agreement number |
| **α, human vs human** | Krippendorff's alpha, displayed **immediately beside κ** | This is the ceiling. If annotators agree at 0.81 and the oracle hits 0.77, that is near-ceiling and reads as strong. Without the ceiling on screen, 0.77 looks mediocre to a buyer who doesn't do this for a living. **Non-negotiable.** |
| **False-positive rate** | Of everything the oracle called a success, what share experts overturned | This is the number the talk track weaponises against competitors. You can only ask it if yours is on a screen. |
| **Per-gate reject rates** | How many candidates each of the four layers kills | Diagnostic, not decorative. A gate passing 100% is a rubber stamp that should be deleted; a gate killing 90% is where the selection pressure actually lives. Shows a technical buyer the layers aren't theatre. |
| **Escalation rate** | Share of findings going to human review | The automation rate, stated plainly. Doubles as the unit-economics number. |

### Why it's a nav item, not a tab

Buried in a methodology drawer, it reads as a disclaimer. At the top level
beside Findings and Surfaces, it reads as a product claim: *measurement is a
first-class feature here.* Identical information, different positioning.

### The "instrument before readings" argument

This is the part that solves the biggest honest weakness.

Fission has no Run data — every efficiency figure in the current material is
illustrative. But **calibration numbers don't need a customer.** Hand-label a
few hundred attempts from internal testing and publish real κ, real α, real
FP rate. Those aren't illustrative. They're measured.

That converts the concession from *"we have nothing yet"* to:

> We have no findings yet. Here is the measured accuracy of the apparatus that
> will produce them, and here is the empty Run 1 panel it will populate.

It is the difference between a promise and a pre-registration.

### The risk, and how to hold it

Publishing your own error rate hands people a stick. Mitigate by:

- Always showing the human ceiling next to κ
- Versioning the panel per oracle release
- Never letting a marketing pass round the numbers up

The moment the numbers are softened, the whole differentiator collapses. The
credibility comes precisely from the fact that they are allowed to look
unflattering.

---

## 6. Data schemas

Suggested shapes. Adapt to whatever already exists rather than rewriting.

### Surface

```json
{
  "surface_id": "docs-sidebar",
  "display_name": "Docs sidebar",
  "product": "Google Docs",
  "account_role": "sender",
  "model_version": "gemini-2.5-pro",
  "model_version_observed_at": "2026-07-23T09:14:00Z",
  "last_run_id": "run_014",
  "open_findings": { "critical": 1, "high": 0, "medium": 2, "low": 0 },
  "drift_status": "stable"
}
```

`drift_status`: `stable` | `changed` | `unknown`. Derived by comparing
`model_version_observed_at` across Runs — not by guessing.

### Finding

```json
{
  "finding_id": "fnd_0231",
  "playbook_id": "pb_cross_account_email_plant",
  "playbook_version": "3",
  "surface_id": "docs-sidebar",
  "severity": "critical",
  "status": "confirmed",
  "oracle": {
    "verdict": "success",
    "confidence": 0.94,
    "gates_passed": ["l1_execution", "l2_effect", "l3_concealment", "l4_replay"],
    "gate_rejected_at": null,
    "escalated_to_human": false,
    "human_label": "success"
  },
  "ipi": true,
  "cross_account": true,
  "accounts_involved": ["sender@testtenant", "recipient@testtenant"],
  "replay_bundle": {
    "url": "/bundles/fnd_0231.tar.gz",
    "sha256": "…",
    "size_bytes": 184320,
    "verifiable_offline": true
  },
  "atlas_techniques": ["AML.T0051.001"],
  "created_at": "2026-07-23T11:02:00Z"
}
```

`status`: `confirmed` | `below_gate` | `overturned`. The `below_gate` value is
the `observe_violation` analogue from §4.4 — surface these, don't hide them.

### Run metrics

```json
{
  "run_id": "run_014",
  "started_at": "2026-07-23T08:00:00Z",
  "attempts": 142,
  "asr_raw": 0.071,
  "asr_ppi_corrected": 0.062,
  "asr_ci_95": [0.044, 0.080],
  "oracle_calibration": {
    "gold_set_id": "gold_2026_07",
    "gold_set_n": 142,
    "gold_set_frozen_at": "2026-07-24",
    "kappa_oracle_vs_expert": 0.77,
    "alpha_human_human": 0.81,
    "false_positive_rate": 0.041,
    "escalation_rate": 0.13,
    "gate_reject_rates": {
      "l1_execution": 0.42,
      "l2_effect": 0.31,
      "l3_concealment": 0.18,
      "l4_replay": 0.04
    }
  }
}
```

**Every field above must be either measured or explicitly `null`.** No
placeholder values that could be mistaken for real data. If a number isn't
computed yet, the UI shows the empty state and the date it will be filled.

---

## 7. Design direction

The audience is technical, sceptical, and will be reading numbers rather than
admiring the interface. Restraint is the correct register.

**Two rules that override everything else:**

1. **Uncertainty is rendered, not annotated.** Wherever a rate appears, the
   interval appears with it, at the same visual weight — not as a superscript
   or a tooltip. An error bar drawn beside the number is better than "±1.8" in
   parentheses, because it cannot be skimmed past.
2. **Empty is honest.** Fission has almost no data. Empty states are the most
   frequently seen component in this build. They should state what will fill
   the slot and when, in the interface's own voice: *"Run 1 populates this on
   28 July"* — never a skeleton loader that implies data is coming, and never
   a grey placeholder number.

**Practical constraints:**

- Data-dense tables need a genuine tabular numeral face; misaligned digits in
  a measurement product undercut the entire claim
- Never encode severity by colour alone — the demo may be projected badly, and
  someone in the room may be colour-blind
- Two accent colours maximum. One for confirmed findings, one for below-gate.
  Everything else is neutral.
- Don't reach for the default AI-generated look: cream background, high-
  contrast serif display, terracotta accent. It reads as templated.

**The signature element** should be the measurement itself — the ASR figure
rendered with its interval as the largest thing on the page. That is the
product's thesis, and no competitor's dashboard has anything equivalent.

---

## 8. Talk track and objection handling

### Three things Fission wins on (rewritten for this room)

1. **Surface.** Model-level and harness-level testing both stop at the product
   boundary. Fission goes through it — in-product filters, grounding and
   retries all traversed, real UI, real second account. Ask anyone else to
   show a cross-account email plant firing from a Doc sidebar.
2. **Unit of work.** A corpus entry and a hand-found exploit become the same
   versioned object, so a 2026 exploit is still runnable against a 2027 Gemini.
   Nobody else has that artefact.
3. **Measurement.** The only vendor publishing an attack-success rate with a
   confidence interval and agreement statistics against expert labels.
   Everyone else says "trust our findings."

### Objections

**"Why not just use Gray Swan?"**
Shade attacks your model, your guardrails, your agent. It does not open Gmail
as one user and Docs as another. Different question, different tool — the two
are compatible and we'd expect them to run alongside each other.

**"We already do this internally."**
Then you already have the hard part — the safeguard stack that produces the
31%-to-0.5% delta. What we add is an external, replayable, statistically
characterised harness pointed at the deployed surface, with numbers that carry
error bars and findings your team can re-verify without touching our platform.

**"You have no results."**
Correct — concede in one sentence, then name the four numbers Run 1 publishes:
per-gate reject rates, κ against the gold set, PPI CI width, and the share of
findings escalating to human review. Hold us to them. And here is the measured
calibration of the instrument, today, on n=142.

**"What's your proof?"**
The replay bundle. Hand it over and let their team re-verify without our
platform.

**"Is this even allowed?"**
Do not bluff. The authorisation ask from §1, delivered as a prepared paragraph.

### Close

Model-level testing tells you whether the model is robust. Fission tells you
whether the *deployed surface* is — the filters, the grounding, the retries,
the multi-account paths — and gives you a number with an error bar.

---

## 9. Explicitly out of scope

Do not build these in the next 36 hours. Show as roadmap slides if they come up.

- Vendor-drift timeline (§4.5) — mock only, no fake data
- Regulatory mapping beyond MITRE ATLAS (§4.7)
- Per-gate breakdown beyond a single summary number
- Any runtime or blocking capability — Fission finds, it does not defend, and
  the correct position is to be the discovery layer feeding whatever guard the
  customer already runs
- Any new UI surface beyond the renaming in §4.1
- Multi-tenant, auth, or anything resembling production hardening
- SOC 2 / ZDR / DPA posture — offer a scoped self-hosted pilot with a named
  certification roadmap instead. Do not pretend the posture exists.

---

## 10. Open questions for the human

**The one that changes everything:**

> Is Google evaluating Fission as a **vendor** for their own red-team
> organisation, or as a **partner** who would need their blessing to operate
> against Workspace?

If vendor: lead with the demo and the calibration numbers, and authorisation is
a procurement detail.

If partner: **authorisation moves from hours 1–3 to the entire meeting.** The
demo becomes evidence that the capability is real, and the ask becomes the
substance. Restructure accordingly.

**Others:**

- Do we have enough historical attempts on hand to hand-label 100–150 tonight?
  If not, the calibration panel needs to shrink to whatever n is honestly
  available — and n must be stated on the panel.
- Which Gemini version is currently live on the test tenancy, and do we have
  any record of when it last changed? This determines whether §4.5 can show
  even one real data point.
- Has anyone verified the Reinforce Labs scorecard figures manually? Their site
  blocks automated access (§2.3). Do not cite those numbers unchecked.
- Is there an existing gold set, or is it being created from scratch tonight?

---

## Appendix — sources

Fetched 25 July 2026 unless noted.

| Source | URL | Confidence |
|---|---|---|
| Alice platform overview (dashboard screenshots) | `alice.io/products/platform` | High |
| Alice WonderBuild | `alice.io/products/wonderbuild` | High |
| Alice WonderCheck | `alice.io/products/wondercheck` | High |
| Alice WonderFence (compliance mapping) | `alice.io/products/wonderfence` | High |
| Gray Swan docs — quickstart, console structure | `docs.grayswan.ai` | High |
| Gray Swan docs — monitor API verdict object | `docs.grayswan.ai/cygnal/monitor-requests` | High |
| Gray Swan docs — Shade | `docs.grayswan.ai/shade/ai-vulnerability-assessment` | High |
| Gray Swan Series A announcement | `grayswan.ai/news/gray-swan-announces-series-a` | High |
| Reinforce Labs | `reinforcelabs.ai` | **Low — robots-disallowed, snippets only** |
| Gray Swan IPI paper | arXiv 2603.15714 | Per source PDF, not re-verified |
| System card figures | Per source PDF §A.2 | Not re-verified — check before quoting |

Competitor headcount, funding and founding dates vary across third-party
databases and should be treated as approximate throughout.
