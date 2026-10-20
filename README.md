
# London TfL Transport Data Platform

## Project objective

Build a cloud-hosted data engineering and analytics platform using Transport for London (TfL) API data. The platform will support live transport queries, historical analysis, automated data collection, and public dashboards.

## Engineering objectives

- Integrate the tested TfL API endpoints through reusable Python clients and collectors.
- Write unit tests for each endpoint's parsing, validation, and error handling.
- Inspect the actual API responses and use analytical judgment to select meaningful questions, metrics, and visualizations.
- Prevent unintended duplication through defined record identities and idempotent processing.
- Use Google Cloud for workload execution and persistent project storage.
- Use Kafka for event streaming and Airflow for orchestration.
- Store raw historical snapshots in Google Cloud Storage with documented retention rules.
- Host an authenticated MCP interface on the Google Cloud VM for agent access to live TfL data and historical datasets.
- Publish a curated dataset to Tableau Public through the planned Google Sheets workflow, targeting a 24-hour publication cycle.
- Apply least-privilege IAM, secure secrets management, logging, monitoring, and cost controls.

## Architecture principles

1. Cloud-first execution; VS Code on macOS is the development interface.
2. Data-driven analytics: proposed KPIs must be supported by the actual source data.
3. Idempotent ingestion and explicit deduplication rules.
4. Separation of raw, processed, and analytical data.
5. Automated tests before scaling collection workloads.
6. Target zero cloud spend, subject to actual resource costs and free-tier eligibility.

## Initial repository structure

- `config/` — endpoint registry and non-secret configuration
- `src/clients/` — shared API clients
- `src/collectors/` — endpoint-specific collection
- `src/validation/` — schema and data-quality checks
- `src/storage/` — storage integrations
- `src/deduplication/` — record identity and duplicate prevention
- `src/mcp_server/` — MCP tools
- `tests/unit/` — mocked unit tests
- `tests/integration/` — tests against live services
- `kafka/` — producers, consumers, and event schemas
- `airflow/` — orchestration DAGs
- `analytics/` — transformations and metrics
- `docs/` — architecture and engineering decisions

## Current status

- [x] Initial repository structure created
- [x] `.gitignore` created
- [x] `.env.example` created
- [ ] Tested endpoint inventory documented
- [ ] API response schemas inspected
- [ ] Unit tests implemented
- [ ] Google Cloud project and billing verified
- [ ] VM provisioned and secured
- [ ] Cloud ingestion and deduplication validated
- [ ] Kafka and Airflow deployed
- [ ] MCP server secured and tested
- [ ] Tableau publication workflow implemented

## Security

Never commit API keys, credentials, private keys, or real `.env` files. Production secrets should be stored in Google Cloud Secret Manager and accessed using least-privilege identities.

## Cost policy

The target is $0 cloud spend. Verify current pricing, free-tier eligibility, resource limits, and billing safeguards before provisioning resources.