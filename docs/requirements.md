# TfL Project — Requirements Checklist

Living document. Update the status markers as work lands.

Legend: `[x]` done · `[~]` in progress / partly done · `[ ]` not started

Last reviewed: 2026-10-21

## Core architecture

| Component | Role |
|---|---|
| **Streamlit** | Live intelligence: current TfL conditions, calls TfL directly |
| **MCP server on Cloud Run** | AI-powered live and business questions; scales to zero |
| **Tableau Public** | Historical analytics, data science, KPIs and trends |
| **Google Sheets** | Temporary bridge for daily Tableau Public publishing |
| **GCS** | Raw and processed historical storage |
| **BigQuery** | Analytical queries, transformations and KPI tables |
| **Kafka + producers (GCP VM)** | Event streaming while the VM runs |
| **Airflow (GCP VM)** | Orchestration and scheduling (design decided when we reach it) |

**Hot path (live):** TfL → Streamlit / MCP live tools (and Kafka while the VM runs).
**Cold path (daily):** GCS → BigQuery → Google Sheet → Tableau Public, plus MCP history tools.

## 1. Data presentation

- [ ] Streamlit: live and near-real-time transport data
- [ ] Streamlit + MCP: AI-powered queries about current TfL conditions
- [ ] Tableau Public: historical analytics, data science, KPIs and trends
- [ ] Google Sheets: temporary bridge for Tableau Public publishing
- [ ] 24-hour cycle: refresh Tableau's published dataset approximately daily
- [ ] Separate live operational insights (Streamlit) from historical analysis (Tableau)

## 2. Data engineering

- [~] Integrate and test all relevant TfL API endpoints — 71/84 pass (sweep 2026-10-20); freshness measured
- [~] Use Python for ingestion, validation and transformation — bus reference collectors, transformers, validators exist
- [ ] Shared TfL client: timeouts, retries with backoff, rate-limit handling, safe error messages
- [ ] Live endpoint registry (`config/live_endpoints.yaml`)
- [ ] Use Kafka for event-streaming workflows
- [ ] Use Airflow to orchestrate and schedule pipelines
- [~] Use GCS for historical and excess data — archive script with manifests exists
- [~] Use BigQuery for analytical queries and transformations — `route_coverage` view exists
- [~] Implement deduplication and idempotent ingestion — run-partitioned, create-only writes for bus data
- [~] Automated data-quality checks — snapshot validators exist
- [ ] Unit tests (pytest, mocked responses, no network)

### Endpoint analysis card (completed for each endpoint before its collector is written)

Business questions · response structure · grain · record identity · change frequency ·
volume · hot or cold · GCS raw/processed/coverage paths · data-quality rules · retention

### Endpoint order

| # | Endpoint | Refresh (measured) | Use | Card |
|---|---|---|---|---|
| 1 | `/Line/Mode/{modes}/Status` (all rail modes) | 30 s, event-driven | Live status; reliability KPI | [ ] |
| 2 | `/Line/Mode/{modes}/Disruption`, `/StopPoint/Mode/{modes}/Disruption` | 30 s | Disruption causes and durations | [ ] |
| 3 | `/Mode/{mode}/Arrivals` | 30 s | Waits and headways | [ ] |
| 4 | `/BikePoint` | 30 s, snapshot (store changes only) | Live map; empty/full docks | [ ] |
| 5 | `/AirQuality` | 30 min | Air-quality patterns (investigate timestamp anomaly) | [ ] |
| 6 | `/Road`, `/Road/all/Disruption` | 150 s | Incidents by borough and severity | [ ] |
| — | Reference data (lines, routes, stops) | 12 h+ | Daily refresh via Airflow | [~] bus done |

## 3. Analytics and data science

- [ ] Write 10–15 business questions and metric definitions (drives tables, dashboards and MCP tools)
- [~] Analyse bus routes, stops and network coverage — route-stop tables and coverage view built
- [ ] Analyse live arrivals and service disruptions
- [ ] Investigate air-quality patterns
- [ ] Develop historical trends, comparisons and meaningful KPIs
- [ ] Select visualisations based on data quality and business questions
- [ ] Compute KPIs over observed time only (collection is on demand, so history has gaps)

## 4. AI integration

- [ ] Host the MCP server on **Cloud Run** (scales to zero, `min-instances = 0`) — *changed from the VM on 2026-10-21*
- [ ] Live tools call TfL directly via the shared client; history tools query BigQuery
- [ ] RAG over metric definitions, disruption text and daily summaries (RAG explains, MCP calculates)
- [ ] Enable natural-language questions and data retrieval, including business questions
- [ ] Restrict MCP to Streamlit's service account (IAM authentication)
- [ ] Cap AI questions per day (model usage is billed)
- [ ] Keep MCP live queries separate from Tableau's scheduled publishing

## 5. Publishing and retention

- [ ] Publish analytical datasets through Google Sheets to Tableau Public (aggregates only; Sheets limit 10M cells)
- [ ] Verify refresh success before deleting temporary data
- [ ] Preserve historical data in GCS before cleanup
- [ ] Delete or clear temporary Sheets only after the agreed retention period and approval
- [ ] Define retention rules for each storage location

### GCS historical archive

- [~] Preserve timestamped raw API responses before transformation — done for bus datasets
- [~] Store processed, validated datasets separately from raw data — local `data/processed/`; not yet in GCS
- [~] Partition by date and, where appropriate, endpoint or transport mode — `raw/{endpoint}/date=/run_id=`
- [ ] Store a collection-coverage record for every poll (proves when data was observed)
- [ ] Build a growing historical dataset for trends, seasonality, comparisons and modelling
- [ ] Retain history independently of the 24-hour Sheets cycle
- [~] Verify archived files are complete and readable before deleting temporary data — SHA-256 manifests exist
- [ ] Apply lifecycle and retention policies to control storage costs

## 6. Cloud cost and operations

- [ ] Target $0 spend where feasible (Kafka/Airflow VM time is the main cost)
- [ ] Use lightweight cloud resources (Cloud Run for MCP; VM sized for Kafka/Airflow — confirm cost first)
- [~] Stop the GCP VM between jobs — done manually; automatic shutdown not built
- [x] Verify that the VM is stopped after use — checked 2026-10-21
- [ ] Monitor costs and configure a budget alert
- [ ] Minimise unnecessary API polling, storage and data transfer (change-only storage for snapshot feeds)

### VM start-up (decision pending)

- [ ] Choose: auto-start when a user visits the Streamlit page, or a "Start live pipeline" button
- [ ] Safeguards either way: cooldown, daily running-hours cap, heartbeat auto-stop, budget alert, status message
- [ ] Daily scheduled VM run so Tableau refreshes on days without visitors

## 7. Security and reliability

- [~] Least-privilege IAM permissions — VM service account cannot write logs; Streamlit account to be created
- [~] Store API keys and secrets securely — `.env` locally, Secret Manager on GCP
- [ ] Logging and monitoring
- [ ] Handle API failures, retries and rate limits (shared client)
- [~] Prevent duplicate records and overlapping pipeline runs — create-only writes; no run locking yet
- [~] Test endpoints and pipeline components independently — endpoint sweep done; no unit tests yet
- [ ] Restrict the firewall: SSH via IAP only, remove the open RDP rule
