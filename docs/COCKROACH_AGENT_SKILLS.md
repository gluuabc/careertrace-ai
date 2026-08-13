# CockroachDB Agent Skills Audit

Audited 2026-08-13 against the official
[`cockroachlabs/cockroachdb-skills`](https://github.com/cockroachlabs/cockroachdb-skills)
repository at commit `e14e86d23ce8ee2e7e40a34ce2944c2502b6eadd`. The upstream
repository contains an Apache-2.0 `LICENSE`.

The Skills were installed only in the developer-local `.agents/skills` cache.
They are database engineering and operational guidance, not CareerTrace user
features. They are therefore separate from `app/skills`, are not in
`CAREER_AGENT_TOOLS`, and their downloaded third-party files are not committed.

| Skill | Official upstream path | Applied to | Finding | Implementation change | Proof | Status |
|---|---|---|---|---|---|---|
| `designing-application-transactions` | `skills/cockroachdb-application-development/designing-application-transactions/SKILL.md` | SQLAlchemy sessions, search source budgets, provider boundaries | Provider calls were already outside reservation transactions, but retryable Cockroach serializable conflicts lacked a bounded reusable retry boundary. | Added `run_retryable_transaction`; budget reservation is a short database-only callback using the official SQLAlchemy Cockroach retry runner. | `test_cockroach_transaction_retry_is_bounded_and_idempotent`; `test_concurrent_source_budget_cannot_overspend`; `test_provider_network_call_not_inside_search_budget_transaction`; live retry/atomic tests | APPLIED — PROVEN BY TEST |
| `cockroachdb-sql` | `skills/cockroachdb-query-and-schema-design/cockroachdb-sql/SKILL.md` | Retrieval migrations, vector/FTS SQL, ownership/session scoping, EXPLAIN | The expression FTS index was not the documented Cockroach shape; important visibility predicates lacked a composite index. | Added a stored `TSVECTOR` computed column, inverted FTS index, composite visibility index, and repository queries against the computed column. Revision 11 repairs already-migrated databases. | `test_cockroach_retrieval_sql_has_deterministic_user_and_session_scope`; `test_cockroach_vector_and_fts_schema_expected_shape`; live migration/vector/FTS/EXPLAIN tests | APPLIED — PROVEN BY TEST |
| `triaging-live-sql-activity` | `skills/cockroachdb-observability-and-diagnostics/triaging-live-sql-activity/SKILL.md` | Developer Managed MCP policy | Arbitrary read SQL and running-query text could expose private application data; output limits were not enforced. | Removed direct running-query capability; limited SQL to approved system metadata relations, one statement, 10,000 characters, and 100 rows. | `test_managed_mcp_config_discovery_and_read_policy` | APPLIED — PROVEN BY TEST |
| `profiling-statement-fingerprints` | `skills/cockroachdb-observability-and-diagnostics/profiling-statement-fingerprints/SKILL.md` | Historical retrieval SQL profiling procedure | No runtime profiling should enter the Career Agent or persist private SQL. Safe analysis needs redacted activity permission, time filters, previews, and limits. | Documented the bounded developer procedure below; no runtime telemetry table or Career Agent tool added. | MCP exclusion/read-policy tests | APPLIED — NO CODE CHANGE REQUIRED |
| `hardening-user-privileges` | `skills/cockroachdb-security-and-governance/hardening-user-privileges/SKILL.md` | Runtime, migration, MCP, and end-user identity separation | One broad identity should not serve all four trust domains. | Documented least-privilege roles and retained MCP/Career Agent separation; no cloud grants were mutated automatically. | MCP tool-surface and write-rejection tests | APPLIED — PROVEN BY TEST |

## Transaction conclusions

- `session_scope` remains the ordinary short transaction boundary.
- `reserve_search_source_calls` uses `SELECT ... FOR UPDATE` plus bounded
  Cockroach retry handling. Its callback performs only deterministic SQL.
- Tavily, Bedrock, Titan, Amazon Rerank, HTTP, Playwright, and MCP calls occur
  after the reservation transaction has returned. They are never replayed by a
  database retry.
- Retrieval embeddings and remote fetches are computed before or after the
  short repository writes; network work is not placed inside SQL transactions.

## Safe live diagnostics and profiling

The developer Managed MCP identity should receive `VIEWACTIVITYREDACTED`, not
`VIEWACTIVITY`, and no cancellation privileges. Diagnostics should use only
production-approved system metadata, a fixed time window, truncated query
previews, and `LIMIT <= 100`. Historical profiling uses
`crdb_internal.statement_statistics` to review mean/max latency, full-scan
flags, index recommendations, rows read, sampled CPU/contention, failures, and
plan-hash changes for the `careertrace` application/database. It must not copy
raw SQL text into CareerTrace application tables.

The current live test identity may be inspected with read-only `SHOW GRANTS`
queries. CareerTrace does not automatically grant or revoke permissions.

## Least-privilege recommendation

1. **Runtime identity:** `CONNECT`, schema `USAGE`, and only the table DML needed
   by CareerTrace. No DDL, admin, activity, cancellation, or MCP privileges.
2. **Migration identity:** separate deployment credential with the schema/DDL
   permissions required by Alembic; not used by the running application.
3. **Developer MCP identity:** read-only metadata plus
   `VIEWACTIVITYREDACTED`; no application DML and no cancellation privileges.
4. **End-user Career Agent:** receives only `CAREER_AGENT_TOOLS`; never SQL,
   Cockroach Cloud MCP, database credentials, or operational Skill content.

## Live validation record

- The dedicated Cockroach test connection was confirmed by the user before the
  audit. During this audit, the first migration run exposed and fixed an Alembic
  percent-encoded URL interpolation defect.
- Live Tavily, Titan (1024 dimensions), and Bedrock CountTokens checks completed.
- Cockroach migration/vector/FTS/EXPLAIN/retry/atomicity tests are maintained in
  `tests/test_cockroach_integration.py` and run only when
  `COCKROACH_TEST_DATABASE_URL` is configured.
- Cockroach Cloud MCP live validation remains separate and requires a complete
  managed MCP endpoint/cluster/service-account configuration.
