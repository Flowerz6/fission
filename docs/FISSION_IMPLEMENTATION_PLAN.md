# Fission — Implementation Plan: turning the attack-landscape shortlist into code

> Companion to `FISSION_ATTACK_LANDSCAPE.md`. Maps each prioritized add to concrete changes
> in the existing harness, with what-exists-now / the-gap / the-change / effort.
> Grounded in code read 2026-06-23. File:line cites may drift — re-verify before editing.

**Guiding principle:** the harness is the spine; every item below is an *incremental change to
existing files*, not a new framework. Effort = rough eng-days for one engineer.

---

## Reality check — what's already built (don't rebuild)

Reading the current code changes the plan materially:

- **`attacks/tap_crescendo.py` already has:**
  - A **TAP tree across strategies** — `_propose_strategy` (line 761) proposes new angles per node;
    branching (`self._bf`), pruning (`self._keep_top`), depth (`self._max_depth`) at lines 265-267,
    340-389. Cross-node memory via `_history_digest`.
  - **TextGrad-style per-turn refinement already implemented** — `refine_left`/`refine_baseline`
    (lines 509-513), `_refine_turn` (657), gated by `self._tg_max`/`self._tg_floor` (570-580).
    This is the core of X-Teaming's "attack optimization" loop. **Already done.**
  - **Per-turn fired-signal extraction** — `build_verdict(...)` in `_attempt` (line 820) already
    computes grounded/persona/handshake/canary/beacon signals per turn.
  - **Hybrid confirm gate** — `_confirm_success` (695) runs the LLM ipi_verdict judge before
    declaring a node converged. This is X-Teaming's "verifier" role. **Already done.**
- **`attacks/indirect_injection/techniques.py`** already ships **several OpenRAG-Soc carriers**:
  `WhiteText` (67), `TextBoxOffscreen` (144, ≈off-screen CSS), `ImageAltText` (437, ≈alt-text),
  `ZeroWidthChars` (177, ≈zero-width). Built into the 25-technique registry (~line 866).

**Net effect:** items 1 and 3 from the shortlist are ~70-80% done. The real gaps are narrower and
listed below. This matches the `xteaming_assessment.md` memory note.

---

## WORK ITEMS

### W1 — Tempest: per-turn breadth + leak re-injection  · `attacks/tap_crescendo.py` · ~3-4 days
**What exists:** the tree branches *across nodes* (whole conversations), and each node is a
**linear** conversation that refines/stalls. Fired-signals are computed per turn but only used for
the verdict, not fed back as an escalation gradient.

**The gap (two distinct Tempest ideas):**
1. **Breadth-first *within a turn*:** at a given turn, generate K candidate next-prompts, send all,
   keep the best-scoring branch. Today `_run_node` generates **one** prompt per turn (line 539).
2. **Leak tracking + re-injection:** when a turn partially complies (a `build_verdict` signal fires
   but the node hasn't converged), capture that "policy leak" and explicitly feed it into the next
   turn's attacker prompt as leverage.

**The change:**
- In `_run_node` (483), add an optional `turn_breadth` knob: when >1, call the attacker K times
  (vary the prompt by index), `_send_and_score` each, advance from the best. Reuse the existing
  pruning instinct — just one level down (turn-level, not node-level).
- Thread the per-turn `build_verdict` result (already computed in `_attempt`) into
  `_NODE_FOLLOWUP_USER` (line 526) as a "partial leaks so far: …" block so the attacker presses
  what's already cracking. Lowest-friction, highest-ROI half of this item — **do this first.**
- Keep both behind defaults that preserve current behavior (`turn_breadth=1`).

**Why first:** builds directly on machinery you already have; biggest expected ASR lift per day.

---

### W2 — OpenRAG-Soc: fill carrier gaps + per-carrier ASR baseline · `indirect_injection/` · ~2-3 days
**What exists:** white-text, off-screen text-box, alt-text, zero-width already in the registry.

**The gap:**
- **ARIA-label carrier** and a **CSS hidden-span carrier** (display:none / visibility:hidden span)
  — the two OpenRAG-Soc carriers not represented.
- No **measured per-carrier ASR** view (the benchmark's real contribution).

**The change:**
- Add two `HidingTechniqueImpl` subclasses in `techniques.py` following the existing pattern
  (subclass, set `attack_class`/`requires_capability`/`applies_to`/`references`, implement `apply`,
  attach `self._marker(payload)`), then register them in the `build_*` list (~line 869).
  - `AriaLabelCarrier` — payload in an `aria-label`/`aria-description` on a rendered element.
  - `HiddenSpanCarrier` — payload in a `display:none`/off-DOM-flow span (distinct from white-text).
- Add a **per-carrier ASR rollup** to the dashboard/summary (you already compute
  `asr_breakdown` keyed by `technique_id` — just surface a carrier-grouped view).

**Note:** carrier ASRs are surface-specific — treat OpenRAG-Soc numbers as relative; your own
deterministic-signal scoring is ground truth.

---

### W3 — Adaptive-payload loop for IPI (MUZZLE-lite) · new, small · ~3-5 days
**What exists:** `IndirectInjectionAttack` (attack.py:123) does a **static** payload × technique
cross-product — no feedback from failed attempts.

**The gap:** MUZZLE's Recon → Synthesis → **Reflection** — refine the payload using the failure
signal from the prior attempt, rather than firing a fixed matrix.

**The change (don't port MUZZLE's code — license unconfirmed; reimplement the loop):**
- Wrap the indirect-injection attempt in a short refine loop: on a non-fired verdict, ask the
  attacker LLM to rewrite the *payload* (not the cover doc) given the judge's reasoning, re-render
  via the same technique, retry up to N. This mirrors W1's `_refine_turn` but on the injection
  payload instead of the conversational turn — **consider sharing one refinement helper.**
- Keep it bounded (N small) — every retry is a real browser session.

**Sequencing:** after W1, since they can share the refinement primitive.

---

### W4 — Tooling borrows · `attacks/`, fork hygiene · ~2-3 days total
- **W4a — Re-point the fork at `microsoft/PyRIT`** (Azure repo archived 2026-03-27) and
  **cherry-pick** v0.14.0 attack classes worth having: `PAIRAttack`, `StrategySequenceAttack`,
  Crescendo personas. **Do NOT full-rebase** (upstream went Pydantic-v2 + enforced-async; costly
  for a diverged fork). ~1 day to evaluate + lift the specific classes.
- **W4b — Scenario's GOAT pattern** (Apache-2.0, clean to borrow): add a "pick attack technique
  per turn" selector to the multi-turn attacker, plus bounded **backtracking**. Complements W1's
  turn-breadth. ~1-2 days.
- **Skip:** commercial platforms; full framework adoption; X-Teaming repo code (unlicensed —
  reimplement ideas only).

---

### W5 — New attack *classes* (later, gated) · new modules · ~5-8 days each
Only once Workspace Gemini side-panels expose persistent memory / multi-step tool use:
- **ASB Mixed + PoT-backdoor coverage** — chain existing vectors (your `ComposedAttack` already
  supports composition); add a Plan-of-Thought-backdoor probe.
- **Memory poisoning (MemMorph / MCFA)** — plant records over time, exploit later. Natural fit for
  a *continuous* engagement and the escalation of the Bikash skill-leak finding.

**Defer** until the side-panel surface supports it and the continuous loop is running.

---

## Sequenced roadmap (mapped to the 3 hats)

| Order | Item | Owner hat | Effort | Why this order |
|---|---|---|---|---|
| 1 | **W1 leak re-injection** (half) | Attack+Eval | 1-2d | Pure win on existing machinery |
| 2 | **W2 carriers + ASR rollup** | Attack+Eval | 2-3d | Cheap, extends current registry, measurable |
| 3 | **W1 turn-breadth** (other half) | Attack+Eval | 2d | Costs browser sessions — tune vs budget |
| 4 | **W4a PyRIT cherry-pick + re-point** | Harness/Infra | 1d | Hygiene; unblocks future upstream borrows |
| 5 | **W3 adaptive IPI loop** | Attack+Eval | 3-5d | Reuses W1 refinement primitive |
| 6 | **W4b Scenario GOAT/backtrack** | Attack+Eval | 1-2d | Composes with W1 |
| 7 | **W5 memory/PoT classes** | Attack+Eval | 5-8d ea | Gated on side-panel memory/tools |

**First two weeks of one engineer ≈ W1-leak + W2 + W4a** — all low-risk, build on what exists,
and produce a measurable ASR delta you can show Google.

---

## Honest notes

- **Don't let attacker upgrades outrun the judge.** Every ASR gain here is only trustworthy if the
  judge/deterministic-signal stack keeps false positives down. W2's per-carrier rollup and the
  existing confirm-gate are the guardrails — keep them in the loop.
- **Browser-session cost dominates.** Turn-breadth (W1) and the adaptive loop (W3) multiply
  sessions per finding. Bound them and watch *sessions-per-confirmed-finding*.
- **Biggest single lever is still W1 leak re-injection** — it's a small, local change to a file you
  already own, and it's the mechanism every SOTA multi-turn method shares.
