# Operation ID Plan: content_service

Canonical registry of `operation_id` values for every route in
`content_service`. These IDs feed the org-wide n8n custom-node generator
and the `ultrareview` cross-service catalogue, so they must be:

- **stable** — never rename without coordinating downstream,
- **`verb_noun` snake_case** — matches the org standard (nlp_service,
  worker_management, kag, analysis_service, video_compiler_service,
  video_analyzer_service),
- **globally unique** — disambiguated against the rest of the fleet's verbs.

This service hosts two API domains (narrative + scripts) plus a health
surface. Generic verbs (`generate_*`, `get_*_status`, `cancel_*`,
`scaffold_*`) are prefixed with the domain noun (`script_` /
`storyline` / `narration` / etc.) where collision is plausible across
the fleet.

## Files to edit

- `src/asgi.py` (root endpoint)
- `src/api/health.py`
- `src/api/narrative_routes.py`
- `src/api/scripts_routes.py`

## Mapping Tables

### `src/asgi.py` (tag: `health`)

| Method | Function | operation_id   | Rationale                                  |
|--------|----------|----------------|--------------------------------------------|
| GET    | `root`   | `service_root` | Service banner; matches the fleet standard. |

### `src/api/health.py` (tag: `health`)

| Method | Function       | operation_id   | Rationale                                                  |
|--------|----------------|----------------|------------------------------------------------------------|
| GET    | `health_check` | `health_check` | Primary health probe; matches the fleet standard verb.     |
| GET    | `health_live`  | `health_live`  | Liveness sub-probe; one-to-one with path.                  |
| GET    | `health_ready` | `health_ready` | Readiness sub-probe; one-to-one with path.                 |

### `src/api/narrative_routes.py` (tag: `narrative`)

| Method | Function                  | operation_id                       | Rationale                                                                                          |
|--------|---------------------------|------------------------------------|----------------------------------------------------------------------------------------------------|
| POST   | `start_storyline`         | `start_storyline`                  | Verb_noun matches function; storyline is the domain noun.                                          |
| GET    | `get_storyline`           | `get_storyline_job`                | Disambiguate from "get the storyline payload" — this returns the async job envelope.               |
| POST   | `build_shotlist`          | `build_shotlist`                   | Verb_noun matches function.                                                                        |
| POST   | `extract_narrative`       | `extract_narrative`                | Verb_noun matches function.                                                                        |
| POST   | `plan_storyline`          | `plan_storyline`                   | Verb_noun matches function.                                                                        |
| POST   | `start_narration`         | `start_narration`                  | Verb_noun matches function.                                                                        |
| GET    | `get_narration`           | `get_narration_job`                | Same disambiguation as storyline — async job envelope, not the script.                             |
| POST   | `start_narration_shotlist`| `start_narration_for_shotlist`     | More-readable verb_noun than the function name; binds narration to the shotlist explicitly.        |
| POST   | `enhance_shotlist`        | `enhance_shotlist`                 | Verb_noun matches function.                                                                        |
| POST   | `storyline_from_findings` | `convert_findings_to_storyline`    | Function name reads as "noun_from_noun"; verb_noun form is `convert_findings_to_storyline`.        |
| POST   | `shotlist_from_findings`  | `convert_findings_to_shotlist`     | Same — `convert_findings_to_shotlist` clarifies it is a conversion operation, not a fetch.         |

### `src/api/scripts_routes.py` (tag: `scripts`)

| Method | Function               | operation_id                | Rationale                                                                                              |
|--------|------------------------|-----------------------------|--------------------------------------------------------------------------------------------------------|
| POST   | `generate_script`      | `generate_script`           | Verb_noun matches function; `script` already domain-prefixes vs. generic `generate_*` in the fleet.    |
| POST   | `generate_batch`       | `generate_script_batch`     | `generate_batch` is too generic across services; pin to the script domain.                             |
| POST   | `generate_campaign_pack` | `generate_campaign_pack`  | Verb_noun matches function; `campaign_pack` is unique to this service.                                 |
| GET    | `get_job_status`       | `get_script_job_status`     | Domain-prefix to disambiguate from analysis/narration job-status verbs elsewhere in the fleet.         |
| POST   | `validate_script`      | `validate_script`           | Verb_noun matches function.                                                                            |
| POST   | `scaffold_project`     | `scaffold_script_project`   | Pin to the script domain — `scaffold_*` could collide with other domains' bootstrap verbs.             |
| DELETE | `cancel_job`           | `cancel_script_job`         | Domain-prefix — `cancel_job` collides across analysis/narration/scripts.                               |
| GET    | `get_batch_status`     | `get_script_batch_status`   | Domain-prefix — keeps `*_batch_status` reads unambiguous when n8n nodes list across services.          |

## Verification

```bash
cd content_service
uv run pytest tests/test_openapi.py -x
```

`tests/test_openapi.py` loads `app.openapi()` and asserts that the set of
`operationId` values equals the union of every right-hand column above.
Drift (rename, missed decorator, fallback) fails the test loudly.

## Disambiguation rationale

Concrete renames where the function name was generic enough to collide
across the fleet or within this service:

- `get_storyline` → `get_storyline_job` and `get_narration` →
  `get_narration_job` — function name suggests "get the artefact"; the
  endpoint actually returns an async job status envelope, so the op_id
  describes what the client sees.
- `start_narration_shotlist` → `start_narration_for_shotlist` — more
  readable verb_noun.
- `storyline_from_findings` / `shotlist_from_findings` →
  `convert_findings_to_storyline` / `convert_findings_to_shotlist` — function
  names lack a verb; clients need `convert_*` to know this is a sync
  transformation, not a fetch.
- `generate_batch` / `get_job_status` / `scaffold_project` / `cancel_job` /
  `get_batch_status` → all domain-prefixed with `script_` so they don't
  collide with similarly-named verbs in `video_analyzer_service`,
  `narrative` jobs in this same service, or other batch-shaped services.

## Future routes

When adding a route in this service, **always** set `operation_id="…"`
explicitly. The `generate_unique_id_function` configured in `asgi.py` is a
safety net for forgotten kwargs, never the primary identifier. New IDs go
in the table above as part of the same PR (no out-of-band updates).
