# Phase 4 — Release & Ops

> Make the project releasable and operable: a tagged version maps to a built image, the
> image deploys by runbook, secrets and data have a home, and a running instance can be
> observed and rolled back. The build/deploy scaffolding exists; this phase turns it into
> a process.

**Objective:** cut a versioned release that builds, deploys, and rolls back predictably,
with the operational contract written down.

**Exit gate:** a git tag produces a released image; a runbook takes it from tag to running
service and back; secrets, database, and artifact storage are documented and provisioned.

## What already holds (keep it)

- `Dockerfile` + `docker-compose.yml` define the app image and local stack.
- `cd.yml` builds and pushes to GHCR on merge to `main`, with a gated Cloud Run deploy.
- `.env.example` documents the runtime env contract.
- Storage is pluggable: SQLite → Postgres via `ADVTEST_DB_URL` (`postgres` extra); local →
  GCS artifact store via `ADVTEST_ARTIFACT_BACKEND` (`gcs` extra).
- Health probes exist (`/healthz`, `/readyz` per the commit history).

## Tasks

| # | Task | Where | What | Effort |
|---|---|---|---|---|
| 4.1 | Versioning scheme (F9) | `pyproject.toml`, tags | Adopt SemVer. Decide the source of truth (manual bump vs `poetry version`) and tie the git tag to it. Stamp the version + commit SHA into the app so a running instance reports what it is (surface it on `/healthz` or the dashboard footer). | M |
| 4.2 | Release automation | `.github/workflows/` | On tag push (`v*`), have CD tag the GHCR image with the release version (not just `latest`/SHA), generate the `CHANGELOG.md` section, and create a GitHub Release. Build once, promote the same digest — never rebuild for prod. | M |
| 4.3 | Deploy runbook | `docs/production/` or `docs/FISSION_DEPLOYMENT.md` | Extend the existing deployment doc into a step-by-step runbook: provision → set secrets → deploy tag → verify `/readyz` → smoke test → rollback. Include the exact `--set-secrets` mapping the `cd.yml` deploy job references. | M |
| 4.4 | Database migrations | `advtest_ext/storage/` | The schema is created via SQLAlchemy `create_all`, which does not handle upgrades. Add Alembic before the first Postgres production write, so schema changes are versioned and reversible. (Roadmap task 3.5 also calls for this.) | L |
| 4.5 | Secrets management | deploy, `.env.example` | Document that prod secrets come from a manager (GCP Secret Manager per `cd.yml`), never from a committed `.env`. Add a startup check that fails fast and loud on a missing required secret (`ANTHROPIC_API_KEY`, `ADVTEST_DB_URL`). | S |
| 4.6 | Observability | `advtest_ext/`, deploy | Structured logs already use `structlog`. Add request/run metrics and error tracking for the dashboard service (OTel or Prometheus per roadmap 3.5; at minimum, ship logs to a durable sink). Define the handful of SLIs that matter (run success rate, oracle latency, error rate). | L |
| 4.7 | Backup & retention for real data | ops | Define backup cadence and retention for the production DB and artifact store, and a restore test. Distinct from the ad-hoc `backups/*.db` files in the dev tree — this is the operational policy. Feeds Phase 5's retention rules. | M |
| 4.8 | Container hardening | `Dockerfile` | Review for production: non-root user, pinned base image digest, minimal layers, no dev deps or secrets baked in, `.dockerignore` excludes `out/`, `*.db`, `seed_data/`. | S |

## Sequencing

4.1 → 4.2 → 4.3 is the release spine and comes first — it delivers the exit gate. 4.4
(Alembic) is a hard prerequisite for any real Postgres deployment and should land before the
first prod write, so schedule it in parallel with the release spine. 4.5 and 4.8 are quick
hardening. 4.6 and 4.7 are the larger ops investments; do them before, not after, going
continuously live.

## Guardrails

- **Build once, promote the digest.** Never rebuild an image for production that differs from
  what was tested. 4.2 must promote the exact digest CI built.
- **No `create_all` in production once 4.4 lands.** Mixing `create_all` and Alembic corrupts
  migration state. Switch cleanly.
- **`.dockerignore` is a leak boundary.** Confirm 4.8 excludes every data/artifact/seed path
  before the first external image push, or sensitive content ships inside the image.

## Definition of done

- [ ] A `v*` tag produces a versioned GHCR image and a GitHub Release with changelog notes.
- [ ] A running instance reports its version + commit SHA.
- [ ] The deploy runbook takes a tag to a verified running service and back to the prior one.
- [ ] Alembic manages the schema; `create_all` is retired for prod.
- [ ] Startup fails fast on missing required secrets.
- [ ] Backups run on a cadence and a restore has been tested.
- [ ] The image is non-root, pinned, and carries no data/secrets.
