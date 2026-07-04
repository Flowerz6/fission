# Fission — Deployment & Orchestration

How to take the `advtest_ext` package from "runs locally via `poetry run advtest`"
to a **continuously-deployed, UI-mode red-team service** for Google Workspace Gemini.

> Diagram: `docs/fission_deployment.pdf` (source `docs/fission_deployment.dot`).
> Architecture rationale: `docs/FISSION_ARCHITECTURE_AND_PRODUCTION.md`,
> `docs/FISSION_MEASUREMENT_SPINE.md`. This doc is the *operational* companion.

---

## 1. The gap: local CLI → deployed service

Today the platform is a **single-process CLI app**. Five things must change before any
infra work matters — none is a rewrite (storage is already SQLAlchemy):

| Today | Becomes | Why |
|---|---|---|
| SQLite (`advtest.db`) | **Postgres** | Concurrent worker + dashboard writes; SQLite locks |
| Artifacts on local disk (`out/`, replay bundles, screenshots, intercepted JSONL) | **Object storage (GCS/S3)** | Workers are ephemeral; artifacts must outlive them |
| CLI-invoked runs | **Queue + worker model** | Continuous, parallel, scheduled execution |
| Dashboard, no auth | **Auth + access control** | Holds findings + adversarial content |
| Creds in env / `.env` | **Secrets manager** | Never bake creds into images |

---

## 2. Deployable topology (≈5 services)

1. **Dashboard / API** — the existing FastAPI app. Reads/writes Postgres, serves the UI and
   the `NEW / REGRESSED / FIXED` digest. Add auth.
2. **Browser workers** — Playwright + Chromium, one browser per Google account. The core of
   UI-mode; resource-heavy. Productionize the existing `session_pool` / `account_pool`.
3. **Scheduler / control plane** — *net-new*. Reads the coverage/freshness map, allocates
   browser-session budget across the regression-watch + frontier lanes, enqueues jobs on a
   cadence. This is what makes it "continuous" rather than "runs forever."
4. **Queue + broker** — Redis + Arq/Celery/RQ. Distributes jobs to workers.
5. **State (managed)** — Postgres (runs/traces/findings/coverage), object storage (artifacts),
   secrets manager (provider keys + Google creds).

External dependencies: **Google Workspace Gemini** (the target — via test tenants) and the
**attacker/judge LLM APIs** (Anthropic / Gemini).

---

## 3. The hard part: the browser farm (≈80% of the effort)

Because the engagement is **UI-mode only**, deployment *is* the browser farm:

- Each worker needs a **signed-in Google session** (persistent profile); Google's
  anti-automation fights cold launches ("unusual activity").
- **Account pool management**: N healthy accounts, cookie/session refresh, re-auth handling,
  rotation, per-account rate-limit awareness.
- **Harness-health monitoring** 24/7 to separate *attack-failed* vs *selector-drift* vs
  *account-flagged* vs *Gemini-changed*.

**Biggest lever = the Google asks, not engineering:** test tenants + allowlisted accounts +
(ideally) stable side-panel test hooks. Secure these *in writing before scaling the fleet* —
they decide whether the farm is tractable at a 2–3 person team size.

---

## 4. Staged rollout (sized for 2–3 engineers)

**Stage 1 — Make it deployable (no farm yet).** Containerize (Chromium in the image),
SQLite→Postgres, artifacts→object storage, dashboard auth, secrets manager. Deploy
dashboard + Postgres + one worker. Runs remotely against a few accounts. *Mostly plumbing.*

**Stage 2 — Queue + small worker fleet.** Redis + job queue; "a run" becomes "an enqueued job
a browser worker pulls." 3–5 workers, each its own account/profile. Extend CI → **CD**
(build image → push → deploy).

**Stage 3 — Scheduler / continuous loop.** Build the control-plane service (coverage map +
budget allocator) that enqueues regression-watch + frontier jobs on a cadence. *The net-new build.*

**Stage 4 — Observability + trust plane.** Ship `structlog` to a log aggregator; metrics
(sessions-per-finding, ASR trend, harness/account health) → dashboards + alerting; the
measurement-spine (ASR ± CI, frozen audit) as a scheduled job.

**Stage 5 — Governance hardening.** Encryption at rest (payloads + possibly PII in captured
sessions), retention policy, egress controls, scoped dashboard access.

**Where to run it:** GCP (Cloud SQL / GCS / Memorystore). **Resist Kubernetes at first** —
start with a small VM fleet for browser workers + a queue, managed Postgres/Redis/GCS, and the
dashboard on a small instance. Move to GKE only when fleet size justifies it. Chrome is
RAM-hungry (~hundreds of MB each) — size instances and cap per-host concurrency accordingly.

**Sequencing rule:** deployable single service → queue + a few workers → scheduler/continuous
loop → observability → governance. The Google test-tenant ask gates scaling the browser fleet.

---

## 5. Stage-1 punch list (get to a deployable containerized service)

Each is a small, independent branch. None blocks the others except where noted.

- [x] **`feat/postgres-storage`** — ✅ done (PR #11). DB URL was already parameterized
      (`ADVTEST_DB_URL`); added `psycopg[binary]` as the `postgres` extra; portable schema uses
      `Base.metadata.create_all` (Alembic deferred until the schema needs to *evolve* in prod —
      `create_all` is fine for a fresh deploy). A gated round-trip test proves Postgres
      compatibility:
      `ADVTEST_TEST_DB_URL=postgresql+psycopg://user:pw@host/db poetry run pytest tests/test_db_integration.py::test_storage_round_trip_on_postgres`
      (skips when unset, so CI/dev stay on SQLite).
- [x] **`feat/object-store-artifacts`** — ✅ done (PR #12). `storage/artifacts.py` adds an
      `ArtifactStore` interface with `LocalArtifactStore` + `GcsArtifactStore` and an
      env-driven factory (`ADVTEST_ARTIFACT_BACKEND=local|gcs`, `+_BUCKET`/`_PREFIX`).
      Replay bundles now write via the store (built in memory → put_bytes), returning a
      locator (local path or `gs://` URI); default local is behaviour-preserving. `gcs`
      optional extra (`google-cloud-storage`). *Follow-up: route screenshots + intercepted
      JSONL through the same store (they still write to local disk today).*
- [x] **`chore/dockerize`** — ✅ done (PR #15), **build-verified locally**. Slim `Dockerfile`
      (python:3.12-slim + Poetry 2.2.1 + `poetry install --only main --extras "postgres gcs"`,
      non-root, `uvicorn …:create_app --factory`) — *no browser; the Stage-2 worker image
      extends it with `playwright install --with-deps chromium`*. `docker-compose.yml` = Postgres
      + dashboard; `.dockerignore`. Verified: `docker compose up` → `/healthz` 200, `/readyz`
      `{"db":"ok"}` against the compose Postgres, `/runs` + `/references` 200.
      Run it: `docker compose up --build` → http://localhost:8000. *(Redis + worker join Stage 2/3.)*
- [x] **`feat/dashboard-auth`** — ✅ done (PR #14). Bearer-token gate on every route except
      the probes/static, via `create_app(auth_token=…)` or env `ADVTEST_DASHBOARD_TOKEN`
      (unset ⇒ open, dev/CI default); constant-time compare. For prod, prefer fronting with an
      identity-aware proxy (GCP IAP / Cloud Run auth) — the token is the built-in fallback so
      the dashboard is never anonymously exposed.
- [ ] **`chore/secrets-config`** — remove creds from `.env`-in-image; load provider keys +
      Google creds from a secrets manager (GCP Secret Manager) at runtime; document the env
      contract.
- [x] **`feat/healthcheck-endpoints`** — ✅ done (PR #13). Dashboard `/healthz` (liveness, no
      deps) + `/readyz` (readiness — runs `SELECT 1`, returns 503 when the DB is unreachable so
      a load balancer / k8s gate holds traffic). *Worker liveness heartbeat deferred to when
      the worker service exists (Stage 2/3).*
- [ ] **`chore/cd-pipeline`** — extend the GitHub Actions CI into CD: on merge to `main`, build
      + push the image (to Artifact Registry), deploy to a staging target.

**Deferred to later stages (do NOT start in Stage 1):** the scheduler/control-plane service,
the multi-worker queue fan-out, account-pool productionization, and the browser farm itself —
they depend on Stage 1 proving the app runs as a service, and on the Google infra asks.

---

## 6. Open dependencies / asks

- **Google:** dedicated test tenants, allowlisted accounts (relaxed abuse detection), raised
  limits, and — stretch — stable side-panel test hooks / accessibility IDs. *Gates Stage 2+.*
- **Labeling capacity** for the measurement spine's gold set (Stage 4).
- **Budget model:** deployment turns cost into a *run-rate* (farm compute + attacker/judge LLM
  spend + human review). Optimize cost-per-confirmed-novel-finding, not cost-per-attempt.
