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
| **Kafka + producers (GCP VM)** | Event streaming on the always-on free-tier VM |
| **Scheduling and orchestration** | systemd timer on the VM for the hourly BigQuery refresh; **Cloud Scheduler + Cloud Run Jobs + Cloud Workflows** (serverless) for the batch jobs |

**Hot path (live):** TfL → Streamlit / MCP live tools (and Kafka while the VM runs).
**Cold path (daily):** GCS → BigQuery → Google Sheet → Tableau Public, plus MCP history tools.

**The Google Sheet is the analysis layer for everything that is not live** (decided 2026-10-22):
buses, bikes, deeper line analysis and data science work, not only buses. It is fed once a day
from the BigQuery serving tables in `tfl_portfolio` (daily summaries, so it stays far below the
10 million cell limit), one tab per subject, and Tableau Public reads it. New feeds (BikePoint,
air quality, roads) add tabs; nothing in the Sheet is needed by the live views.

Google Sheet design (decided 2026-10-22): **one Sheet file for the whole project**, one tab per
subject. The 10 million cell limit applies to the whole file, so each tab keeps its own window:
**6 months** for every summary tab (line reliability by day and by hour, busiest 12 routes by
direction and time of day, all routes by day, stations and accessibility by day, prediction
accuracy by day), and **30 days** for the two detailed tabs (busiest 12 routes by stop; all routes
by 4-hour band), which would otherwise exceed the limit on their own. Estimated total ~5.8M cells,
leaving room for bikes, air quality and roads.

Daily export routine (automated, never manual deletion): write yesterday's rows; archive rows now
outside a tab's window to GCS as gzip CSV (`exports/sheets/<tab>/date=YYYY-MM-DD/`, Nearline after
30 days, deleted after 90 by a lifecycle rule, Nearline rather than Coldline: Coldline bills a
90-day minimum); delete them from the Sheet; check the file's cell count and stop with a warning
above 7M. Tableau Public shows only what is in the Sheet at its daily refresh. BigQuery remains
the source of truth; any tab or archive file can be regenerated from the serving tables.

**Buses (decided 2026-10-22).** The full network is too large for the e2-micro
(`/Mode/bus/Arrivals?count=-1`: 106,619 predictions, 101 MB, 687 MB to parse), so the rule is:
whatever can be live is live, and whatever cannot is analysed daily in Tableau Public.

| Path | Bus data | Source |
|---|---|---|
| Live (Streamlit, later MCP) | Next buses at a stop, buses on a route now, routes with problems now, current disruptions | TfL API directly: `/StopPoint/{id}/Arrivals`, `/Line/{route}/Arrivals`, `/Line/Mode/bus/Status`, `/Line/Mode/bus/Disruption`, `/StopPoint/Mode/bus/Disruption` |
| VM (history) | Arrivals for a **selected set of routes** only (~10 routes ≈ 2.9 MB per poll, the size of the Tube feed), added to the existing arrivals producer; optionally bus status on change | `/Line/{routes}/Arrivals` |
| Off the VM (reference, weekly) | Routes, stop sequences, stops, timetables (scheduled headways for excess wait time) | `/Line/Mode/bus/Route`, `/Line/{id}/Route/Sequence/{dir}`, `/Line/{id}/Timetable/{stop}`, existing bus stop data in GCS |
| Daily analysis (Tableau Public) | Headways, waits, bunching, prediction accuracy, excess wait time, disruption history | BigQuery daily aggregates → 24-hour Google Sheet |

Goal (2026-10-22): Tableau bus analysis will cover **all routes**, with **no new VM**. The VM keeps
the busiest 12 routes (continuous arrivals). All ~675 routes come from a **Cloud Run job** run every
30 minutes (Cloud Scheduler): it reads the whole network once (101 MB, needs ~690 MB memory; Cloud
Run allows 2 GB), computes per route and stop the predicted gaps between the next buses (a
network-wide bunching snapshot), and writes only that summary. ~48 one-minute runs a day stays in
Cloud Run's free tier. The summaries feed the Google Sheet for Tableau; Sheet rows are deleted after
analysis. Google Sheets cannot do the collection itself (Apps Script caps a fetch at 50 MB and a run
at 6 minutes).

## 1. Data presentation

- [x] Streamlit: live rail line status — public on the portfolio case study (joeldataportfolio.streamlit.app/transport_for_london, logic vendored in the portfolio's `tfl_live/`); standalone board in `app/streamlit_app.py`
- [ ] Streamlit + MCP: AI-powered queries about current TfL conditions
- [ ] Tableau Public: historical analytics, data science, KPIs and trends
- [ ] Google Sheets: temporary bridge for Tableau Public publishing
- [ ] 24-hour cycle: refresh Tableau's published dataset approximately daily
- [ ] Separate live operational insights (Streamlit) from historical analysis (Tableau)

## 2. Data engineering

- [~] Integrate and test all relevant TfL API endpoints — 71/84 pass (sweep 2026-10-20); freshness measured
- [~] Use Python for ingestion, validation and transformation — bus reference collectors, transformers, validators exist
- [x] Shared TfL client (`src/clients/tfl.py`): timeouts, retries with backoff and jitter, `Retry-After`, safe error messages — 25 unit tests
- [~] Endpoint configuration — `config/line_status.json` (one JSON file per live endpoint; no YAML dependency)
- [~] Use Kafka for event-streaming workflows: arrivals (Tube, tram) on the free e2-micro since 2026-10-22, single KRaft node, producer → detector → archiver (docs/endpoints/arrivals.md); 24-hour memory watch pending
- [~] Orchestration (decided 2026-10-23): the hourly serving refresh stays on its systemd timer (since
  2026-10-21). Batch jobs run serverless: **Cloud Run Jobs** do the work (all-routes bus snapshot,
  daily Google Sheet export and archive, weekly bus reference load), **Cloud Workflows** chains the
  daily steps with retries ("quality checks, then export, then archive"; Mondays also the bus
  reference load), **Cloud Scheduler** triggers them. Free-tier design: 2 Scheduler jobs (of 3
  free): the 30-minute snapshot is triggered directly (through a Workflow it would need ~7,000
  steps a month, over the 5,000 free), and the daily Workflow (~300 steps a month). Snapshot
  ~86k vCPU-s / 173k GiB-s a month of Cloud Run's 180k / 360k free, shared with the future MCP
  server. One container image in Artifact Registry (0.5 GB free) with old versions cleaned up.
  Check live pricing pages when building; a $1 budget alert is the safety net.
- [~] Use GCS for historical and excess data — archive script with manifests exists
- [x] Use BigQuery for analytical queries and transformations — line status external tables and views (`analytics/line_status/`), hourly serving table `tfl_portfolio.line_disruption_hourly` refreshed by a systemd timer on the VM
- [~] Implement deduplication and idempotent ingestion — run-partitioned, create-only writes for bus data
- [~] Automated data-quality checks — snapshot validators exist
- [~] Unit tests (pytest, mocked responses, no network) — client, line status and stop disruptions covered (100 tests); run with `.venv/bin/python -m pytest`

### Endpoint analysis card (completed for each endpoint before its collector is written)

Business questions · response structure · grain · record identity · change frequency ·
volume · hot or cold · GCS raw/processed/coverage paths · data-quality rules · retention

### Endpoint order

| # | Endpoint | Refresh (measured) | Use | Card |
|---|---|---|---|---|
| 1 | `/Line/Mode/{modes}/Status` (all rail modes) | 30 s, event-driven | Live status; reliability KPI | [x] [card](endpoints/line_status.md) |
| 2 | `/StopPoint/Mode/{modes}/Disruption` (line disruptions rejected: duplicates line status) | 30 s, slow-changing | Station notices, accessibility, planned-works overrun | [x] [card](endpoints/stop_disruptions.md) |
| 3 | `/Mode/{mode}/Arrivals` | 30 s | Waits and headways | [ ] |
| 4 | `/BikePoint` | 30 s, snapshot (store changes only) | Live map; empty/full docks | [ ] |
| 5 | `/AirQuality` | 30 min | Air-quality patterns (investigate timestamp anomaly) | [ ] |
| 6 | `/Road`, `/Road/all/Disruption` | 150 s | Incidents by borough and severity | [ ] |
| — | Reference data (lines, routes, stops) | 12 h+ | Weekly scheduled refresh | [~] bus done |

## 3. Analytics and data science

- [ ] Write 10–15 business questions and metric definitions (drives tables, dashboards and MCP tools)
- [~] Analyse bus routes, stops and network coverage — route-stop tables and coverage view built
- [ ] Analyse live arrivals and service disruptions
- [ ] Investigate air-quality patterns
- [~] Develop historical trends, comparisons and meaningful KPIs — disruption trends line chart on the portfolio (TfL map colours, hover details, 1D–1Y ranges unlocking as history grows)
- [ ] Select visualisations based on data quality and business questions
- [ ] Compute KPIs over observed time only (collection is on demand, so history has gaps)

### 3a. Sampling design

History is only useful for data science if it is a fair, consistent sample.

- [ ] Collect during fixed daily windows (e.g. 30 min each at morning peak, midday and evening peak) as part of the daily scheduled VM run — confirm VM cost first
- [ ] Treat visitor-triggered collection as extra data, never the basis for comparisons
- [ ] Minimum history before modelling or drawing conclusions: 2–4 weeks of fixed windows
- [ ] Record the collection window with every dataset so analyses can filter to comparable samples

### 3b. Data science methods

| Method | Data | Example question | Minimum history | Status |
|---|---|---|---|---|
| Descriptive statistics and distributions | Arrivals, disruptions | What is the typical wait at a station, and how variable is it? | Days | [ ] |
| Spatial analysis | Stops, routes, bike docks | Which areas have poor bus coverage or few bike docks? | Available now (reference data) | [ ] |
| Headway regularity and bunching | Arrivals | Do trains or buses arrive in clumps instead of evenly? | Days | [ ] |
| Prediction accuracy | Arrivals | How far off are TfL's countdown predictions, by line? | Days | [ ] |
| Time-series seasonality | Line status, BikePoint, air quality | Do disruptions peak at certain hours or weekdays? | 2–4 weeks | [ ] |
| Trend analysis (rolling averages) | Reliability KPI | Is a line getting more or less reliable? | Weeks | [ ] |
| Survival analysis (handles ongoing/censored events) | Disruption durations | How long does a signal failure usually last? | Weeks | [ ] |
| Anomaly detection | Disruption counts, air quality | Was today unusual compared with normal? | Weeks | [ ] |
| Hypothesis testing | Reliability | Is weekend service less reliable than weekdays? | Weeks | [ ] |
| Forecasting (baseline vs simple model, time-based split) | BikePoint | Will this dock be empty in an hour? | Weeks | [ ] |
| Correlation (not causation) | Air quality vs road incidents | Do high-pollution days coincide with more incidents? | Months | [ ] |

### 3c. Analysis workflow

- [ ] Python notebooks in `analytics/` (pandas, statsmodels, scikit-learn) reading from BigQuery / GCS
- [ ] Publish results as tables to Tableau (via the daily Sheet) and to MCP (so the AI can answer from them)
- [ ] Document each analysis: question, data used, method, assumptions, limitations

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

Bucket: `gs://transport_for_london_bucket` — `us-central1`, Standard, public access blocked,
uniform IAM access. Soft delete and versioning are **off**, so deletions are permanent.
Size on 2026-10-21: 219 MiB, 24 objects.

#### Zone layout

| Zone | Path convention | Contents | Rule |
|---|---|---|---|
| `raw/` | `raw/{endpoint}/date=YYYY-MM-DD/run_id={id}/{endpoint}.jsonl.gz` + `manifest.json` | Exact API responses, timestamped | Immutable; long-term history |
| `processed/` | `processed/{table}/date=YYYY-MM-DD/run_id={id}/{table}.csv` + manifest | Cleaned, validated tables | Rebuildable from `raw/` |
| `coverage/` | `coverage/{endpoint}/date=YYYY-MM-DD/...` | One record per poll (time, status, records, changes) | Proves when data was observed |
| `tmp/` | `tmp/...` | Partial runs, in-progress files | Auto-deleted after a few days |

- [~] `raw/` follows the convention, with SHA-256 manifests — bus datasets done
- [~] Compress raw files as `.jsonl.gz` (BigQuery reads gzip directly) — new collectors via `src/storage/batch_writer.py`
- [x] Partial or failed runs stay in local tmp until verified; leftovers are recovered on the next run
- [ ] Align `processed/` to `processed/{table}/date=/run_id=`
- [ ] Add `coverage/` records for every poll

#### One-off tidy-up of existing objects — done 2026-10-21 (approved by user)

Each object was copied, CRC32C-verified, then the source deleted; manifests were rewritten
with the new `gcs_uri` plus `moved_from` / `moved_at_utc`.

- [x] Top-level `bus_arrivals_snapshot_20261014.jsonl` → `raw/_legacy/bus_arrivals/date=2026-10-14/` (mixes legacy records and one run, so quarantined)
- [x] Top-level `bus_stop_points.json.gz` → `raw/bus_stop_points/date=2026-10-14/run_id=20261014T203151000000Z/` with a new manifest (run ID derived from `collected_at_utc`)
- [x] `processed/run_id=20261016T202642305170Z/*` → `processed/{route_stop_occurrences,unique_bus_stops}/date=2026-10-16/run_id=.../`
- [x] Four partial `bus_route_sequences` runs of 2026-10-16 → `tmp/raw/...`, manifests marked `run_status: PARTIAL`; the complete 211 MB run stays in `raw/`

`raw/_legacy/` is a quarantine for files that predate the conventions: kept, but not read by `raw/{endpoint}/` loaders.

#### Archive requirements

- [~] Preserve timestamped raw API responses before transformation — done for bus datasets
- [~] Store processed, validated datasets separately from raw data — local `data/processed/`; not yet in GCS
- [~] Partition by date and, where appropriate, endpoint or transport mode — `raw/{endpoint}/date=/run_id=`
- [x] Store a collection-coverage record for every poll (proves when data was observed) — line status collector
- [ ] Build a growing historical dataset for trends, seasonality, comparisons and modelling
- [ ] Retain history independently of the 24-hour Sheets cycle
- [~] Verify archived files are complete and readable before deleting temporary data — SHA-256 manifests exist
- [ ] Apply lifecycle and retention policies to control storage costs

## 6. Cloud cost and operations

- [~] Target $0 spend where feasible: free-tier e2-micro for collection and Kafka, BigQuery and Cloud Run within free tiers
- [~] Use lightweight cloud resources (Cloud Run for MCP and the all-routes bus snapshot; the e2-micro for collection and Kafka)
- [x] Always-on collection on a free-tier e2-micro VM (changed 2026-10-21 from stopping the VM between jobs); Kafka fits on the same e2-micro after memory tuning (Docker test 2026-10-22: p95 396 MiB of ~477 free)
- [x] Verify that the VM is stopped after use — checked 2026-10-21
- [x] Budget alert: "TfL 10$ budget" (50% / 90% / 100%) on the TfL project; expected cost ~$4/month (external IP)
- [ ] Minimise unnecessary API polling, storage and data transfer (change-only storage for snapshot feeds)

### VM start-up (decision pending)

- [ ] Choose: auto-start when a user visits the Streamlit page, or a "Start live pipeline" button
- [ ] Safeguards either way: cooldown, daily running-hours cap, heartbeat auto-stop, budget alert, status message
- [ ] Daily scheduled VM run so Tableau refreshes on days without visitors

## 7. Security and reliability

- [~] Least-privilege IAM — `tfl-pipeline-sa` has bucket objectAdmin, secret accessor and log writer only; consider narrowing objectAdmin (which can delete history) to create + read
- [~] Store API keys and secrets securely — `.env` locally, Secret Manager on GCP
- [~] Logging and monitoring — collector logs to the systemd journal (`journalctl -u tfl-line-status`); VM service account can write to Cloud Logging; no alerting yet
- [~] Handle API failures, retries and rate limits — shared client done; existing collectors not yet migrated to it (shared client)
- [~] Prevent duplicate records and overlapping pipeline runs — create-only writes; no run locking yet
- [~] Test endpoints and pipeline components independently — endpoint sweep done; no unit tests yet
- [x] Firewall: SSH only from Google IAP (35.235.240.0/20); open RDP rule removed
