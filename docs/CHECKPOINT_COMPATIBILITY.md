# LangGraph checkpoint compatibility

CareerTrace uses one Profile graph with an explicit persistence backend:

- local development: `SqliteSaver`
- deployed CockroachDB: `CockroachDBSaver` from `langchain-cockroachdb==0.2.1`

The generic `langgraph-checkpoint-postgres` saver was evaluated first. Its
schema setup completed on the disposable CockroachDB integration database, but
its first checkpoint read failed with:

```text
psycopg.errors.UndefinedTable: no data source matches prefix:
jsonb_each_text in this context
```

That saver is therefore a rejected compatibility candidate. CareerTrace does
not patch its SQL, vendor it, or include it as a runtime dependency.

The Cockroach-specific saver passed setup, checkpoint write/read/list,
intermediate writes, a fresh-connection read, real graph compilation, the
Profile confirmation interrupt, and a true two-interpreter resume. The resume
test also proves exactly one Profile version, its field revisions, and its
document-source relationship are persisted without replaying extraction.
