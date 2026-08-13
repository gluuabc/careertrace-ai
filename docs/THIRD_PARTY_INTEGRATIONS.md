# Third-Party Integrations

This inventory describes the integration boundaries implemented by CareerTrace. It is an engineering record, not a legal conclusion. Each authenticated service is used through an account or API key under the provider's applicable terms.

## Amazon Web Services / Amazon Bedrock

- Purpose: private S3 document/evidence storage; conversational models; Titan Text Embeddings V2; Amazon Rerank 1.0.
- Official documentation: [Amazon Bedrock](https://docs.aws.amazon.com/bedrock/), [Titan Text Embeddings V2](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html), [Rerank API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_Rerank.html), [Amazon S3](https://docs.aws.amazon.com/s3/).
- Terms: [AWS Service Terms](https://aws.amazon.com/service-terms/).
- Authentication: the standard AWS credential provider chain and IAM; credentials are never accepted by the UI or stored in source code.
- Enabled by default: S3/model behavior depends on runtime configuration. Amazon Rerank is disabled until `BEDROCK_RERANK_ENABLED=true`.
- Environment: `AWS_REGION`, `S3_BUCKET_NAME`, `S3_REGION`, `BEDROCK_MODEL_CHEAP`, `BEDROCK_MODEL_REASONING`, `BEDROCK_EMBEDDING_MODEL`, `BEDROCK_EMBEDDING_DIMENSIONS`, `BEDROCK_RERANK_ENABLED`, `BEDROCK_RERANK_REGION`, `BEDROCK_RERANK_MODEL_ID`.
- Data received: documents/evidence for S3 storage; prompts or text for model processing; bounded RRF candidate text for reranking.
- Data persisted: S3 objects plus SQL metadata/results. The application does not persist raw Bedrock responses as provider logs.
- Raw provider output: source documents and large sanitized evidence may be persisted in private S3; model response facts/analysis are stored through application schemas.
- Retention: follows the application's S3 lifecycle, SQL retention, and AWS account configuration.
- Attribution: none added by CareerTrace; AWS marks and model names remain their owners' property.
- Processing region: primary Bedrock/S3 defaults to `us-east-1`; Amazon Rerank 1.0 is locked to `us-west-2`. Reranking does not move the application's other AWS resources.

## Tavily

- Purpose: optional broad-web search and URL discovery only.
- Official documentation: [Tavily API](https://docs.tavily.com/documentation/api-reference/endpoint/search).
- Terms: [Tavily terms](https://tavily.com/terms).
- Authentication: `TAVILY_API_KEY` when `TAVILY_ENABLED=true`.
- Enabled by default: no.
- Data received: a bounded search query, optional domain restrictions, and result limit.
- Data persisted: Tavily snippets are not authoritative evidence and are not persisted as evidence. A discovered URL must pass URL/DNS validation and be independently retrieved from its final official/public source before persistent evidence is created.
- Raw provider output: no.
- Retention/attribution: governed by the Tavily account and applicable terms; CareerTrace retains independently fetched evidence under its own retention policy.

## Greenhouse Job Board API

- Purpose: verified official job-board discovery for cataloged employers.
- Official documentation: [Job Board API](https://developers.greenhouse.io/job-board.html).
- Terms: [Greenhouse terms](https://www.greenhouse.com/terms-of-service).
- Authentication: public GET endpoints for configured board tokens.
- Enabled by default: only for catalog entries explicitly marked enabled and verified.
- Data received/persisted: public job records and the retrieved feed as user/run-scoped evidence.
- Raw provider output: yes, bounded and sanitized; large evidence may move to private S3.
- Retention/attribution: application evidence retention applies; source URL and provider are retained.

## Lever Postings API

- Purpose: verified official public job discovery for cataloged employers.
- Official documentation: [Lever Postings API](https://github.com/lever/postings-api).
- Terms: [Lever terms](https://www.lever.co/terms-of-service/).
- Authentication: public postings endpoint for configured sites.
- Enabled by default: only for enabled, verified catalog entries.
- Data received/persisted: public postings plus bounded raw feed evidence.
- Raw provider output: yes, with URL/provider provenance.
- Retention/attribution: application evidence retention applies.

## OpenAlex

- Purpose: structured public academic-author discovery.
- Official documentation: [OpenAlex API](https://docs.openalex.org/).
- Terms/license: [OpenAlex data license](https://docs.openalex.org/additional-help/faq#how-is-openalex-licensed).
- Authentication: optional `OPENALEX_API_KEY`.
- Enabled by default: public academic discovery is available; credentials are optional subject to provider policy.
- Data received/persisted: public author, topic, and affiliation metadata; returned source payload may be stored as evidence.
- Raw provider output: yes, bounded, with retrieval provenance.
- Retention/attribution: application evidence retention applies; OpenAlex source identity is retained.

## Wikidata

- Purpose: structured public identity discovery.
- Official documentation: [Wikidata REST API](https://www.wikidata.org/wiki/Wikidata:REST_API).
- Terms/license: [Wikidata licensing](https://www.wikidata.org/wiki/Wikidata:Licensing).
- Authentication: public endpoint.
- Enabled by default: yes for permitted people-search workflows.
- Data received/persisted: public entity metadata and bounded source evidence.
- Raw provider output: yes, with source provenance.
- Retention/attribution: application evidence retention applies; downstream reuse must respect Wikidata licensing and attribution requirements.

## CockroachDB

- Purpose: production SQL persistence, full-text retrieval, `VECTOR(1024)` storage, and cosine-distance retrieval. SQLite remains the local unit-test backend.
- Official documentation: [SQLAlchemy connection](https://www.cockroachlabs.com/docs/stable/build-a-python-app-with-cockroachdb-sqlalchemy), [full-text search](https://www.cockroachlabs.com/docs/stable/full-text-search), [VECTOR](https://www.cockroachlabs.com/docs/stable/vector), [vector indexes](https://www.cockroachlabs.com/docs/stable/vector-indexes).
- Terms: [Cockroach Labs terms](https://www.cockroachlabs.com/terms/).
- Authentication: `DATABASE_URL`; the optional integration-test URL must identify a disposable database.
- Enabled by default: no; local default is SQLite.
- Data received/persisted: user profiles, versions, metadata, memories, conversations, runs, search state, evidence metadata, drafts, retrieval documents, and embeddings.
- Raw provider output: CockroachDB is the persistence provider, not a content provider.
- Retention: controlled by the application/operator's SQL retention policy.
- Diagnostics: `COCKROACH_CLOUD_MCP_ENABLED=false` by default. The developer-only wrapper uses the official MCP Python SDK's Streamable HTTP client with the managed endpoint, a pinned cluster ID, and a bounded system-metadata-only read allowlist. It is never included in Career Agent tools.
- Agent Skills: CockroachDB's official [`cockroachlabs/cockroachdb-skills`](https://github.com/cockroachlabs/cockroachdb-skills) repository is reserved for developer/database operations. The audit used upstream commit `e14e86d23ce8ee2e7e40a34ce2944c2502b6eadd` (Apache-2.0). It is not copied into `app/skills` and is not exposed to end users. See [CockroachDB Agent Skills Audit](COCKROACH_AGENT_SKILLS.md).

## Playwright

- Purpose: controlled JavaScript rendering fallback for one already-known, validated public URL when direct HTTP has no usable content.
- Official documentation: [Playwright for Python](https://playwright.dev/python/docs/intro).
- License: [Apache-2.0](https://github.com/microsoft/playwright-python/blob/main/LICENSE).
- Authentication: none.
- Enabled by default: no (`PLAYWRIGHT_ENABLED=false`).
- Data received/persisted: the known URL is rendered; sanitized HTML may become evidence only after the same URL/domain and private-IP protections used by direct HTTP.
- Raw provider output: Playwright is a local browser library, not a remote content provider.
- Retention/attribution: application evidence retention applies.
- Operations: browser binaries are installed explicitly with `python -m playwright install chromium`; startup never installs them.

## Google OpenID Connect

- Purpose: normal-user authentication and mapping a Google subject to the application's UUID `user_id`.
- Official documentation: [OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect).
- Terms: [Google APIs Terms](https://developers.google.com/terms).
- Authentication: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `OAUTH_REDIRECT_URI`, and `AUTH_COOKIE_SECRET`.
- Enabled by default: only when configured. Judge sessions use isolated ordinary UUID users and do not bypass authorization.
- Data received/persisted: subject identifier, email, name, and optional profile image; no Google password is received or stored.

## LangSmith

- Purpose: optional LangChain/LangGraph tracing.
- Official documentation: [LangSmith observability](https://docs.langchain.com/langsmith/observability).
- Terms: [LangChain terms](https://www.langchain.com/terms-of-service).
- Authentication: `LANGCHAIN_API_KEY` when tracing is enabled.
- Enabled by default: configuration-dependent.
- Data received/persisted: application traces according to the configured project and LangSmith account retention. CareerTrace sanitizes its own persisted tool trajectory, but operators must separately review tracing configuration for sensitive-data handling.

## Not integrated: Firecrawl

Firecrawl was evaluated as a possible future optional discovery/extraction provider. It is not installed, configured, called, required, or exposed to the Career Agent in this implementation. Any future integration requires explicit approval and a separate terms, privacy, validation, and retention review.
