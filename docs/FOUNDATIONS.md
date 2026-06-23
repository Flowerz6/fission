# Project Foundations: Research Grounding for the Tool

## What this is

This is the research basis for `advtest`, an automated AI vulnerability testing
tool targeting productivity AI products. Read it before writing any code.
Reference its citations in code comments, plugin metadata, and the eventual
pitch deck.

## Threat model

Indirect prompt injection (IPI) against productivity AI is the primary threat
class this tool targets. Direct jailbreaks are secondary, included for coverage
and composition.

### Foundational paper

**Greshake et al. 2023 — "Not what you've signed up for: Compromising
Real-World LLM-Integrated Applications with Indirect Prompt Injection."**
arXiv:2302.12173, AISec '23.
Coined IPI. Showed that LLM-integrated applications blur the line between
data and instructions. Demonstrated against Bing Chat. Cite as the origin
of the threat class.

### Industry framing

OWASP LLM Top 10 (2025) classifies prompt injection as LLM01:2025, the top
LLM security vulnerability. Distinguishes direct injections (user input
alters behavior) from indirect injections (external content alters behavior
without user awareness). Use this framing in severity assessments and the
pitch deck.

### Real-world Workspace evidence

These public disclosures prove the threat class is real on the exact surface
Google asked about. Cite them in pitch materials.

- **GeminiJack** (Noma Security, Dec 2025). Zero-click data exfiltration via
  shared Google Docs. RAG pipeline confused instructions with content.
- **Gemini Trifecta** (Tenable, Sept 2025). Three IPI flaws in Gemini Cloud
  Assist, Search Personalization, and Browsing.
- **"Invitation Is All You Need!" / Promptware** (Nassi et al. 2025). IPI via
  Google Calendar invites against Gemini for Workspace.
- **Slack AI exfiltration** (PromptArmor, 2024). Data theft via markdown
  image rendering chain. The exfiltration mechanism (image-tag URL) applies
  identically to Workspace targets.

## Benchmarks and prior frameworks

### Benchmarks to align with

- **InjecAgent** (Zhan et al. 2024, ACL Findings). 1,054 IPI test cases
  across 17 user tools and 62 attacker tools. Attack-intention taxonomy
  (direct harm vs. data exfiltration) is the basis for our `Behavior` model.
- **AgentDojo** (Debenedetti et al. 2024, NeurIPS D&B). Dynamic environment,
  70 tools, 97 user tasks, 27 injection targets. Used by US/UK AISI for
  joint red-teaming. Closest existing thing to what we're building. Run
  it as one of our scenarios; position our tool as going further by
  attacking real Workspace surfaces, not simulated ones.
- **Agent Security Bench** (Zhang et al. 2025, ICLR). Comprehensive agent
  attack taxonomy: DPI, IPI, memory poisoning, tool-response injection.

### Critical perspective

- **Bhagwatkar et al. 2025 — "Indirect Prompt Injections: Are Firewalls All
  You Need, or Stronger Benchmarks?"** Critiques AgentDojo and ASB.
  Introduces a three-stage cascading attack strategy (standard injection →
  second-order → adaptive) — directly informs our orchestrator's attack
  composition logic.

### Standard for evaluation

- **HarmBench** (Mazeika et al. 2024, ICML). De facto standard for ASR
  measurement. We use the HarmBench classifier as one of three judges and
  HarmBench behaviors as part of the seed catalogue.

## Attack techniques

### Ship these (high priority)

- **PAIR** (Chao 2023). Twenty-query attacker/judge loop. Ship as a payload
  generator that composes with indirect injection, not as a standalone
  attack. Don't expect standalone success on frontier models.
- **Crescendo** (Russinovich 2024). Multi-turn benign-to-harmful escalation.
  PyRIT supports it natively.
- **PAP** (Zeng 2024). Persuasive Adversarial Prompts taxonomy. Ship as a
  payload template library; cheap, composes well.
- **Many-shot Jailbreaking** (Anil et al. 2024, NeurIPS). Long-context
  attack using hundreds of fake assistant demonstrations. Highly relevant
  for Gemini's long context. Power-law scaling is a pitch-friendly graph.
- **Best-of-N Jailbreaking** (Hughes et al. 2024). Repeatedly samples
  augmented prompt variations. 78% ASR on Claude 3.5 Sonnet at N=10,000.
  Power-law scaling. Composes with MSJ for 28x sample efficiency. This is
  our scaling story.
- **AgentVigil / AGENTFUZZER** (2025). MCTS-based black-box fuzzing for IPI.
  71% on AgentDojo against o3-mini. The MCTS payload search is what our
  orchestrator should aspire to.

### Ship if budget allows

- **TAP** (Mehrotra 2024). PAIR + tree search. Marginal gains; PyRIT has it.
- **AutoDAN** (Liu 2024). GA jailbreaks. Lower priority than PAIR + composition.
- **Speak Easy** (Zhao 2024). Multi-turn task decomposition.
- **FigStep** (Gong 2023). Multimodal: render instruction as image. Cheap
  and Gemini is multimodal. Ship.

### Skip or downgrade

- **GCG** (Zou 2023). Published universal suffixes do not transfer to
  current frontier models. Keep as historical baseline only.
- **Visual Adversarial Examples** (Qi 2023), **HADES** (Li 2024). Heavier
  weight, lower priority.

## Hiding techniques (the wedge)

For document-targeted attacks. Each technique must ship with a citation in
its `references` field. Citation sources below.

- **Zero-width / hidden Unicode / Unicode tag chars**: Boucher et al. 2022,
  "Bad Characters: Imperceptible NLP attacks." Cited in Greshake et al.
- **White text, tiny font, off-page text boxes**: documented in real-world
  disclosures rather than a single paper. Cite GeminiJack and Promptware.
- **Comments, footnotes, tracked changes, headers, metadata**: Promptware
  (Nassi et al. 2025) and GeminiJack disclosure.
- **Encoded payloads (base64, ROT13, etc.)**: covered in BoN's augmentation
  set (Hughes et al. 2024).
- **Role-confusion / system-message mimicry**: Greshake et al. 2023 taxonomy.
- **Tool-response mimicry**: InjecAgent (Zhan et al. 2024), ASB.
- **Markdown/HTML injection causing exfiltration via image rendering**: this
  is the Slack AI attack chain (PromptArmor 2024) and the GeminiJack
  mechanism. Highest-impact technique; both citations required.
- **Linked content / cross-doc injection**: Promptware, GeminiJack.

## Judge layer

Three-judge ensemble with disagreement-routed HITL. Each judge:

- **HarmBench classifier** (Mazeika et al. 2024). Fast, standard.
- **StrongREJECT** (Souly et al. 2024). LLM-as-judge with rubric. Catches
  hedged/partial compliance other judges miss. The paper itself motivates
  ensembling and human review of flagged outputs because classifiers are
  imperfect under repeated sampling.
- **Llama Guard 3** (Meta). Or ShieldGemma (Google) — Llama Guard 3 is more
  common in research; ShieldGemma might land better with Google. Make this
  configurable; default to Llama Guard 3.

## Frameworks

- **Microsoft PyRIT.** Fork this. Battle-tested by MS AI Red Team. Ships
  with Crescendo, TAP, Skeleton Key. Architecture
  (targets/converters/orchestrators/scorers) maps onto our needs. Save
  weeks; gain credibility with Google. Disclose the fork explicitly in the
  pitch.
- **Garak** (NVIDIA). Lighter weight, similar ideas. Reference but don't fork.
- **Inspect** (UK AISI). Eval framework; AgentDojo runs on it.
- **PromptArmor** (defender side). State-of-the-art guardrail.
  <1% FPR/FNR on AgentDojo. Cite as what our tool needs to bypass.

### Tool-use coverage via a controlled local agent

The Workspace adapters (Prompt 11) talk to Gemini in `api_mode`, which
only does summarisation — Gemini won't actually send email, share docs,
create calendar invites, or trash files from a document-summarisation
context. Without a tool-using DocumentTarget the four
`unauthorized_action` seed behaviours are untestable end-to-end.

We close this gap with `advtest_ext/adapters/tool_use_agent/`: a local
ReAct agent driving an OpenAI-function-calling-capable model (vLLM /
Ollama) over four simulated tools (`send_email`, `share_document`,
`create_calendar_event`, `delete_file`). The tools are pure simulators
— they record calls to a `CallLog` and return plausible-looking
strings; *no real Workspace actions occur*. Threat-model framing
follows InjecAgent (Zhan et al. 2024), which established tool-using
agents as the canonical IPI threat model. Disclose this distinction
explicitly in pitch materials so the audience doesn't conflate
end-to-end Workspace tool actions with our local-agent simulation.

## Open problems we credibly address

These aren't solved in the literature; pitch them as our contributions:

1. Productivity-AI-surface coverage (real Workspace, not simulated).
2. Hiding-technique × payload composition taxonomy (the demo heatmap).
3. Document-format-specific techniques (OOXML, comments, suggested edits).
4. Reproducibility infrastructure: every finding ships a replay bundle.
5. Continuously curated attack library (the labelling-team moat).

## Pitch citations (short list)

Five papers maximum:

1. Greshake et al. 2023 — IPI threat class.
2. AgentDojo (Debenedetti et al. 2024) — what we extend.
3. Many-shot Jailbreaking (Anil et al. 2024) — long-context scaling.
4. Best-of-N (Hughes et al. 2024) — automation scaling.
5. HarmBench (Mazeika et al. 2024) — judge/behavior standardisation.

Plus Workspace-specific disclosures (GeminiJack, Gemini Trifecta,
Promptware) as evidence the threat is real on the exact surface Google
asked about.
