# London Transport Live Data Platform

A data platform built on the [Transport for London Unified API](https://api.tfl.gov.uk/): live
network status now, and a growing historical archive for reliability analysis, Tableau dashboards
and AI-powered questions.

**Status: in progress.** Rail line status is live end to end. Streaming, orchestration, KPIs and
the AI assistant are planned. See [docs/requirements.md](docs/requirements.md) for the full checklist.

## Architecture

| Path | Purpose | Components |
|---|---|---|
| **Hot (live)** | What is happening now | Shared TfL client → Streamlit live board · MCP live tools (planned) |
| **Cold (daily)** | History, KPIs, data science | Collectors → Google Cloud Storage → BigQuery → Google Sheets → Tableau Public (planned) |

Built so far:

- **API discovery** — all 84 TfL operations catalogued; 71 return data with endpoint-specific inputs;
  refresh frequency measured per endpoint ([scripts/](scripts/), [reports/](reports/)).
- **Shared client** — retries with exponential backoff and jitter, `Retry-After`, error messages that
  never contain the API key ([src/clients/tfl.py](src/clients/tfl.py)).
- **Line status collector** — raw responses always kept; change records (`BASELINE`, `NEW_INCIDENT`,
  `UPDATE`, `RESOLVED`, `ONGOING`) and per-poll coverage records; crash-safe hourly gzip batches with
  SHA-256 manifests ([src/collectors/line_status.py](src/collectors/line_status.py)).
- **Data quality** — 19 rules (ERROR / WARN / CHECK) tested against real responses
  ([docs/endpoints/line_status.md](docs/endpoints/line_status.md)).
- **Live board** — Streamlit app showing all 20 rail lines, refreshed every 30 s with one shared
  TfL call for all visitors ([app/streamlit_app.py](app/streamlit_app.py)).
- **Bus reference data** — 32,567 stops and 675 lines catalogued; 55,986 validated route–stop links
  across 19,985 stops loaded into BigQuery.

## Findings

- TfL's disruption "end date" is a rolling placeholder (request time + 3 hours), so durations are
  measured from observed status changes.
- The live feed can flap: a line can switch Good → Severe → Good within a minute. The change log stays
  faithful to the source; smoothing happens in the analysis layer.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # then set TFL_API_KEY (free key from api-portal.tfl.gov.uk)

.venv/bin/python -m pytest                                   # unit tests, no network
.venv/bin/streamlit run app/streamlit_app.py                 # live board
.venv/bin/python -m src.collectors.line_status --polls 3     # collect into data/lake/
```

## Repository layout

| Folder | Contents |
|---|---|
| `app/` | Streamlit live board |
| `src/clients/` | Shared TfL client and secret lookup |
| `src/collectors/` | Endpoint collectors |
| `src/validation/`, `src/deduplication/` | Data-quality rules and change detection |
| `src/storage/` | Data-lake writers (local or GCS) |
| `src/presentation/` | Board logic shared by the app and future MCP tools |
| `config/` | Endpoint configuration and API catalogue |
| `docs/` | Requirements checklist and endpoint analysis cards |
| `tests/` | Unit tests and recorded TfL responses |

## Security

API keys are never committed. Locally the key comes from `.env`; on Google Cloud from Secret Manager;
on Streamlit Community Cloud from the app's secrets.

Powered by TfL Open Data. Contains OS data © Crown copyright and database rights 2016 and
Geomni UK Map data © and database rights 2019.
