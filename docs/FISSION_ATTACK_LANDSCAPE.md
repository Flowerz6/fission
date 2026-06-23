# Fission — Adversarial Attack & Red-Team Landscape (2024–2026)

> Deep-research synthesis for the Fission continuous UI-mode red-team of Google Workspace Gemini.
> Generated 2026-06-23. Method: fan-out web search → 28 sources → 136 claims extracted →
> 25 adversarially verified (24 confirmed, 1 killed). 111 research agents.
>
> **Scope:** what is NEWER and BETTER than the team's existing stack
> (PAIR, TAP, Crescendo, BoN, MSJ, PAP, document-hiding indirect injection),
> and what to add next.

---

## ⚠️ Read this first — how to treat the numbers

- **All ASR (attack-success-rate) figures below are self-reported by each method's own
  LLM-judge.** They are *directional, not absolute*. Re-baseline every technique against
  Fission's own deterministic-signal + LLM-judge scoring stack before trusting a percentage.
- Several results are **single-harness / single-benchmark** (OpenRAG-Soc carrier ASRs;
  MUZZLE's WASP comparison is not strictly head-to-head). Read as relative.
- Most sources are **recent arXiv preprints** (some 2026); X-Teaming / ASB / ActorAttack /
  Tempest have venue acceptances, others are pre-peer-review.
- **Area 4 (frameworks/tooling — PyRIT, garak, etc.) is NOT covered here** — those claims did
  not survive verification in this pass. A dedicated follow-up research pass is running.

---

## Headline

The frontier has moved **past TAP/Crescendo** on three fronts, and the newest work
(several 2026 preprints) converges on exactly what Fission is building — **trajectory-driven,
adaptive, UI-native red-teaming**. The architecture instinct is validated by the literature;
what's missing is the newest *attacker algorithms* layered on top of the existing harness.

---

## 1. Multi-turn jailbreak SOTA — what beats Crescendo/TAP

| Method | Mechanism | Reported ASR | Verification |
|---|---|---|---|
| **X-Teaming** | Multi-agent: separate **planner / attacker / verifier** agents collaborate, optimize, self-check across turns | up to **98.1%**; **96.2% vs Claude 3.7 Sonnet** (near-immune to single-turn) | ✓ 3-0 |
| **Tempest** | Breadth-first **conversation tree**; branches multiple prompts/turn; **tracks partial-compliance "policy leaks" and re-injects them** | **100%** GPT-3.5, **97%** GPT-4; *fewer* queries than Crescendo/GOAT | ✓ 3-0 |
| **ActorAttack** | Actor-network graph of semantically-linked "actor" nodes → diverse intent-concealing openings | **78.8%** avg (vs PAIR 27.5%, PAP 23.2%, GCG 24.6%); beats Crescendo on diversity | ✓ 3-0 |

**Sources:** X-Teaming [arxiv.org/abs/2504.13203](https://arxiv.org/abs/2504.13203) ·
Tempest [arxiv.org/abs/2503.10619](https://arxiv.org/abs/2503.10619) ·
ActorAttack [arxiv.org/html/2410.10700v1](https://arxiv.org/html/2410.10700v1)
(reference impl: github.com/AI45Lab/ActorAttack + SafeMTData dataset)

**Recommendation (maps onto `attacks/tap_crescendo.py`):**
- **Tempest leak-tracking / re-injection = highest-ROI add.** Near-drop-in generalization of
  the existing node loop: track partial-compliance signals per turn, feed them back as the
  escalation gradient. Maps onto the existing `build_verdict` signal extraction.
- **X-Teaming planner/attacker/verifier split** maps cleanly onto the attacker-provider + judge
  architecture; best reported numbers vs Claude/Gemini-class targets. Essentially the
  guide-steered attacker, formalized into roles.
- ActorAttack is a path-diversification layer (complements PAP). **X-Teaming supersedes it** —
  prioritize X-Teaming if choosing one.

---

## 2. Indirect prompt injection (IPI) — most relevant to Fission

### OpenRAG-Soc — highest-relevance add
- **Source:** [arxiv.org/pdf/2601.10923](https://arxiv.org/pdf/2601.10923) (ACM Web Conference 2026) · ✓ 3-0
- Web-native IPI benchmark, 6,200 social-style pages, **five carriers** with measured ASR:

  | Carrier | Vanilla ASR |
  |---|---|
  | Hidden spans | 34.0% |
  | Off-screen CSS | 30.1% |
  | Alt text | (in matrix) |
  | ARIA | (in matrix) |
  | Zero-width | (in matrix) |
  | **Macro-avg** | **24.9%** |

- Full hygiene stack (sanitization + Unicode normalization + attribution-gated prompting)
  drops macro-avg to **4.7%**. Red-team-tuned **adaptive payloads** raise ASR across *every*
  defense tier but **preserve defense ordering** — hygiene is degraded, not defeated.
- **Recommendation:** Directly extends `indirect_injection/techniques.py` with a measured
  per-carrier ASR baseline. The adaptive-payload methodology *is* the continuous-red-team pattern.

### Benchmark warning — don't standardize on the obvious ones
- **Source:** [arxiv.org/abs/2510.05244](https://arxiv.org/abs/2510.05244) · ✓ 3-0
- **AgentDojo, InjecAgent, Agent Security Bench, τ-Bench are "easily saturated… flawed metrics,
  weak attacks."** Use for **regression coverage only**, not as a sufficiency bar; rely on
  adaptive, harness-generated attacks for real signal.
- ⚠️ The same paper's claim that a **two-firewall defense solves IPI was REFUTED (0-3)** —
  defenses do not hold against adaptive attackers. (Good news for the business thesis.)

### Target-specific Gemini intel (sources, not formally verified — directly on-point)
- GeminiJack (Noma Labs): [noma.security/noma-labs/geminijack](https://noma.security/noma-labs/geminijack/)
- HiddenLayer — Gemini-for-Workspace phishing/content-manipulation:
  [hiddenlayer.com/research/new-gemini-for-workspace-vulnerability...](https://www.hiddenlayer.com/research/new-gemini-for-workspace-vulnerability-enabling-phishing-content-manipulation)
- 0DIN — "Phishing for Gemini": [0din.ai/blog/phishing-for-gemini](https://0din.ai/blog/phishing-for-gemini)
- Dark Reading — invisible/hidden malicious prompts in Gemini:
  [darkreading.com/...google-gemini-ai-bug-invisible-malicious-prompts](https://www.darkreading.com/remote-workforce/google-gemini-ai-bug-invisible-malicious-prompts)
- **Google's own published IPI layered-defense page** (tells you which carriers are already
  mitigated vs open):
  [knowledge.workspace.google.com/admin/security/indirect-prompt-injections...](https://knowledge.workspace.google.com/admin/security/indirect-prompt-injections-and-googles-layered-defense-strategy-for-gemini)

---

## 3. Agentic / tool-use attacks — the class Fission will need next

### MUZZLE — closest published analog to what Fission is building
- **Source:** [arxiv.org/pdf/2602.09222](https://arxiv.org/pdf/2602.09222) · core claims ✓ 3-0; "first cross-app" ✓ 2-1
- Multi-agent (AutoGen-based). Uses the agent's own **execution trajectory to rank UI injection
  surfaces**, then refines payloads via execution feedback in three phases:
  **Reconnaissance → Attack Synthesis → Reflection.**
- **86.7%** combined end-to-end ASR vs WASP's 20% fixed templates; first to demonstrate
  **cross-application hijack** (an injection in one app drives a destructive action in another).
- **Recommendation:** Borrow the **Recon → Synthesis → Reflection loop** and trajectory-based
  surface ranking wholesale — it is the architectural template for the continuous farm.

### Agent Security Bench (ASB) — use as a coverage map, not a bar
- **Source:** [arxiv.org/abs/2410.02644](https://arxiv.org/abs/2410.02644) (ICLR 2025) · ✓ 3-0
- 10 scenarios, 400+ tools, 27 attack/defense types. **Per-ReAct-stage taxonomy:**
  Direct PI (user prompt) · Indirect PI (tool/observation) · Memory Poisoning (RAG/long-term) ·
  **Plan-of-Thought (PoT) backdoor** (system prompt) · Mixed.
- **Mixed/chained attacks scored highest (84.3%)**; memory poisoning lowest (~7.9%) *in ASB*.
- **Recommendation:** Use the taxonomy as the attack-catalogue **coverage map** — especially
  PoT backdoor + Mixed, which the team likely lacks. Mixed being most effective argues for
  **chaining existing vectors** (`ComposedAttack` already supports this).

### Memory poisoning — the natural escalation of the Bikash finding
- **MemMorph:** [arxiv.org/html/2605.26154](https://arxiv.org/html/2605.26154) · ✓ 3-0 — biases
  tool selection by injecting **3 natural-language memory records** (disguised as facts/incident
  reports/policies), not tool-metadata tampering. **Up to 85.9% ASR**, beats PoisonedRAG by ~25%.
- **MCFA (Memory Control Flow Attacks):** [arxiv.org/html/2603.15125v1](https://arxiv.org/html/2603.15125v1)
  · ✓ 3-0 — two-phase: plant action-oriented instructions via **benign interactions**, then
  hijack tool selection + execution order on later tasks despite explicit safety constraints.
- **Recommendation:** Build once Gemini side-panels expose persistent memory / multi-step tool
  use. The "plant via benign interaction, exploit later" pattern is a perfect fit for a
  *continuous* engagement where the red-team plants records over time.

---

## 4. Frameworks / tooling — build-vs-borrow

> Dedicated follow-up pass (2026-06-23): 21 sources → 91 claims → 25 verified (24 confirmed, 1 killed).
> **Bottom line: keep the PyRIT fork as the spine; borrow attack-planning logic, don't replace the harness.**
> No off-the-shelf framework drives a real browser UI multi-turn — that capability is the team's moat.

### Microsoft PyRIT (upstream) — BORROW, behind a migration plan
- **Source:** [github.com/microsoft/PyRIT/releases](https://github.com/microsoft/PyRIT/releases) · ✓ 3-0
- **Repo moved** `Azure/PyRIT` → `microsoft/PyRIT`; the **old Azure repo was archived (read-only) 2026-03-27.**
  Make sure the fork tracks the new upstream.
- **v0.14.0 (2026-06-05)** added: **PAIRAttack**, **StrategySequenceAttack**, **TAP image** support,
  **single-turn Crescendo**, **Crescendo personas**. Also **renamed "Red Teaming Orchestrator" → "Multi-Turn Orchestrator"** (now `*Attack` classes).
- ⚠️ Upstream migrated to **Pydantic v2 + enforced async** and a new `Message`/`MessagePiece` model — a full rebase is **costly** for a fork that has diverged.
- **Recommendation:** **Cherry-pick** PAIRAttack / StrategySequenceAttack / Crescendo-personas logic rather than full rebase. Treat upstream as a parts bin, not a base to re-sync wholesale.

### LangWatch **Scenario** — BORROW the GOAT pattern (Apache-2.0, clean license)
- **Source:** [github.com/langwatch/scenario](https://github.com/langwatch/scenario) ·
  [docs](https://langwatch.ai/docs/agent-simulations/introduction) · ✓ 3-0
- Open-source agent-testing framework; ships a **`RedTeamAgent`** (drop-in for the simulated user) that runs
  **Crescendo and GOAT** with scoring + **backtracking (up to 10)**. **GOAT** (Meta, ICML 2025) **picks an attack technique per turn** dynamically.
- **Target-agnostic via adapters**, but **no documented browser/UI support** — API/agent targets only.
- **Recommendation:** Borrow the **GOAT per-turn technique-selection pattern** and the **backtracking** idea into the existing attacker. Apache-2.0 means code reuse is clean. Skip as a harness (no UI mode).

### MUZZLE — BORROW the loop (highest-priority architectural template)
- **Source:** [arxiv.org/abs/2602.09222](https://arxiv.org/abs/2602.09222) (Feb 2026) · ✓ 3-0
- Adaptive, **trajectory-driven** red-team for **browser web agents** vs indirect prompt injection:
  ranks injection surfaces from the agent's own trajectory, generates adaptive payloads, refines from failed
  executions (44 attacks across 4 apps). **The published method closest to what Fission is building.**
- ⚠️ **Public code release / license unconfirmed** (open question). Borrow the *design*, not necessarily code.
- **Recommendation:** Reimplement the **Recon → Synthesis → Reflection** loop + trajectory surface-ranking in the existing harness. Top architectural borrow.

### Newest jailbreak methods — PORT the planning, not the harness
- **X-Teaming** ([repo](https://github.com/salman-lui/x-teaming), [arxiv 2504.13203](https://arxiv.org/abs/2504.13203)):
  up to 98% ASR — but **API/local targets only, no browser driver**, and **repo is unlicensed** (no clean code reuse). Port the persona-plan-generation idea into Playwright.
  ⚠️ The "four-agent (Attacker/Target/Verifier/Plan-Generator with TextGrad)" architecture detail was **REFUTED (1-2)** — don't assume that exact internal structure.
- **ActorAttack** ([repo](https://github.com/renqibing/ActorAttack), Python, open-source): actor-network clue discovery; **API targets (GPT-4o/Claude)**. Cleanest open repo of the three to study.
- **Tempest** ([arxiv 2503.10619](https://arxiv.org/abs/2503.10619)): tree-search branching + leak re-injection; **not benchmarked on Gemini** (2-1).
- **Recommendation:** All three are **text/API-modality** — borrow their *planning algorithms* (persona plans, actor clues, leak re-injection) and run them through the existing UI-mode driver. None replace the harness.

### Commercial platforms — SKIP (competitive awareness only)
- **Source:** [Lakera Red](https://www.lakera.ai/lakera-red) · [Gray Swan](https://www.grayswan.ai/solutions/ai-red-teaming) ·
  [Cisco AI Defense](https://newsroom.cisco.com/c/r/newsroom/en/us/a/y2026/m02/cisco-redefines-security-for-the-agentic-era.html) · ✓ 3-0
- **Lakera Red** (SaaS; acquired by Check Point 2025) tests AI agents. **Gray Swan** (Shade / Cygnal / Arena;
  Series A June 2026) is **primarily human-driven** red-teaming. **Cisco AI Defense** does adaptive single+multi-turn.
- Vendor claims are **not independently benchmarked**. **Recommendation:** Track for positioning; **not for adoption.**

### Build-vs-borrow verdict (2-3 engineers)
**Keep the fork as the spine.** No framework does UI-mode browser multi-turn — that's exactly the team's
differentiator, so building it is correct. Everything else is a **parts bin**: cherry-pick PyRIT attack classes,
port jailbreak *planning* logic (X-Teaming personas / ActorAttack clues / Tempest re-injection) into the
Playwright driver, and steal **MUZZLE's adaptive-IPI loop** + **Scenario's GOAT/backtracking** patterns.
Prioritize the **MUZZLE loop** as the single highest-value borrow.

**Refuted in this pass:** X-Teaming's specific four-agent/TextGrad architecture (1-2). Treat ASR numbers as
ceilings — all self-reported on non-Gemini targets.

---

## Prioritized shortlist — what to add

**Techniques (Areas 1-3):**
1. **Tempest-style leak-tracking + breadth-first branching** on the Crescendo loop — lowest
   friction, highest ROI, builds on `tap_crescendo.py`.
2. **OpenRAG-Soc five-carrier matrix + adaptive payloads** — extends document-hiding, gives
   per-carrier ASR baselines.
3. **X-Teaming planner/attacker/verifier** multi-agent attacker — best numbers vs
   Claude/Gemini-class, fits the guide-steered design.
4. **MUZZLE Recon → Synthesis → Reflection** trajectory loop — template for the continuous farm.
5. **ASB Mixed + PoT-backdoor coverage**, then **memory poisoning (MemMorph / MCFA)** once
   side-panels gain memory.

**Tooling / borrow (Area 4):**
6. **Borrow MUZZLE's adaptive-IPI loop** (highest-value framework borrow) + **Scenario's
   GOAT per-turn technique selection + backtracking** (Apache-2.0, clean reuse).
7. **Cherry-pick PyRIT v0.14.0** attack classes (PAIRAttack, StrategySequenceAttack,
   Crescendo personas) — and re-point the fork at `microsoft/PyRIT` (Azure repo archived).
8. **Keep the fork as the spine** — no off-the-shelf framework drives a real browser UI
   multi-turn; that capability is the moat. Build it; borrow everything else.

---

## Refuted / killed in verification

- **"A two-component firewall (Tool-Input + Tool-Output) achieves near-perfect, model-agnostic
  security against IPI."** — REFUTED **0-3**. "Perfect security" applied only to four saturable
  static benchmarks, not adaptive attackers. Source: [arxiv.org/abs/2510.05244](https://arxiv.org/abs/2510.05244)

## Open questions (carried into follow-up passes)

1. State + comparison of maintained red-team harnesses (PyRIT, garak, 2025-26 frameworks) for a
   UI-mode browser-driven continuous red-team. *(follow-up pass running)*
2. Do X-Teaming / Tempest / MUZZLE have maintained, license-clear open-source implementations?
3. How do these self-reported LLM-judge ASRs translate against Workspace Gemini specifically
   (Docs/Gmail/Sheets side panels), which none of the cited papers benchmark directly?
4. Which newer defenses (OpenRAG-Soc hygiene stack; tool-input/output firewalls) are actually
   deployed in Workspace Gemini — i.e., which carriers/attacks are already mitigated vs open?

---

## Full source list

**Multi-turn jailbreak (primary):**
arxiv.org/abs/2504.13203 (X-Teaming) · arxiv.org/abs/2503.10619 (Tempest) ·
arxiv.org/html/2410.10700v1 (ActorAttack)

**IPI (primary):**
arxiv.org/abs/2510.05244 (benchmark weakness / refuted firewall) ·
arxiv.org/pdf/2601.10923 (OpenRAG-Soc) · arxiv.org/pdf/2602.09222 (MUZZLE)

**Agentic / tool-use (primary):**
proceedings.iclr.cc/.../2025 (ASB) · arxiv.org/abs/2410.02644 (ASB) ·
arxiv.org/html/2605.26154 (MemMorph) · arxiv.org/html/2603.15125v1 (MCFA) ·
arxiv.org/html/2603.22489v1 · arxiv.org/pdf/2510.06445

**Gemini / Workspace target-specific:**
noma.security/noma-labs/geminijack · 0din.ai/blog/phishing-for-gemini ·
darkreading.com (invisible malicious prompts) · perimeter.net (hacking Gemini trust boundaries) ·
hiddenlayer.com (Gemini-for-Workspace vuln) · knowledge.workspace.google.com (Google IPI defense)

**Benchmarks / leaderboards:**
arxiv.org/abs/2603.06594 · arxiv.org/pdf/2402.10260 · arxiv.org/abs/2508.16347 ·
github.com/JailbreakBench/jailbreakbench · arxiv.org/html/2406.13352v3

**Tooling (unverified — follow-up pass):**
github.com/confident-ai/deepteam · dev.to (promptfoo/deepteam/PyRIT/garak) ·
helpnetsecurity.com (Scenario; deepteam; red-teaming agents) · beyondscale.tech (tools comparison 2026)
