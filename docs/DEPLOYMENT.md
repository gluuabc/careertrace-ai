# Deployment and Verification

## Current public demo

- URL: <https://ca-52d4a73a56df47e78e9405283c5d3daa.ecs.us-east-1.on.aws/>
- Region indicated by the public endpoint: `us-east-1`
- Entrypoint: `app/ui/dashboard.py`
- Health path: `/_stcore/health`

The endpoint was supplied by the deployment operator. Before submission, verify
both the root page and health path from a normal Internet connection. A
network-restricted development environment failing DNS is not evidence that the
deployment itself is unavailable.

## Runtime topology

```text
Public AWS ECS endpoint
  → CareerTrace Streamlit container
      ├── CockroachDB application SQL
      ├── CockroachDBSaver checkpoint schema
      ├── Amazon Bedrock generation
      ├── Titan Text Embeddings V2
      ├── private Amazon S3 documents/evidence
      └── optional Amazon Rerank and public search providers
```

The repository includes the production `Dockerfile`, container health check,
S3 CloudFormation template, and least-privilege S3 object policy. It does not
currently include the operator's ECS task/service definitions, ECR publishing
automation, load-balancer configuration, or Secrets Manager resources. Do not
claim those resources are reproducible from this repository until their
infrastructure definitions are committed.

## Database prerequisite

The current Alembic head is `20260817_17`. Production deployment requires:

- a CockroachDB `DATABASE_URL` with `sslmode=verify-full`;
- `COCKROACH_CA_CERT` or a deployment-accessible `sslrootcert`;
- `LANGGRAPH_CHECKPOINT_BACKEND=cockroachdb`;
- a separate checkpoint schema;
- migrations run once with a migration identity before new application tasks
  start.

Run the real migration path in a disposable Cockroach database first:

```bash
DATABASE_URL='cockroachdb://VALIDATION_USER:.../VALIDATION_DATABASE?sslmode=verify-full' \
  python scripts/validate_cockroach_memory_migration.py
```

Never point the validator or `COCKROACH_TEST_DATABASE_URL` at production.

## Runtime configuration

Use `.env.example` as the public contract. Supply completed values through the
deployment's server-side secret mechanism; do not commit them.

Required production areas are:

- CockroachDB URL, CA certificate, and checkpoint backend;
- AWS credential provider chain, region, Bedrock models, and S3 bucket;
- Judge mode and a private access code;
- optional provider credentials only when that provider is enabled.

CareerTrace materializes `COCKROACH_CA_CERT` to a restrictive temporary file and
uses the same resolved TLS URL for SQLAlchemy and `CockroachDBSaver`.

## Pre-submission verification

1. Confirm the deployed revision is the intended submission commit.
2. Confirm `alembic current` reports `20260817_17 (head)` using a read-only
   migration-status check.
3. Check the public health endpoint:

   ```bash
   curl --fail --silent --show-error \
     https://ca-52d4a73a56df47e78e9405283c5d3daa.ecs.us-east-1.on.aws/_stcore/health
   ```

4. Start a new Judge workspace and complete
   [`JUDGE_TESTING_INSTRUCTIONS.md`](JUDGE_TESTING_INSTRUCTIONS.md).
5. Save the recovery code, log out, and resume the same workspace.
6. Restart or replace the application task and repeat the recovery check.
7. Confirm no secret, recovery code, local database, or `.env` file appears in
   the image or repository.

## Repository-side setup validation

Run the deployed-mode configuration checks without printing secret values:

```bash
python scripts/check_setup.py --mode deployed
```

Live checks must be run only from an authorized environment with the required
AWS and Cockroach connectivity.
