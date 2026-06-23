# Fission — Measurement Spine (detailed design)

> The **first thing to build** for (c). You cannot claim "self-improving" without a
> trustworthy, Run-over-Run improvement metric. The spine produces that metric AND
> grows the gold set that the flywheel later trains on. Most of it is **activate +
> extend** of plumbing that already exists (dormant HITL queue), not from-scratch.

## Legend

- 🟩 **EXISTS** — reuse as-is.
- 🟨 **ACTIVATE / EXTEND** — built but dormant, or needs a small extension.
- 🟦 **ADD** — new build.

---

## 1. The spine, end to end

```mermaid
flowchart TD
    RUN["Completed Run → Traces<br/>(orchestrator persists these)"]:::exists
    ORC["Oracle labels ALL traces<br/>L0 signals + L2 judge + verdict-upgrade"]:::exists

    UNC["Uncertainty score per trace<br/>layer disagreement + near-threshold + low judge-confidence"]:::activate
    SAMP["Stratified sampler<br/>top-K uncertain  +  uniform random slice"]:::add

    QUEUE["HITL queue<br/>enqueue_hitl() / HitlItem (dormant today)"]:::activate
    UI["Review UI<br/>≤30s pre-labelled confirm / override + reason<br/>(extend /hitl route)"]:::activate

    GOLD["Gold-set sink<br/>immutable GoldLabel rows<br/>(oracle prediction + expert verdict)"]:::add

    PPI["PPI metric<br/>ASR ± 95% CI<br/>oracle over full Run, bias-corrected on the labeled sample"]:::add
    TREND["Run-over-Run dashboard<br/>ASR ±CI, oracle agreement (κ/α), FP rate, CI width"]:::add

    FLY["FLYWHEEL (downstream)<br/>L1 SFT / L2 DPO + promotion gate"]:::downstream

    RUN --> ORC
    ORC --> UNC --> SAMP --> QUEUE --> UI --> GOLD
    ORC --> PPI
    GOLD --> PPI --> TREND
    GOLD -. feeds (next phase) .-> FLY

    classDef exists fill:#cfeacb,stroke:#1a531b,color:#0f3a10;
    classDef activate fill:#f8e3a0,stroke:#6b4f0a,color:#3a2a04;
    classDef add fill:#bcd4f0,stroke:#1a3a6b,color:#0f223a;
    classDef downstream fill:#eee,stroke:#888,color:#444,stroke-dasharray:4 3;
```

---

## 2. Gold-set schema (the GoldLabel entity)

Each reviewed trace becomes ONE immutable labeled example. This is simultaneously the
**calibration ground truth** (for PPI) and the **training data** (for the flywheel).

| Field | Type | Why it's here |
|---|---|---|
| `id` | str | PK |
| `trace_id` | FK→Trace | which execution was labeled |
| `run_id` | FK→Run | which run it came from |
| `behavior_id` | str | for per-behavior calibration |
| `oracle_verdict` | enum | what L0–L2 said **at sample time** (frozen) |
| `oracle_score` | float | the judge score at sample time |
| `oracle_layers_fired` | json | which signals / judge fired (for L0↔expert audit) |
| `response_snapshot` | text | the scored text, frozen — label stays valid if the trace changes |
| `sample_reason` | enum | `disagreement` \| `uniform` (needed for unbiased PPI) |
| `expert_verdict` | enum | success / partial / refused — the ground truth |
| `expert_confidence` | int 1–5 | down-weights shaky labels |
| `reviewer_id` | str | inter-rater agreement, accountability |
| `review_seconds` | int | track the ≤30s budget |
| `reason` | text | why the expert overruled/confirmed (sharpens prompts later) |
| `reviewed_at` | datetime | versioning vs model/UI drift |
| `frozen` | bool | immutable once reviewed (audit integrity) |

---

## 3. Sampling design — why two slices

The oracle labels the **whole** run cheaply. Experts only label a **sample**. The sample
has two parts, and you need both:

- **Disagreement-weighted (top-K uncertain):** traces where layers disagree (L0 fired but
  the judge said no, or vice-versa), the judge score sits near the decision threshold, or
  judge confidence is low. This concentrates scarce expert time where the oracle is least
  sure — uncertainty sampling. *Reuses the `ensemble.py` disagreement logic that already exists.*
- **Uniform random slice:** a small unbiased sample across the whole run. **This is the
  load-bearing piece for PPI** — without it the gold set skews toward hard cases and the ASR
  estimate is biased. The uniform slice is what lets you make an *honest* population claim.

Sample size is set by your **label budget** (labels/week). That one number decides how big
each slice is and how many rounds until the L1 loop can plausibly close.

---

## 4. PPI metric — in plain English

You want the *true* attack-success rate but can only afford to expert-label a sample.

- Using only the small expert sample → unbiased but a **wide** CI (few labels).
- Using only the oracle over the full run → **tight** but **biased** (the oracle errs).
- **PPI (prediction-powered inference)** gives you the best of both: take the oracle's ASR
  over the *full* run, then **correct it** by the oracle-vs-expert disagreement measured on
  the labeled sample, and produce a CI that accounts for both the sample size and the
  oracle's reliability.

Output a reviewer can trust: **"ASR = 70% ± 3% at 95% CI"** — *not* "the oracle said 70%."
That single calibrated number is what makes every downstream improvement claim defensible.

---

## 5. Activate vs Add (the build list)

| Piece | Status | Work |
|---|---|---|
| Run → traces, oracle labels | 🟩 EXISTS | reuse |
| Uncertainty / disagreement scoring | 🟨 ACTIVATE | repurpose `ensemble.py` spread logic onto single-judge + L0 signals |
| HITL queue (`HitlItem`, `enqueue_hitl`, `/hitl`) | 🟨 ACTIVATE | turn enqueue on in the default path (today only the dormant ensemble enqueues) |
| Review UI (≤30s pre-labelled confirm/override) | 🟨 EXTEND | extend `/hitl` to show oracle verdict + evidence; one-click confirm/override + reason |
| Stratified sampler (uncertain + uniform) | 🟦 ADD | new — picks which traces enqueue |
| Gold-set sink (`GoldLabel`) | 🟦 ADD | new immutable table (above schema) |
| PPI metric (ASR ±CI) | 🟦 ADD | new — runs per Run on (full oracle labels + labeled sample) |
| Run-over-Run trend dashboard | 🟦 ADD | new dashboard view |

---

## 6. What this unblocks

1. **Labeling can start immediately** — the moment the gold-set schema + review UI exist,
   your experts label the §7 traces you already have. (Long pole; start in parallel.)
2. **The proof experiment becomes runnable** — once the gold set grows, stand up L1, SFT it,
   and show *L1 agreement-with-experts ↑ over K rounds with no L0 regression*. That single
   result validates the (c) thesis on the cheapest layer.
3. **Every later claim gets a CI** — "ASR dropped 8% ±3% after Gemini's update" instead of
   anecdote. This is the credibility moat for a Google/Anthropic audience.

The spine ends at **gold set + PPI metric + trend**. The flywheel (L1 SFT → L2 DPO →
promotion gate) is the next diagram, and it consumes the gold set this spine produces.

---

## 7. Pressure-test — open risks & the refinements they force

**Q: With only ONE judge (no L1, ensemble dormant), is "disagreement" a strong enough
uncertainty signal?** Short answer: yes to start, but it's weaker than a panel — and that
shapes the early design.

- **You still have three valid uncertainty axes without a second model:**
  (1) **L0↔L2 disagreement** — a deterministic signal fired but the judge said no (or vice
  versa). This is genuine *cross-method* disagreement (pattern-match vs LLM), and it's
  actually a strong signal. (2) **Near-threshold** judge score. (3) **Low judge
  self-confidence** (ipi_verdict already emits confidence 1–5). So uncertainty sampling works
  on day one — it just leans on L0↔L2 + confidence rather than judge-vs-judge spread.

- **Refinement 1 — weight the uniform slice HEAVILY at first, shift to disagreement as L1
  comes online.** Early on the only "second opinion" is L0; once L1 exists you get a true
  second decision method to disagree against, which sharpens uncertainty sampling. So the
  spine **bootstraps L1, and L1 sharpens the spine** — a virtuous loop. Make the
  uniform:disagreement ratio a tunable that starts high (exploration) and decays.

- **Refinement 2 — cold-start phase.** Before any gold set exists you can't trust *any*
  uncertainty signal. The first 1–2 runs: label a larger **uniform** sample to establish
  baseline oracle accuracy, *then* turn on uncertainty-weighting.

- **Refinement 3 — blind-spot guard (already partly designed).** Uncertainty sampling never
  surfaces the oracle's *unknown unknowns* (cases it's confidently wrong about). The uniform
  slice is the only thing that catches those — so it must stay non-trivial, permanently, not
  just at cold-start. This is *why* §3 insists on two slices.

- **Refinement 4 — anchoring risk in the ≤30s pre-labelled review.** Showing the expert "the
  oracle says SUCCESS — confirm?" invites rubber-stamping (confirmation bias), which would
  silently inflate measured oracle accuracy. **Mitigation:** blind a random fraction of
  reviews (hide the oracle verdict, show only evidence); compare blind-vs-prelabelled
  override rates to detect/quantify anchoring.

- **Refinement 5 — inter-rater reliability, not just oracle-vs-expert.** PPI assumes the
  expert label *is* ground truth. If your experts disagree with *each other*, that premise is
  shaky. **Mitigation:** double-label a small fraction (`reviewer_id` is already in the
  schema) and track expert↔expert κ; if it's low, the behavior's success criteria need
  tightening before its numbers are trustworthy.

- **PPI validity under a moving oracle:** the flywheel changes the oracle between runs. Safe
  because `oracle_verdict`/`oracle_score` are **frozen at sample time** in the gold label, so
  each run's PPI uses the predictor that actually labeled it. (Schema already handles this.)

**Net:** the spine is sound on a single judge, *provided* the uniform slice carries more
weight early, cold-start uses uniform-only, a fraction of reviews are blind, and you track
inter-rater κ. None of these are new components — they're parameters and a small blind-review
toggle on the review UI.
