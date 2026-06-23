# Fission — Architecture Diagrams (detailed)

Mermaid diagrams (render on the dashboard or any Mermaid viewer). Four views:
1. Layered architecture (the boxes)
2. Run lifecycle (the data flow / spine)
3. `tap_crescendo` search (the core attack)
4. Target production architecture (the proposed control plane)

Each layer is annotated with its **role**, **current choice**, and the **key fork** we
brainstorm in `FISSION_ARCHITECTURE_AND_PRODUCTION.md` §4–5.

---

## 1. Layered architecture (current)

```mermaid
flowchart TD
    subgraph L1["Layer 1 — Spec / Config"]
        CFG["YAML run config"]
        RC["RunConfig (Pydantic)<br/>phases, attacks, judges, signals, budget"]
        REG["Registries<br/>attacks / judges / targets"]
        CFG --> RC --> REG
    end

    subgraph L2["Layer 2 — Orchestration (single process)"]
        ORCH["Orchestrator<br/>Grid (cross-product) | MCTS (UCB1)"]
        BUD["Budget tracker"]
        ORA["Layered oracle<br/>signal fired ⇒ upgrade verdict"]
        ORCH --- BUD
        ORCH --- ORA
    end

    subgraph L3["Layer 3 — Attacks"]
        ATK["AttackPlugin.generate() async-iterator"]
        TC["tap_crescendo<br/>TAP tree of Crescendo nodes"]
        OTH["pair / tap / bon / msj / crescendo<br/>indirect_injection (25 techniques)"]
        ATK --- TC
        ATK --- OTH
    end

    subgraph L4["Layer 4 — Targets (AI under test)"]
        TGT["DocumentTarget interface"]
        WS["Workspace: docs / gmail / sheets<br/>api_mode | ui_mode"]
        MT["multiturn side-panel (live)"]
        AG["tool_use / agentdojo (agentic)"]
        TGT --- WS
        TGT --- MT
        TGT --- AG
    end

    subgraph L4b["Layer 4b — UI automation (Playwright)"]
        UI["headed Chrome, persistent profiles<br/>selectors, interception, streaming reads"]
    end

    subgraph L5["Layer 5 — Judges + Signals (oracle)"]
        SIG["Signals L0 (deterministic)<br/>token / canary / regex / http-shape"]
        JDG["Judges L2 (LLM + heuristic)<br/>ipi_verdict, retrieval, strong_reject..."]
    end

    subgraph L6["Layer 6 — Storage"]
        DB["SQLite (Postgres-ready)<br/>Run / Trace / Finding / Signal"]
        FS["JSON traces + replay-bundle zips"]
    end

    subgraph L7["Layer 7 — Presentation"]
        DASH["Dashboard (FastAPI + HTMX + Mermaid)<br/>trees, findings, heatmap, HITL"]
        RPT["PDF / report export"]
    end

    subgraph LX["Cross-cutting"]
        PROV["Providers: anthropic / gemini / openai / vllm"]
        DEL["Delivery E2E: fission_e2e (deliver→wait→attack)"]
    end

    REG --> ORCH
    ORCH --> ATK
    ATK --> TGT
    WS --> UI
    MT --> UI
    TGT --> JDG
    ATK -. drives turn-by-turn .-> TGT
    JDG --- SIG
    ORCH --> DB
    ORCH --> FS
    DB --> DASH
    FS --> RPT
    ATK --> PROV
    JDG --> PROV
    DEL --> ORCH
```

---

## 2. Run lifecycle (the spine)

```mermaid
sequenceDiagram
    autonumber
    participant U as CLI / E2E
    participant O as Orchestrator
    participant A as Attack
    participant T as Target
    participant J as Judge + Signals
    participant S as Storage

    U->>O: run(config)
    O->>T: setup()
    O->>S: init Run row (in_progress)
    loop each (attack, behavior)
        O->>A: generate(behavior, target, budget)
        loop each attempt (attack drives target)
            A->>T: render_document() + invoke(prompt)
            T-->>A: Response (browser or API)
            A->>J: score_response()
            J-->>A: JudgeScore (+ confirm gate)
            A-->>O: yield AttackAttempt (precomputed response)
            O->>J: evaluate signals (L0)
            J-->>O: SignalRecords
            O->>S: persist Trace (+ verdict upgrade, finding)
        end
    end
    O->>T: close()
    O->>S: finalize Run (completed) + SummaryStats
    O-->>U: RunResult
```

---

## 3. tap_crescendo search (core attack)

```mermaid
flowchart TD
    SEED["seed(s): codename to steer grounding"] --> N0
    N0["n0 root<br/>Crescendo conversation (gradual multi-turn)"] -->|fail: deepen| D1
    N0 -->|success: stop, move on| DONE["seed done"]
    D1["depth-1 children (branching_factor)<br/>n1-0-1 may replay a seed_chain"] -->|prune keep_top| D2
    D2["depth-2 survivors..."] --> DONE
    subgraph perturn["inside each node"]
        H["heuristic judge<br/>escalation gradient"] --> G{"crossed threshold?"}
        G -->|yes| C["confirm_judge (LLM ipi_verdict)<br/>must AGREE"]
        C -->|confirmed| WIN["node = success"]
        C -->|rejected| H
    end
    HIST["history of ALL prior nodes<br/>fed to strategist"] -.-> D1
    HIST -.-> D2
```

---

## 4. Target production architecture (proposed control plane)

```mermaid
flowchart TD
    CI["Customer / CI:<br/>run this suite"] --> GW["API Gateway + Auth<br/>OIDC, RBAC, multi-tenant, quotas"]
    GW --> CP

    subgraph CP["Control plane"]
        RO["Run Orchestrator service"]
        SCH["Scheduler<br/>cron / on-model-release"]
        Q["Durable workflow + job queue<br/>Temporal / Cloud Workflows"]
        CS["Config service (schema-validated suites)"]
        RO --- Q
        SCH --- Q
        CS --- RO
    end

    Q --> AW["Attack workers (stateless, scale-out)"]
    Q --> JS["Judge service<br/>rate-limit + cache + cost budget + calibration"]

    AW --> TR
    subgraph TR["Target Runner service (isolated)"]
        API["API targets (preferred)<br/>model + Workspace APIs + agent sandboxes"]
        BF["Browser farm (fallback)<br/>headless Chrome in containers,<br/>1 per session, auto-heal, drift-detect"]
        VB["Per-tenant credential vault broker"]
        API --- VB
        BF --- VB
    end

    AW --> DP
    JS --> DP
    TR --> DP
    subgraph DP["Data plane"]
        PG["Postgres: Run / Trace / Finding"]
        OS["Object store: replay bundles, screenshots"]
        SEC["Secrets: Vault / KMS"]
        BUS["Event bus"]
    end

    DP --> OBS["Observability<br/>OTel traces, Prometheus, logs, SLOs, alerts"]
    DP --> WEB["Web app (multi-tenant)<br/>auth, run compare, trends, reports"]
```

---

## Layer index (what each is, current choice, key fork)

| # | Layer | Current choice | Key fork to decide |
|---|-------|----------------|--------------------|
| 1 | Spec / Config | YAML → Pydantic `RunConfig`, string registries | one-shot "run" vs first-class versioned **suite/target/schedule** objects |
| 2 | Orchestration | single-process asyncio, Grid/MCTS, in-memory | keep clean engine + wrap in **durable workflow** vs distributed rewrite |
| 3 | Attacks | plugins, async-iterator that **drives** the target | attacks as **drivers** (hold session) vs **planners** (emit attempts) |
| 4 | Targets | live **headed browser** vs api_mode export | **fidelity (browser farm)** vs **scale (API targets)** — or both tiers |
| 5 | Judges/Oracle | signals L0 + LLM judge L2 + confirm gate | how much to trust LLM judge; build a **calibration set** |
| 6 | Storage | SQLite + JSON + zips, no migrations | Postgres + object store + Alembic (low controversy) |
| 7 | Presentation | FastAPI + HTMX, single-user | add auth + multi-tenancy + trends; server-render is fine |
| X | Delivery/Pkg | UI email senders, 2 venvs, manual profiles | controlled **test-tenant fixtures** vs UI senders; one container |
