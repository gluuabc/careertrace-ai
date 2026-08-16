# Phase 7 portability validation

Validation date: 2026-08-15

## Result

Phase 7 passed. The implementation is ready for the Streamlit Community Cloud
deployment checkpoint.

## Automated evidence

- Full non-live regression: `252 passed, 17 skipped, 5 subtests passed`.
- Full configured live regression: `269 passed, 0 skipped, 5 subtests passed`.
- Clean Python 3.13 environment: requirements installed successfully and
  `pip check` reported no broken requirements.
- Fresh SQLite migration: empty database reached revision `20260815_16`.
- Existing SQLite migration: representative revision `20260803_04` reached
  revision `20260815_16` using SQLite-safe batch constraint changes.
- Fresh disposable Cockroach test database: application tables were reset only
  after verifying the database name contained `test`; Alembic reached revision
  `20260815_16`.
- Cockroach live suite: VECTOR(1024), full-text search, EXPLAIN, transaction
  retry, atomic source-budget reservation, and ownership predicates passed.
- CockroachDBSaver: setup, write, read, list, intermediate writes,
  fresh-connection recovery, real Profile interrupt/resume, and cleanup passed.
- True process restart: independent process B resumed process A's interrupted
  Profile workflow without SQLite, session state, module globals, or replayed
  extraction. Exactly one Profile version, its field revisions, and one document
  source relationship were stored.
- Live setup probes passed for AWS identity, Nova Lite, the configured Sonnet
  4.6 reasoning profile, Bedrock CountTokens, Titan 1024-dimensional embedding,
  S3 put/get/delete, and bounded Tavily discovery.
- Native Streamlit startup and `/_stcore/health` returned `ok`.

## User-verified execution surfaces

The following checks were run from the normal native execution surface because
the Codex macOS sandbox cannot register Chromium's Mach rendezvous service and
does not provide a container runtime:

- Native Playwright Chromium smoke: **PASS**.
- Docker image build: **PASS**.
- Docker container startup: **PASS**.
- Docker Streamlit health endpoint: **PASS** (`ok`).
- Docker used an automatically assigned host port (`52515`). Earlier attempts
  on `8501`/`8502` failed only because those host ports were already occupied.

## Configuration reconciliation

- Canonical reasoning generation model:
  `global.anthropic.claude-sonnet-4-6`.
- Direct CountTokens model:
  `anthropic.claude-sonnet-4-20250514-v1:0`; it is used only for token
  accounting and never for response generation.
- Public job-result maximum: `10`.
- Local checkpoint backend: SQLite.
- Deployed checkpoint backend: official CockroachDBSaver from
  `langchain-cockroachdb==0.2.1`.
- No private `.env`, secret value, generated evidence, recovery code, or
  disposable migration artifact is part of the deployment candidate.
