# Red-Team Research → Fission Upgrades (2024–2026)

State-of-the-art IPI / agent red-teaming mapped to concrete changes in this
system. Ranked by expected value. Confidence is flagged: **[verified]** =
canonical paper/repo confirmed; **[verify-id]** = surfaced in search with a
plausible-but-unconfirmed 2026 arXiv id — spot-check before ingesting/citing.

Some of these are already partly reflected in the delivery exporter's metadata
tagging (OWASP LLM01, MITRE ATLAS `AML.T0051.001`, Promptware 5-class, MCP03) —
noted inline.

## Tier 1 — highest value, on our exact surfaces

1. **Promptware — "Invitation Is All You Need"** (Gemini + Workspace) **[verified]** ·
   https://sites.google.com/view/invitation-is-all-you-need
   - 5-class Workspace threat taxonomy: (1) short-term context poisoning,
     (2) long-term memory poisoning via Gemini "Saved Info", (3) tool misuse,
     (4) automatic agent invocation, (5) automatic app invocation.
   - **Slot-in:** adopt the 5-class as a first-class technique axis (already
     emitted as `metadata.promptware_class` in the exporter — currently a coarse
     heuristic; make it a real per-guide tag). Its delivery vectors (invite
     title, subject line, shared-doc filename) are ready-made seed templates and
     Playwright injection points.

2. **AgentDojo** (NeurIPS 2024) **[verified]** · https://arxiv.org/abs/2406.13352 ·
   https://github.com/ethz-spylab/agentdojo
   - Its under-used idea: **joint utility + security** scoring.
   - **Slot-in:** add a **utility axis** to the measurement spine — "did the
     agent still do the user's real job?" — so severity isn't inflated when the
     agent is merely broken (WASP's "security by incompetence"). Already have
     AgentDojo entries in the seed corpus.

3. **MCPTox — tool-manifest poisoning** (AAAI) **[verify-id 2508.14925]**
   - Metadata poisoning needs a *different payload grammar* than output-based
     IPI (adapted InjecAgent payloads scored ~0% at the manifest layer).
   - **Slot-in:** a distinct **tool-manifest-coercion mutation operator**,
     separate from content-injection operators; and a calibration set for the
     new class. Tag with OWASP **MCP03: Tool Poisoning** (already emitted for
     `*_skill_leak` / `tool_manifest` behaviours).

## Tier 2 — upgrades to existing engine components

4. **AutoDAN-Turbo** (ICLR 2025 Spotlight) **[verified]** ·
   https://arxiv.org/abs/2410.05295 · https://github.com/SaFoLab-WISC/AutoDAN-Turbo
   - Black-box attacker that *discovers* strategies and accumulates them in a
     growing, retrievable **strategy library** (88.5% on GPT-4-turbo).
   - **Slot-in:** the biggest step beyond our PAIR/TAP/Crescendo/BoN/MSJ set —
     a **strategy-library layer** that mines our own successful traces into
     reusable strategies keyed by surface/target. The measurement spine already
     logs traces → judge scores, which is the exact signal its distillation loop
     needs. Follow-up: AutoDAN-Reasoning (test-time scaling).

5. **Agent Security Bench (ASB)** (ICLR 2025) **[verified]**
   - 16 attack types × 11 defenses × 10 scenarios; introduces a **Plan-of-Thought
     backdoor** targeting the agent's *planning* stage.
   - **Slot-in:** a plan-stage / reasoning-injection operator we lack; and the
     cleanest 16-type taxonomy to reconcile our seed tags against.

6. **AgentHarm** (ICLR 2025) **[verified]** · https://arxiv.org/abs/2410.09024
   - **Per-task rubric + LLM judge**, and a **capability-retention** requirement
     (harm = attended AND capable AND completed).
   - **Slot-in:** give the decomposed oracle's `harmful_effect` axis **per-task
     rubrics** instead of one generic harm prompt (fewer false positives); add a
     capability-retention gate to `complied` so a refusal-shaped-but-broken
     completion isn't scored as harm.

7. **StrongREJECT** **[verified]** · https://arxiv.org/abs/2402.10260 ·
   https://github.com/alexandrasouly/strongreject
   - Refusal-robust 0→1 judge; separates refusal-detection from harm-scoring.
   - **Slot-in:** use as a **cross-check judge** for `complied` and a
     false-positive audit — where our oracle and StrongREJECT disagree, gate on
     confidence (fits the existing confidence-gated calibration).

## Tier 3 — memory poisoning & newer attack classes

8. **MINJA — memory injection** **[verify-id 2503.03704]**
   - Poisons an agent's memory using only normal queries + observed outputs.
   - **Slot-in:** a **multi-session seed family** + a cross-session finding shape
     (payload planted turn N, triggered turn N+k). Pairs with Promptware class 2
     (Gemini "Saved Info"). Note: the pilot marks memory-poisoning as *secondary*
     — build for post-pilot, keep it out of the core 300.

9. **Skill-file injection cluster** — SkillInject / POISE / MalSkillBench **[verify-id]** ·
   aggregator: https://github.com/LLMSecurity/awesome-agent-skills-security
   - Hidden-HTML-comment injection and self-modification ("edit my code") in
     `SKILL.md`-style files; 8-class harm taxonomy.
   - **Slot-in:** new operators as Workspace adds skill/extension surfaces;
     treat specific paper ids as leads.

10. **WASP — web-agent IPI** **[verify-id 2504.18575]**
    - Up to 86% *partial* success; "security by incompetence".
    - **Slot-in:** reinforces graded (not binary) outcome scoring — which we
      already do (partial verdicts) and should keep foregrounding.

## Tier 4 — standards & taxonomy alignment (tag findings to these)

11. **OWASP GenAI** — LLM Top 10 (LLM01:2025 Prompt Injection) + **MCP Top 10**
    (MCP03: Tool Poisoning) **[verified]** ·
    https://genai.owasp.org/llmrisk/llm01-prompt-injection/ ·
    https://owasp.org/www-project-mcp-top-10/2025/ — *emitted by the exporter.*
12. **MITRE ATLAS** — `AML.T0051.000/.001` (prompt injection direct/indirect)
    **[verified]** — *emitted by the exporter.*
13. **Google DeepMind defense context** — CaMeL "Defeating Prompt Injections by
    Design" (2503.18813), Workspace continuous-IPI blog, "Adaptive Attacks Break
    Defenses" (2503.00061) **[verified]**. Notably: after adversarial fine-tuning
    of Gemini 2.5, **TAP still succeeded 53.6%**.
    - **Slot-in:** prioritise adaptive/TAP-style attacks in regression suites —
      they survive the exact defenses Google ships; use 53.6% as a baseline
      sanity check for ASR against hardened Gemini.

## Judge upgrades, in priority order

1. Per-task rubrics (AgentHarm) over a generic harm prompt.
2. Separate refusal-detection from harm-scoring (StrongREJECT) so a fooled
   refusal judge doesn't null a real harm signal.
3. Add a utility axis (AgentDojo) so severity isn't inflated on a merely-broken
   agent.
4. Add a capability-retention gate to `complied`.

A 2026 line of work argues many agent-safety scores conflate capability with
harm (*"Can LLM-as-a-Judge Reliably Verify Rubrics in Agentic Scenarios?"*,
*"Safety, or Just Capability?"*) **[verify-id]** — worth reading before trusting
any single judge axis.
