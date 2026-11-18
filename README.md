# Content Service

Unified content generation microservice — storyline, narration, shot lists, and Playwright test scripts. Port 6305.

## What Content Service Does

Content Service is the merge of two former w2v services, `narrative_service` and `script_generator_service`, into a single FastAPI application with two route groups. Upstream, the `w2v_orchestrator` drives every request: its course, lesson, `demo_video`, and promo pipeline stages call in whenever a run needs a storyline planned, a shot list built, narration written, or a Playwright recording script generated. Callers authenticate with a per-tenant license token and receive job IDs they poll for completion, since most generation work runs asynchronously in the background.

The narrative side turns source material into video-production artifacts. It plans a storyline against a target duration, breaks it into chapters, extracts a narrative from scraped findings, builds a shot list (with optional AI creative enhancement), and produces TTS-ready narration segments. Narration additionally reaches out to the `tts_service` mesh endpoint to estimate and align speaking duration. The script side generates TypeScript Playwright test scripts — single, batch, or full campaign-pack — validates them by TypeScript compilation and a headless dry run, auto-fixes failures across a bounded number of improvement passes, and can scaffold a runnable Playwright project around the generated code.

Every model call is routed through `worker_management` via `POST /workers/{name}/message` using the `WMClient` adapter in `src/llm/wm_provider.py`; the service never talks to OpenAI, Anthropic, or the inference router directly. This keeps cost attribution per-tenant and lets providers be swapped server-side. The only LLM-shaped knobs the service still owns are per-call timeouts (`narrative_llm_timeout_seconds`, `script_llm_timeout`); provider, model, and API keys all live in `worker_management`.

The service is stateful, backed by PostgreSQL 18 (psycopg 3.x — no asyncpg, no SQLite) with an Alembic-managed schema and row-level security. RLS is ENABLE-not-FORCE: migration `003_rls` is inert until the runtime connects as the restricted non-owner role `w2v_content_app` with `TENANT_RLS_MODE=enforce`, at which point every query is tenant-scoped. Redis provides caching and per-job locks (optional — the app degrades gracefully if Redis is unavailable), and MinIO holds generated artifacts in the `content-service` bucket. A periodic stale-job reconciler, backed by a `SECURITY DEFINER` SQL function, sweeps rows wedged at `processing` after a worker crash or OOM and fails them out so the pipeline never hangs indefinitely.

## Features

- **Storyline generation and chapter planning** — plans a duration-bounded storyline and splits it into chapters so downstream video stages have a structured spine to render against
- **Shot list building with AI enhancement** — converts a storyline into browser-recording parts and title cards, optionally enriched by an LLM creative pass, so the recording harness knows exactly what to capture
- **Narration generation** — writes TTS-ready narration segments with word-target and duration-safety-margin math, aligned against the `tts_service` endpoint, so narrated audio fits each video segment
- **Findings-to-storyline conversion** — turns scraped/analyzed findings directly into a storyline or shot list so research output feeds the production pipeline without a manual authoring step
- **Playwright script generation** — produces single, batch, and campaign-pack TypeScript Playwright test scripts so app demos can be recorded automatically
- **Validation and auto-fix** — compiles generated scripts with `tsc`, runs a headless dry run, and iterates improvement passes so only working scripts reach consumers
- **Project scaffolding** — generates a runnable Playwright project (with optional `npm install`) around a script so it can be executed standalone
- **WM-gated LLM access** — every LLM call flows through `worker_management` so cost is attributed per tenant and the underlying provider/model can change without touching this service
- **Tenant isolation via RLS** — Postgres row-level security under the restricted `w2v_content_app` role guarantees a tenant can never read another tenant's jobs
- **Stale-job reconciler** — a periodic `SECURITY DEFINER` sweep fails out `processing` rows abandoned by a crashed worker so jobs never wedge forever
- **Graceful background-task drain** — narrative fire-and-forget tasks and the script `TaskManager` are drained on shutdown so a rollout mid-job does not orphan in-flight work
- **Health probes** — standard liveness, readiness, and startup endpoints for Kubernetes orchestration

## Authentication

Auth is enabled by default (`AUTH_ENABLED=true`) and delegated to `w2v_common.auth.middleware.AuthMiddleware`. Inbound requests carry the license token as `X-Tenant-Id: Bearer <license_token>` or — since w2v_common 0.74.0 — as `Authorization: Bearer <opaque_token>`, the only shape the platform's `license-authz` ext-authz gate can read. `X-Tenant-Id` wins when both are present. A **JWT-shaped** `Authorization: Bearer` is still never a license token; it goes to the Keycloak JWT lane. The middleware introspects the token against the licenses service and caches results for 5 minutes (`CACHE_LICENSE_TTL`, default `300`). For service-to-service calls, this service forwards `X-Tenant-Id`, `X-License-Id`, and `X-Subject-Id` as plain headers (no `Bearer` prefix).

| Priority | Method | Details |
|----------|--------|---------|
| 1 | License token | Introspection of `X-Tenant-Id: Bearer <token>` (preferred) or an opaque `Authorization: Bearer <token>` against the licenses service, cached 5 min |
| 2 | Trusted network | Bypass for private RFC 1918 source IPs (`127.0.0.1/32`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) |

Public paths are exempt from auth: `/`, `/health`, `/health/live`, `/health/ready`, `/healthz`, `/readyz`, `/ready`, `/docs`, `/redoc`, `/openapi.json`, `/favicon.ico`, `/metrics`, plus anything under the `/docs`, `/redoc`, and `/health/` prefixes.

RLS is enforced at the database tier: when the app connects as the restricted `w2v_content_app` role with `TENANT_RLS_MODE=enforce`, the current tenant GUC is re-bound on every transaction (`src/database/rls_events.py`) so all rows are tenant-scoped in depth.

## Quick Start

```bash
uv sync
uv run uvicorn src.asgi:app --host 0.0.0.0 --port 6305
uv run pytest tests/ -v --cov=src
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```

## API Endpoints

### Narrative `/api`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/storyline` | Start storyline generation (returns a job) |
| GET | `/api/storyline/{job_id}` | Get storyline job status/result |
| POST | `/api/storyline/plan` | Plan chapters for a storyline |
| POST | `/api/storyline/from-findings` | Convert scraped findings into a storyline |
| POST | `/api/shotlist` | Build a shot list |
| POST | `/api/shotlist/enhance` | AI-enhance an existing shot list |
| POST | `/api/shotlist/from-findings` | Convert findings into a shot list |
| POST | `/api/narrative/extract` | Extract a narrative from source material |
| POST | `/api/narration` | Start narration generation (returns a job) |
| GET | `/api/narration/{job_id}` | Get narration job status/result |
| POST | `/api/narration/shotlist` | Generate narration for a shot list |

### Scripts `/api/scripts`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/generate` | Generate a single Playwright script |
| POST | `/generate/batch` | Generate a batch of scripts |
| POST | `/generate/campaign-pack` | Generate a full campaign pack |
| GET | `/jobs/{job_id}` | Get script job status/result |
| POST | `/jobs/{job_id}/validate` | Validate (and auto-fix) a script job |
| POST | `/jobs/{job_id}/scaffold` | Scaffold a runnable project for a job |
| DELETE | `/jobs/{job_id}` | Cancel a script job |
| GET | `/batches/{batch_id}` | Get batch status/result |

### Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Service info and endpoint listing |
| GET | `/health` | Health check |
| GET | `/health/live` | Liveness probe |
| GET | `/health/ready` | Readiness probe (checks DB) |
| GET | `/healthz` | Liveness alias |
| GET | `/readyz` | Readiness alias |

## Configuration

All configuration via environment variables. `API_PORT=8000` is rejected at settings validation (reserved for the user's local mother service).

| Variable | Default | Description |
|----------|---------|-------------|
| `API_HOST` | `0.0.0.0` | Listen host |
| `API_PORT` | `6305` | Listen port |
| `API_DEBUG` | `false` | Enable /docs and /redoc detail |
| `API_RELOAD` | `false` | Auto-reload on code change (dev) |
| `AUTH_ENABLED` | `true` | Toggle license-token auth middleware |
| `CACHE_LICENSE_TTL` | `300` | License introspection cache TTL (seconds) |
| `TENANT_RLS_MODE` | `disabled` | `enforce` activates Postgres RLS under `w2v_content_app` |
| `LOG_LEVEL` | `INFO` | Log level |
| `LOG_JSON_LOGS` | `false` | Structured JSON logging |
| `DATABASE_URL` | — (required) | PostgreSQL DSN (psycopg 3.x; no localhost fallback) |
| `DATABASE_POOL_SIZE` | `10` | Connection pool size |
| `DATABASE_MAX_OVERFLOW` | `20` | Max connections beyond pool size |
| `DATABASE_POOL_TIMEOUT` | `30` | Pool checkout timeout (seconds) |
| `REDIS_URL` | — (required) | Redis DSN for cache + job locks (no localhost fallback) |
| `REDIS_MAX_CONNECTIONS` | `10` | Max Redis connections |
| `STORAGE_BACKEND` | `local` | `local` or `minio` |
| `STORAGE_LOCAL_PATH` | `/app/data` | Local artifact path |
| `STORAGE_MINIO_BUCKET` | `content-service` | MinIO bucket for artifacts |
| `NARRATIVE_LLM_TIMEOUT_SECONDS` | `90` | Per-call timeout for narrative WM calls |
| `SCRIPT_LLM_TIMEOUT` | `120.0` | Per-call timeout for script WM calls |
| `NARRATION_ENABLED` | `true` | Enable narration generation |
| `NARRATION_TTS_PROVIDER` | `kokoro` | TTS provider |
| `NARRATION_VOICE` | `af_heart` | TTS voice identifier |
| `NARRATION_WORDS_PER_MINUTE` | `150` | Speaking rate for duration estimation |
| `STORYLINE_TARGET_DURATION_MINUTES` | `3.0` | Target video duration (0 = auto) |
| `SHOTLIST_AI_CREATIVE` | `true` | Enable AI creative shot-list enhancement |
| `GENERATOR_PARALLEL_WORKERS` | `8` | Concurrent script generation workers |
| `GENERATOR_MODE` | `webapp_commercial` | Script generation mode |
| `VALIDATION_TS_COMPILE_TIMEOUT` | `60` | TypeScript compile timeout (seconds) |
| `SCAFFOLD_NPM_INSTALL` | `true` | Run `npm install` during scaffold |
| `RECONCILER_ENABLED` | `true` | Enable the periodic stale-job reconciler |
| `RECONCILER_INTERVAL_SECONDS` | `300` | Seconds between reconciler sweeps |
| `RECONCILER_STALE_AFTER_SECONDS` | `1800` | A `processing` job older than this is failed out |

## Architecture

```
src/
├── asgi.py                          # FastAPI app, lifespan, WM script-LLM factory, health, OpenAPI
├── config.py                        # Pydantic settings (extends w2v_common; port 6305, rejects 8000)
├── logging_config.py                # Structured logging setup
├── api/
│   ├── health.py                    # /health, /health/live, /health/ready, /healthz, /readyz
│   ├── narrative_routes.py          # Narrative endpoints (/api/storyline, /shotlist, /narration, ...)
│   ├── narrative_schemas.py         # Narrative request/response DTOs
│   ├── narrative_types.py           # Narrative type definitions
│   ├── scripts_routes.py            # Script endpoints (/api/scripts/generate, /jobs, /batches)
│   ├── scripts_schemas.py           # Script request/response DTOs
│   └── dependencies.py              # FastAPI dependency wiring
├── auth/
│   ├── middleware.py                # Re-exports w2v_common AuthMiddleware + public/trusted sets
│   └── models.py                    # Auth model shims
├── llm/
│   ├── wm_provider.py               # WMLLMProvider — routes LLM calls through worker_management
│   └── types.py                     # LLMRequest / LLMResponse protocol types
├── services/
│   ├── reconciler.py                # StaleJobReconciler loop + SECURITY DEFINER DDL source
│   ├── narrative/
│   │   ├── storyline_generator.py   # Storyline generation
│   │   ├── chapter_planner.py       # Chapter planning
│   │   ├── narrative_extractor.py   # Narrative extraction from source
│   │   ├── narration_builder.py     # Narration segment generation
│   │   ├── shotlist_builder.py      # Shot list construction
│   │   ├── shotlist_enhancer.py     # AI creative enhancement
│   │   ├── findings_converter.py    # Findings → storyline/shotlist
│   │   ├── tts_client.py            # tts_service mesh client
│   │   └── llm_retry.py             # LLM retry helper
│   └── scripts/
│       ├── orchestrator.py          # Script job orchestration
│       ├── generation_service.py    # Script generation
│       ├── improvement_service.py   # Auto-fix / improvement passes
│       ├── validation_service.py    # tsc compile + headless dry run
│       ├── scaffold_service.py      # Runnable project scaffolding
│       ├── campaign_pack_service.py # Campaign-pack generation
│       ├── prompt_builder.py        # Prompt template assembly
│       ├── storage_service.py       # Artifact storage
│       └── script_safety.py         # Generated-script safety checks
├── repository/
│   ├── models.py                    # SQLAlchemy ORM models (jobs, batches, usage)
│   ├── job_repository.py            # Script job persistence
│   ├── batch_repository.py          # Batch persistence
│   └── usage_repository.py          # Usage/metering persistence
├── database/
│   ├── connection.py                # Engine creation with retry
│   ├── session.py                   # Global env-driven session maker
│   ├── models.py                    # Declarative base
│   ├── rls_events.py                # Per-transaction tenant GUC re-bind (RLS enforce)
│   └── migrations/versions/         # Alembic migrations 001–005 (initial, artifact_id, RLS, reconcile, index)
├── cache/
│   ├── cache_keys.py                # Tenant-scoped cache key builders
│   └── job_lock.py                  # Redis-backed per-job locks
├── infra/
│   ├── redis_client.py              # Redis client wrapper
│   └── task_manager.py              # Bounded async task manager for script jobs
├── middleware/
│   └── request_id.py                # Request ID ContextVar middleware (outermost)
├── domain/                          # Domain models (job, batch, script, generation, usage)
├── scaffold/ · scaffold_mobile/     # Playwright project scaffold assets
└── templates/                       # Prompt/script templates
```

## Testing

```bash
uv run pytest tests/ -v --cov=src          # All tests with coverage
uv run pytest tests/unit/ -v               # Unit tests
uv run pytest tests/ -m integration -v     # Integration tests (RLS isolation, reconciler DB)
uv run ruff check src/ tests/              # Lint
uv run ruff format --check src/ tests/     # Format check
```

Coverage threshold: 80% (`fail_under = 80` in pyproject.toml). Integration tests (`test_rls_isolation.py`, `test_reconciler_db.py`) exercise a real PostgreSQL CI database. Run `./scripts/ci-local.sh` before pushing — it mirrors the CI lint/unit job (ruff, ruff format, ty, pytest+coverage, detect-secrets, pip-audit, bandit) verbatim.

## CI/CD

`.github/workflows/ci-public.yml` is self-contained and runs on GitHub-hosted runners: a betterleaks secret scan, the `no-private-refs` guard, then `ruff check`, `ruff format --check`, `pytest` against a Postgres 18 service container, and `scripts/pip-audit.sh`. No image is published from this repository.


Per-PR the archetype runs lint + ruff format + ty + pytest (with coverage) + bandit + pip-audit + detect-secrets, builds an amd64 image, deploys it to a per-commit `test-<sha>` namespace, and runs `kubectl rollout status` to confirm the pod reaches Ready. On merge to main the two-tag promote runs `:sha-<commit>` → in-cluster smoke → `:latest`, and Flux rolls out the new digest. End-to-end smoke lives in `w2v_orchestrator`'s post-promote workflow, not here.

Image: `registry.test:5000/content_service:sha-<commit>`, promoted to `:latest` on success.

## Docker

```bash
docker build -t content-service .
docker run -p 6305:6305 content-service
docker run -p 6305:6305 -e LOG_LEVEL=DEBUG -e AUTH_ENABLED=false \
  -e DATABASE_URL=postgresql+psycopg://host/content -e REDIS_URL=redis://host:6379/0 content-service
```

A `docker compose up -d` path is also available for local runs (`curl http://localhost:6305/health`).

## Dependencies

- **worker_management** — sole LLM gateway; all model calls go through `POST /workers/{name}/message` via `WMClient`
- **w2v_orchestrator** — upstream caller; course, lesson, `demo_video`, and promo stages consume storyline, narration, shot lists, and scripts
- **tts_service** — narration aligns speaking duration against the TTS mesh endpoint
- **licenses service** — validates inbound license tokens via introspection
- **PostgreSQL 18** — persistent storage via PgBouncer (`pgbouncer.pgbouncer:6432`); psycopg 3.x driver, Alembic-managed schema, RLS-enforced
- **Redis** — cache and per-job locks (optional; app degrades gracefully if absent)
- **MinIO** — artifact storage in the `content-service` bucket
- **FastAPI** — web framework (`fastapi[all]`)
- **Uvicorn** — ASGI server
- **Pydantic v2 / pydantic-settings** — request/response and settings validation
- **SQLAlchemy 2.x (asyncio)** — async ORM
- **psycopg 3.x** — PostgreSQL driver
- **Alembic** — database migrations
- **redis** — Redis client
- **httpx** — async HTTP client for WM/TTS calls
- **Playwright** — script validation dry runs and scaffold generation
- **Jinja2** — prompt/script templating
- **prometheus-client** — metrics
- **w2v-common** — shared config, auth middleware, clients, and error envelope

## Known Issues

- **Stale "Offline LLM Stub" documentation** — the previous README advertised an `LLM_PROVIDER=stub` deterministic offline provider. No such toggle exists in the current code: LLM provider config is intentionally absent and every call routes through `worker_management`. In CI and unit tests the LLM provider is mocked at the test boundary (patching `_create_script_llm_provider` / the WM client), not via an environment variable.
</content>
</invoke>

## License

Apache 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

## Licensing

This service contains no licence enforcement of its own and runs no licence
server. Tenant and licence identity is forwarded from the caller. It runs with
no licence switch set; unset means off.
