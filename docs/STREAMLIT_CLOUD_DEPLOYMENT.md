# Streamlit Community Cloud deployment

CareerTrace's public deployment uses:

```text
GitHub branch
  -> Streamlit Community Cloud
  -> CockroachDB application SQL + LangGraph checkpoints
  -> private S3 documents/evidence
  -> Amazon Bedrock
```

Local `.env` is private developer configuration and is ignored by Git.
`.env.example` is the public configuration contract and contains no secrets.
Streamlit Cloud **Secrets** is the deployment-only store for completed values.

## Before opening Streamlit Cloud

1. Provision a persistent Cockroach application database. Do not use the
   disposable database from `COCKROACH_TEST_DATABASE_URL`.
2. Run Alembic to head using a migration credential:

   ```bash
   DATABASE_URL='cockroachdb://...' alembic upgrade head
   ```

3. Give the runtime application identity only the required application DML,
   sequence, connection, and checkpoint-schema permissions. The deployed app
   checks migration state on startup; it must not use a local SQLite database.
4. Confirm the S3 application identity has only the documented object actions
   for `careertrace-resumes`.
5. Prepare a private completed copy of
   `docs/streamlit-secrets.example.toml`. Do not save that completed file in the
   repository. Do not copy a machine-local `sslrootcert=/Users/...` parameter
   into the cloud `DATABASE_URL`.

## Create the app

Open [Streamlit Community Cloud](https://share.streamlit.io/) and select
**Create app**. Use:

- Repository: `gluuabc/careertrace-ai`
- Branch: `codex/careertrace-memory-portability`
- Entrypoint: `app/ui/dashboard.py`
- App URL: choose a stable available subdomain and record the resulting HTTPS URL

Open **Advanced settings** before deployment:

- Python version: `3.13`
- Secrets: paste the completed root-level TOML secret set

Root-level Streamlit secrets become environment variables, which matches the
CareerTrace configuration boundary. Never add `COCKROACH_TEST_DATABASE_URL`, a
local SQLite URL, a recovery code, or a local certificate path.

## Required deployment secrets

- Persistent application `DATABASE_URL` using `cockroachdb://`.
- `LANGGRAPH_CHECKPOINT_BACKEND="cockroachdb"` and checkpoint schema.
- Server-side AWS credential-chain values, region, Bedrock generation/token/
  embedding models, and private S3 bucket.
- Enabled Judge mode plus a private shared access code.

Tavily, Playwright, Rerank, LangSmith, Google OIDC, and developer MCP are
optional. Keep an unused integration disabled. The Judge path does not require
Google and remains available when Google settings are absent.

## Optional Google login

After the final Streamlit URL exists, set:

```toml
GOOGLE_CLIENT_ID = "..."
GOOGLE_CLIENT_SECRET = "..."
AUTH_COOKIE_SECRET = "at-least-32-random-characters"
OAUTH_REDIRECT_URI = "https://YOUR_APP.streamlit.app/oauth2callback"
```

Add that exact HTTPS callback to Google Auth Platform **Authorized redirect
URIs**. Google may remain in External Testing mode; Judge access does not depend
on Google allowlisting.

## Post-deployment acceptance

Do not treat deployment as complete until all checks pass:

1. `https://YOUR_APP.streamlit.app/` is publicly reachable.
2. `https://YOUR_APP.streamlit.app/_stcore/health` returns `ok`.
3. The Judge entry page loads without Google login.
4. A new Judge workspace can upload the synthetic documents through S3 and
   complete Profile confirmation through the Cockroach-backed checkpointer.
5. Logout and recovery restore the same SQL-backed workspace.
6. The app is rebooted from Streamlit settings and the same workspace still
   recovers, proving sleep/wake compatibility.
