# Fission — The Flywheel (detailed design)

> The (c) headline: the oracle (and later the agents) **improve themselves** from expert
> corrections, staying calibrated as Gemini / the UI / the attacker surface drift. It
> consumes the **gold set** the [measurement spine](FISSION_MEASUREMENT_SPINE.md) produces.
>
> **Everything here is 🟦 ADD (none built).** It is also the riskiest part — so it is
> **staged**: prove the loop closes on the cheapest layer (L1) before building the rest.

## The loop: measure → train → promote → (active oracle) → measure

```mermaid
flowchart LR
    subgraph MEASURE["1 · MEASURE  (the spine — designed)"]
        GS["Gold set<br/>(response, oracle pred, expert verdict)<br/>grows every Run"]
        PPI["PPI metric<br/>ASR ± 95% CI"]
    end

    subgraph TRAIN["2 · TRAIN  (staged — L1 first)"]
        L1T["L1 SFT — attack-success classifier<br/>pairs: (response_snapshot → expert_verdict)<br/>cheap, deterministic, fastest to move<br/>★ BUILD + PROVE FIRST"]
        L2T["L2 DPO — LLM judge<br/>preference pairs: expert override ≻ judge verdict<br/>family-disjoint from target"]
    end

    subgraph PROMOTE["3 · PROMOTE  (gate — all checks pass, else roll back)"]
        GATE["Promotion gate"]
    end

    ACTIVE["Active oracle<br/>L0 + L1 + L2"]

    GS --> L1T --> GATE
    GS --> L2T --> GATE
    GATE -->|all pass| ACTIVE
    GATE -->|any fail| RB["Roll back<br/>candidate"]
    ACTIVE -->|next Runs label more| MEASURE
    PPI -.published per Run.-> TREND["Run-over-Run trend"]

    classDef add fill:#bcd4f0,stroke:#1a3a6b,color:#0f223a;
    classDef first fill:#cfe0ff,stroke:#1a3a6b,color:#0f223a,stroke-width:3px;
    class GS,PPI,L2T,GATE,ACTIVE,RB,TREND add;
    class L1T first;
```

---

## The promotion gate — the anti-reward-hacking guard

A trained candidate replaces the incumbent **only if it passes every check**. Any failure
rolls it back automatically.

```mermaid
flowchart TD
    CAND["Candidate layer<br/>(new L1 or L2)"]:::add
    K1{"Gold-set macro-F1<br/>≥ incumbent?<br/>(frozen held-out human labels)"}:::add
    K2{"L0 agreement<br/>NOT dropped?<br/>(model-independent audit)"}:::add
    K3{"Bias panel<br/>robustness held?"}:::add
    K4{"Length-bias<br/>below threshold?"}:::add
    K5{"Family disjoint<br/>from target?"}:::add
    OK["Promote → active oracle"]:::ok
    NO["Roll back"]:::no

    CAND --> K1 -->|yes| K2 -->|yes| K3 -->|yes| K4 -->|yes| K5 -->|yes| OK
    K1 -->|no| NO
    K2 -->|no| NO
    K3 -->|no| NO
    K4 -->|no| NO
    K5 -->|no| NO

    classDef add fill:#bcd4f0,stroke:#1a3a6b,color:#0f223a;
    classDef ok fill:#cfeacb,stroke:#1a531b,color:#0f3a10;
    classDef no fill:#f0c8c8,stroke:#6f1414,color:#3f0808;
```

**The key idea — L0 is a frozen, model-independent audit.** The trained layers (L1, L2)
**never see L0's pattern-matched signals during training**. So if a tuned model starts
reward-hacking (gaming the gold set, drifting away from ground truth), its agreement with
L0's deterministic signals (canary present? handshake echoed? beacon URL emitted?) drops —
and check K2 catches it and rolls it back. L0 can't be gamed because it isn't trained.

---

## What trains on what

| Layer | Method | Training signal (from gold set) | Why it's ordered here |
|---|---|---|---|
| **L1 classifier** | SFT | `(response_snapshot → expert_verdict)` pairs | cheapest, direct, deterministic, **fastest to show movement** → prove the loop here first |
| **L2 LLM judge** | DPO | expert **overrides** of the incumbent judge = preference pairs (expert-correct ≻ judge-wrong) | higher value, more delicate; do after L1 proven |
| **Agentic roles** (Extractor / Explorer / Replayer / Intent-Verifier) | prompt + policy updates | triage failures, gate rejections, operator inventory | changes *attack behavior* — riskiest; **last**, and keep prompt-versioned not auto-trained until much later |

---

## Staged build order (do NOT build all at once)

1. **Spine** (gold set + PPI + trend) — prerequisite.
2. **L1 SFT loop + promotion gate** → run the **proof experiment**.
3. **L2 DPO loop**.
4. **Agentic-role tuning** — only once 1–3 are stable.

### The proof experiment (what validates the entire (c) thesis)
> Over K rounds of expert corrections: **L1's macro-F1 vs the expert gold set goes UP**,
> while **L1's agreement with L0 does NOT drop** (no reward-hacking), and L1 **beats the
> frozen pretrained baseline** (ablation). One number, one CI, one ablation — on the
> cheapest layer — *before* committing to L2/agentic. If it closes, (c) is fundable. If it
> doesn't, you learned it cheaply and fall back to (b) without over-building.

---

## Safety / governance (mandatory for an autonomous self-improving offensive system)

- **Provenance:** every promotion logged — which gold labels, which checks, which model
  version became active, when. A Finding can always be traced to the oracle version that scored it.
- **Reversible:** promotion gate gives automatic rollback; add a manual **kill-switch** to
  freeze the active oracle at a known-good version.
- **Bounded autonomy:** the loop tunes *evaluation* (L1/L2) freely; tuning the *attacker*
  (agentic roles) stays gated + logged because it makes the system better at attacking.
- **Label integrity:** gold labels are immutable (`frozen`); retraining reads, never edits.

The flywheel closes the loop the spine opens: spine **measures + collects**, flywheel
**trains + promotes**, the improved oracle scores the next Run, which feeds the spine again.
