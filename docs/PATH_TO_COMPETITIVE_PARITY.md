# Path to Competitive Parity — what Fission is missing to compete with Silmaril and Straiker

> Companion to [COMPETITIVE_LANDSCAPE.md](COMPETITIVE_LANDSCAPE.md) (who they are) and
> [FISSION_SCALABILITY_REVIEW.md](FISSION_SCALABILITY_REVIEW.md) (why it can't deploy yet).
> This doc answers one question: **what capabilities must Fission add to reach their standard,
> and in what order.** It assumes the goal is to compete head-on, not just to occupy a niche.

---

## 1. The core gap in one sentence

**Both competitors sell a defensive product that protects a live customer system; Fission
produces no defensive artifact at all.** It attacks and it measures. Silmaril's and Straiker's
businesses are runtime firewalls/guardrails, and their red-teaming exists only to feed those
defenses. To compete on their terms, Fission needs to turn its attack-and-measure engine into
something that also *protects*, and it needs to be deployable, integrable, and continuous. To
compete on its own terms, it needs to be the rigorous measurement layer they lack. Either path
requires most of the same platform work below; they diverge only at the top of the stack.

---

## 2. Capability gap matrix

Severity: 🟥 must-have to compete · 🟨 needed for parity · 🟦 differentiator to keep or extend.

| Capability | Silmaril | Straiker | Fission today | Gap |
|---|---|---|---|---|
| Live attack engine | ✅ | ✅ (Ascend) | ✅ strong | 🟦 keep |
| High-fidelity real-UI target | partial | partial | ✅ Gemini/Workspace | 🟦 keep, protect |
| Calibrated measurement (ASR±CI, gold set) | ✗ | ✗ | ✅ | 🟦 extend, lead with it |
| **Runtime defense / guardrail** | ✅ core | ✅ (Defend) | ✗ | 🟥 build |
| **Low-latency inline classifier** | ✅ ~15ms | ✅ | ✗ (batch LLM judges) | 🟥 build |
| **Kill switch / block action** | — | ✅ | ✗ | 🟥 build |
| **SDK / API integration** | ✅ 5-line SDK | ✅ | ✗ (CLI only) | 🟥 build |
| **Deployable at scale** | ✅ | ✅ | ✗ (single-process) | 🟥 build (see scalability review) |
| **Continuous / scheduled testing** | ✅ | ✅ | ✗ (fire-and-forget CLI) | 🟥 build |
| **Multi-tenancy / RBAC / auth** | ✅ | ✅ | ✗ (single DB, token only) | 🟥 build |
| Agent/MCP discovery & inventory | — | ✅ (Discover) | ✗ | 🟨 build |
| MCP ecosystem testing | partial | ✅ scanner | ✗ | 🟨 build |
| Coding-agent targets (Cursor/Copilot/Claude Code) | ✅ | ✅ | partial (tool_use/agentdojo) | 🟨 extend |
| Agent-to-agent / inter-agent attacks | — | ✅ | ✗ | 🟨 build |
| Closed self-improving loop (retrain + deploy) | ✅ <1hr | ✅ | partial (gold set, no trained defense) | 🟨 close |
| Compliance (SOC2 / ISO 27001) | ✅ | ✅ | ✗ | 🟨 obtain |
| Published research brand | 2 exploits | ✅ STAR Labs | ✗ | 🟨 start |
| Funding / team / enterprise logos | seed/YC | ~$85M, logos | solo | (business, not eng) |

---

## 3. The missing pieces, grouped

### A. A defensive output — the biggest gap 🟥
Fission has no product a customer runs to be protected. This is what the competitors actually
sell. Three parts, in increasing effort:
1. **A guardrail/classifier** that scores a request-plus-context-plus-trajectory as
   safe/unsafe and can **block** before a tool call or action fires. Fission's `judges/`
   already contain the scoring logic (the layered oracle, decomposed IPI axes); the missing
   step is repackaging a judge as an **inline detector** rather than a batch grader.
2. **Low latency.** The competitors run at tens of milliseconds. Fission's judges are LLM
   ensembles that take seconds and fan out many calls. A competitive guardrail needs a small
   distilled classifier (train it on the gold set — see D) with the LLM judge reserved for the
   uncertain tail.
3. **An enforcement point / kill switch** that sits in the request path and can deny an action
   or shut down an agent, with an audit trail. Fission has none of this today.

### B. Integration and deployment 🟥
A product has to reach the customer's system and survive real load.
1. **An SDK / API** to wrap inference or agent calls (the "5 lines of code" model). Fission is
   a CLI plus a dashboard; there is no library a customer embeds.
2. **Deployability at scale.** Everything in the scalability review: fix the event-loop
   blocking, add retries and a run state machine, move off SQLite, build the browser farm.
   Today it is a single-operator local tool.
3. **Continuous operation.** A scheduler/control plane that re-tests on a cadence and on every
   model release, instead of fire-and-forget CLI runs. The deployment doc already scopes this
   as the net-new "Stage 3" service.
4. **Multi-tenancy, RBAC, and real auth.** One shared SQLite/Postgres and a single bearer
   token cannot serve multiple customers with isolation. This is table stakes for SaaS.

### C. Surface breadth 🟨
The market's attention is on surfaces Fission does not cover.
1. **MCP ecosystem** — a scanner for dangerous MCP tools and supply-chain attacks (Straiker's
   most-marketed capability). Fission touches no MCP today.
2. **Coding agents** — Cursor, Claude Code, Copilot. Fission has simulated `tool_use_agent`
   and `agentdojo` but not the real coding-agent surfaces where the loud exploits land.
3. **Agent-to-agent / inter-agent manipulation** — an emerging class both competitors name.
4. The engine's **registry design already supports adding targets**, so this is additive work,
   not a rewrite. Depth on Workspace-Gemini is the differentiator; breadth here is parity.

### D. Close the self-improving loop 🟨
Fission has the front half (the measurement spine produces a gold set) but not the back half:
a *trained defense artifact* that is auto-updated and deployed. The flywheel design
(L1 SFT / L2 DPO + a promotion gate) is documented but not built. Competitors claim retrain
within an hour. Closing this both matches their claim and produces the classifier that part A
needs — the same gold set feeds both. Do these together.

### E. Enterprise readiness 🟨
- **Compliance:** SOC2 and ISO 27001 are gating for enterprise buyers; both competitors have
  them. This is process and time, not engineering.
- **Data handling:** replay bundles store raw request/response with no redaction; captured
  sessions may hold PII. Retention, quarantine, and encryption-at-rest are needed before
  customer data flows through.

### F. Research and go-to-market brand 🟨
STAR Labs turns disclosures into inbound. Fission's findings are internal and unpublished. The
leverage move here is not to match their disclosure headcount but to **publish one rigorous,
calibrated public benchmark on Gemini IPI**, which plays to the measurement strength.

---

## 4. What not to lose while catching up

The temptation is to chase feature parity and dilute the two things that are actually ahead:
1. **Live UI-mode fidelity on the real Gemini surface.** No competitor tests the true
   Gemini-in-Workspace surface the way Fission does. Protect it even though it is the hardest
   to scale.
2. **Calibrated, auditable measurement.** ASR ± CI with a frozen gold set is a claim neither
   competitor makes. It is the wedge for a buyer who needs a defensible number, and it is the
   engine for the flywheel and the classifier.

Build parity underneath these, do not trade them away for it.

---

## 5. Sequenced roadmap to parity

Ordered so each phase unblocks the next and the differentiators stay intact.

**Phase 0 — make it deployable (prerequisite).** The scalability-review P0s: unblock the
event loop, add retries and a run state machine, Postgres, artifact store, dashboard as a real
service. Nothing product-facing ships until this is done.

**Phase 1 — lead with measurement.** Harden and publish the calibrated ASR pipeline; ship one
public Gemini IPI benchmark. This builds credibility now, using the existing edge, while the
heavier product work proceeds.

**Phase 2 — close the flywheel and distill a classifier.** Train a small detector on the gold
set (the flywheel's back half). Output: a fast classifier artifact and the auto-retrain loop.
This is the shared foundation for the defense product.

**Phase 3 — ship a defensive guardrail + SDK.** Wrap the classifier as an inline detector with
an enforcement point and a kill switch, exposed through an SDK. This is the first product a
customer *runs to be protected* — parity with the competitors' core.

**Phase 4 — continuous operation + multi-tenancy.** The scheduler/control plane and tenant
isolation/RBAC that make it a SaaS rather than a tool.

**Phase 5 — broaden the surface.** MCP scanner, coding-agent targets, agent-to-agent. Additive
via the registry.

**Ongoing — enterprise readiness.** SOC2/ISO, data redaction and retention, encryption at
rest. Start early; it gates enterprise deals regardless of feature completeness.

**The dependency spine:** deployable (0) → measurement credibility (1) → trained classifier
(2) → defense product + SDK (3) → SaaS operation (4) → breadth (5). Phases 0 and 2 are the two
hardest and everything downstream depends on them.
