# Competitive Landscape — Silmaril and Straiker vs Fission

> Research on two players in the AI-agent security space, and an honest read of where Fission
> overlaps, where it is behind, and where it is genuinely differentiated. Compiled 2026-09-13
> from the companies' own sites, their published research, YC, and press. Figures are their
> claims, not independently verified; where reported numbers conflict, both are noted.

---

## TL;DR — the one thing that matters

Both competitors have **already shipped the thing Fission treats as its headline thesis: a
self-improving loop** where autonomous red-teaming discovers attacks that automatically
retrain a defense. Silmaril calls it "self-healing," Straiker splits it across Ascend
(red-team) and Defend (runtime). Neither is a lab curiosity; Straiker has raised about $85M
and Silmaril is a funded YC company with paying customers.

That reframes Fission's position. The "(c) flywheel" is no longer a differentiator on its
own. Fission's real, defensible edges are narrower and deeper: **live UI-mode browser attacks
against the real Google Workspace Gemini surface**, and **measurement rigor** (calibrated
ASR with confidence intervals, a frozen gold set) that neither competitor advertises. The
biggest strategic gap is that **both competitors sell a runtime defense product; Fission is
offense and measurement only.**

---

## 1. Silmaril (silmaril.dev)

**What they are.** A YC company (Spring 2026 batch), two founders, San Francisco. Positioned
as "security for agents that self-improves" and "the first self-healing prompt injection
defense." Core product is a **firewall for AI agents** that wraps inference calls to block
harmful outcomes in real time.

**Founders.** Aum Upadhyay (CEO, ex-AWS security) and Eduardo Velasco (CTO, ex-Amazon,
low-latency ML). Small team, early stage.

**Approach — three parts:**
- **Attack:** autonomous agents reverse-engineer the customer app and continuously probe it
  to find vulnerability chains (data exfiltration, privilege escalation) before attackers do.
- **Protect:** a low-latency classifier firewall that analyzes "application snapshots rather
  than input patterns" and evaluates the agent's whole trajectory against the user's task
  before a protected action fires. Integrates via an SDK ("5 lines of code," e.g. LangGraph).
- **Retrain:** verified exploits feed back and the firewall "retrains and deploys updated
  weights automatically within an hour."

**Claims.** ~15ms P90 inference / ~50ms end-to-end; ~20ms overhead; 95.6% (site) / 96% (YC)
blocking accuracy vs competitors at 61-79%; 131 attack techniques; damages prevented cited
both as $28M total and as $68M + $20M at named customer types. Y Combinator backed; SOC /
ISO 27001 / NVIDIA Inception.

**Signature research.** Two named disclosures used as marketing:
- **SearchLeak** — turned a legitimate microsoft.com link into silent Microsoft 365 Copilot
  data theft (emails, MFA codes, OneDrive, SharePoint), no auth needed.
- **CurseChain** — hid instructions in README comments invisible on GitHub; Cursor obeyed
  them, stole SSH keys, and regenerated the exfiltration code in unrelated projects.

**Read:** offense exists only to feed the defense. The product they sell is the runtime
firewall. Target market is AI-native productivity, analytics, and legal apps, plus coding
agents (Claude Code, Copilot, Cursor, Gemini CLI).

---

## 2. Straiker (straiker.ai)

**What they are.** "The Agentic AI Security Company." Far more mature and better funded:
about $85M raised ($64M Series A in June 2026 led by Marathon, Citi Ventures, Illuminate,
Workday, after a ~$21M launch backed by Bain Capital Ventures and Lightspeed). Named
enterprise customers (Omada Health, Coupa, Amex GBT, EnterpriseDB, Automation Anywhere),
Fortune 500 and frontier-lab reach, OpenAI Select Partner.

**Product — three integrated pillars:**
- **Discover AI** — inventory and governance: maps every agent, MCP server, tool, and AI
  workflow in the enterprise.
- **Ascend AI** — the red-team engine (the piece most comparable to Fission). Continuously,
  adversarially attacks agents across tools, MCP servers, and workflows before production, to
  expose prompt injection, goal hijacking, tool misuse, and inter-agent manipulation.
- **Defend AI** — runtime guardrails: detects and blocks prompt injection, data leakage,
  identity abuse, memory poisoning, and tool manipulation in real time, plus an "agentic kill
  switch" to shut down rogue agents.

**Research arm — STAR Labs.** A prolific, PR-forward threat-research team. Disclosures
include NomShub (Cursor sandbox escape via IPI), a Perplexity Comet zero-click Google Drive
wipe from one email, Ghostfabric (Gemini 3.1 Pro coding-agent exfiltration over VXLAN),
ClawHub/Moltbook (malicious Claude Skills), SmartLoader (MCP supply-chain), and an AI-SRE
Kubernetes ransomware chain via poisoned OpenTelemetry. Headline stats they market: 36% of
successful coding-agent attacks reach remote code execution; 91% of productivity-agent
attacks end in silent data exfiltration; 28.6% of cataloged MCP tools are "dangerous on
their face." They publish a STAR framework meant to supplement NIST AI RMF and OWASP.

**Claims.** Runtime detection quoted at 98.1-99% on Claude Chat/Cowork/Code; a Skills and
MCP scanner that stops IPI; SOC2, ISO 27001, CSA, OWASP recognition.

**Read:** a full enterprise platform (discover → test → defend) with a strong research brand
and enterprise go-to-market. The center of gravity is coding agents, custom enterprise
agents, MCP ecosystems, and chatbots.

---

## 3. Side-by-side

| Dimension | Silmaril | Straiker | Fission |
|---|---|---|---|
| **Category** | Self-healing agent firewall | Full agentic-AI security platform | IPI red-team + measurement harness |
| **Stage / funding** | YC S26, ~2 people, seed | ~$85M raised, enterprise GTM | Solo, pre-product internal tool |
| **Offense** | Autonomous app-specific probing | Ascend AI continuous red-team | tap_crescendo + guides, MCTS/grid |
| **Runtime defense** | Yes (core product) | Yes (Defend AI + kill switch) | **None** |
| **Primary targets** | Coding agents, productivity/analytics/legal apps | Coding agents, MCP, custom enterprise agents, chatbots | **Google Workspace Gemini** (Docs/Gmail/Sheets) |
| **Delivery to target** | SDK wrap of inference calls | API/MCP/agent integration | **Live browser (Playwright) driving the real UI** |
| **Self-improving loop** | Yes ("retrain in <1hr") | Yes (Ascend feeds Defend) | Thesis, partly built (gold set + spine) |
| **Measurement rigor** | Headline accuracy % | Headline attack-success % | **Calibrated ASR ± CI (PPI), frozen gold set** |
| **Research brand** | 2 named exploits | Prolific STAR Labs | Internal, unpublished |
| **Knowledge capture** | Implicit in model | Threat-data corpus | **Human-authored guides from real engagements** |

---

## 4. What they do that Fission does not

1. **They sell runtime defense; Fission does not.** For both companies, red-teaming is the
   discovery engine that feeds a *firewall/guardrail* product. That runtime product is what
   customers actually buy and what recurs. Fission has no runtime component at all.
2. **They cover the modern agent surface: MCP, tools, coding agents, agent-to-agent.** Most
   of the loudest current attacks (NomShub, SmartLoader, ClawHub, CurseChain) are about MCP
   supply chains, Skills marketplaces, and coding-agent sandboxes. Fission is Workspace-Gemini
   specific and does not touch MCP or coding agents today.
3. **They integrate at the API/SDK layer,** which is how a product scales to many customers.
   Fission's UI-mode browser approach is more realistic but far harder to deploy and scale
   (see the scalability review).
4. **They have a research/marketing flywheel.** STAR Labs especially turns disclosures into
   inbound. Fission's findings are internal and unpublished.
5. **They are funded and staffed with enterprise credibility** (SOC2/ISO, named logos,
   partner status). Fission is a solo pre-product effort.
6. **They already claim the self-improving loop** that was meant to be Fission's wedge.

## 5. What Fission does differently (the real edges)

1. **Live UI-mode attacks on the real Gemini-in-Workspace surface.** Fission drives the
   actual Gemini side panel in a real signed-in browser, over Docs, Gmail, and Sheets, with
   traffic interception and streaming-completion detection. This is the highest-fidelity way
   to test what a real user's assistant actually does, and it is exactly the surface the
   competitors reach only through APIs or simulated agents. It is a moat *and* the hardest
   part to operationalize.
2. **Measurement as a first-class product, not a headline stat.** Fission produces a
   *calibrated* attack-success rate with a confidence interval (prediction-powered inference),
   backed by a stratified human-labeled gold set, inter-rater agreement, and blind review.
   Competitors publish single accuracy numbers with no stated calibration method. For a buyer
   who needs a defensible, audit-grade number rather than a marketing figure, this is real
   differentiation.
3. **The layered oracle.** Deterministic ground-truth signals (canaries, beacons, handshakes)
   override a fallible LLM judge, which is a more honest success criterion than an LLM judge
   alone.
4. **Institutional knowledge capture via guides.** Human-authored, mutation-aware, replayable
   attack recipes grounded in real engagement transcripts. This encodes *why* an attack works
   and how to vary it, not just a pass/fail.
5. **Depth over breadth on one hard surface.** The competitors are broad (every agent, every
   MCP server). Fission is deep on the Workspace-Gemini IPI problem, which is a genuinely
   hard, high-value, under-served target.

## 6. Strategic implications

- **The self-improving loop is now table stakes, not a wedge.** Lead with the measurement
  spine and the live-UI fidelity instead. "We give you a calibrated, auditable attack-success
  rate against the real Gemini surface" is a claim neither competitor makes.
- **Decide the category honestly.** Fission today is a *measurement and red-team harness*, not
  a security product. Two viable paths: (a) become the rigorous measurement/benchmark layer
  (calibrated ASR, gold sets, regression tracking) that even defense vendors would want, or
  (b) add a runtime component and compete head-on, which is a much larger build against funded
  incumbents. Path (a) leans into the existing edge; path (b) fights on their turf.
- **The Workspace-Gemini specialization is the differentiator to protect.** It is also the
  scalability bottleneck (browser farm, account management). The moat and the hardest
  engineering problem are the same thing, which is worth being clear-eyed about.
- **Consider publishing.** A single rigorous, calibrated public benchmark on Gemini IPI would
  establish credibility faster than matching STAR Labs' disclosure cadence, and it plays to
  the measurement strength rather than the research-headcount weakness.
- **MCP and coding agents are where the market's attention is.** If Fission stays
  Workspace-only, that is a deliberate focus choice, not an oversight, and should be stated as
  one. If breadth matters, MCP is the obvious next surface and the engine's registry design
  already supports adding targets.

---

## Sources

- Silmaril site — https://www.silmaril.dev/
- Silmaril YC profile — https://www.ycombinator.com/companies/silmaril
- Straiker site and products — https://www.straiker.ai/ , https://www.straiker.ai/products
- Straiker research (STAR Labs) — https://www.straiker.ai/research
- Straiker $64M Series A — https://www.straiker.ai/blog/straiker-raises-64m-series-a-to-secure-the-agentic-workforce
- Straiker launch ($21M) — https://www.prnewswire.com/news-releases/straiker-launches-with-21-million-to-safeguard-ai-302412224.html
- YC / TechCrunch Demo Day coverage — https://techcrunch.com/2026/06/18/the-11-standout-startups-from-ycs-demo-day-according-to-vcs/
