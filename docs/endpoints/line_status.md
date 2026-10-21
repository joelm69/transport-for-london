# Endpoint analysis card — Line status (all rail modes)

Status: complete (2026-10-21) · collector implemented: `src/collectors/line_status.py`

| | |
|---|---|
| Endpoint | `GET /Line/Mode/tube,dlr,overground,elizabeth-line,tram/Status?detail=false` |
| Refresh (measured) | TfL `max-age` 30 s; event-driven (changes only when an incident starts, changes or ends) |
| Size | ~20 KB per call, 20 lines (11 tube, 6 Overground, DLR, Elizabeth line, tram) |
| Collect every | 30 s while the VM runs |
| Evidence | Two calls on 2026-10-21, 17:29:46 and 17:35:39 UTC |

## 1. Response structure

```
line (id, name, modeName, created, modified)
 └── lineStatuses[]            one or more per line
      ├── statusSeverity, statusSeverityDescription, reason
      ├── validityPeriods[]    fromDate, toDate, isNow
      └── disruption{}         category, description
```

Every rail line had one status on 2026-10-21, but the bus snapshot of 2026-10-15 had
789 statuses for 675 lines, so a line can carry several statuses at once.

## 2. Grain and identity

**Grain:** one row per (line, status) per observation.

| Key | Built from | Purpose |
|---|---|---|
| `incident_key` | `line_id` + `validityPeriods[0].fromDate` | Identifies one disruption: counts, durations, survival analysis |
| `status_fingerprint` | `line_id` + `statusSeverity` + SHA-256(`reason`) | Detects any change between polls |

Why the reason is in the fingerprint: severity says how bad, not what happened. Two different
incidents with the same severity would otherwise merge into one, inflating durations and hiding causes.

Why `fromDate` identifies the incident: it stayed identical for all six ongoing incidents across both
calls, while text edits would change the reason hash. Using both keys avoids merging distinct incidents
and splitting one incident when TfL edits its text.

### Field assessment

| Field | Use | Evidence |
|---|---|---|
| `validityPeriods[0].fromDate` | **Event time** (TfL-reported start) | Stable across calls; Elizabeth line incident reported 17:32:38, observed at 17:35:39 |
| `collected_at_utc` (ours) | **Processing time** | |
| `validityPeriods[0].toDate` | Stored as `to_date_raw`; **never used in KPIs** | Rolling placeholder: call time + 3 h (moved 20:29 → 20:35 between calls) or end of service day (00:29) |
| `lineStatuses[].id` | Ignored | Always `0` |
| `lineStatuses[].created` | Ignored | Always `0001-01-01` placeholder |

### Assumptions to verify with collected data

- `fromDate` stays constant for the life of an incident (check after one week: count incidents whose `fromDate` changes).
- `fromDate` may be TfL's publication time rather than the true fault start; label it "TfL-reported start".

## 3. Storage layout and retention

### Volume

| Pattern | Polls/day | Raw gzip |
|---|---|---|
| Fixed windows (1.5 h/day) | 180 | ~0.4 MB/day |
| VM on all day | 2,880 | ~6 MB/day (~2 GB/year) |

Every raw response is kept: cheap, and it allows all history to be rebuilt if change logic is fixed later.
Polls are batched into one file per hour (avoids the small-files problem). Each batch file has its own
`<file>.manifest.json`, because two collection sessions in the same hour produce two batch files.

### Paths (storage contract — unchanged when Kafka becomes the writer)

| Zone | Path | Contents |
|---|---|---|
| Raw | `raw/line_status/date=YYYY-MM-DD/hour=HH/line_status_{batch_start}.jsonl.gz` + `<file>.manifest.json` | One envelope per poll: `poll_id`, `collected_at_utc`, `collection_window`, `http_status`, `endpoint`, full `response` |
| Changes | `processed/line_status_changes/date=YYYY-MM-DD/line_status_changes_{batch_start}.jsonl.gz` | One row per (line, status) whose fingerprint changed |
| Coverage | `coverage/line_status/date=YYYY-MM-DD/coverage_{batch_start}.jsonl.gz` | One row per poll, change or not |
| Temporary | `tmp/line_status/...` | Partial or failed batches |

### Change record columns

`poll_id`, `collected_at_utc`, `collection_window`, `line_id`, `line_name`, `mode`, `status_index`,
`severity`, `severity_description`, `reason`, `incident_key`, `status_fingerprint`,
`previous_fingerprint`, `event_from_utc`, `to_date_raw`, `change_type`

`change_type`:
- `BASELINE` — first poll of every collection session (full state, because changes while the VM was off are unknown)
- `NEW_INCIDENT` — a new `incident_key` appears
- `UPDATE` — same incident, severity or reason changed
- `RESOLVED` — an incident disappears (line returns to Good Service)
- `ONGOING` — unchanged status, re-emitted because another status on the same line changed,
  so a line's latest change rows always describe its full state

Every change row also carries `severity_class` (good / closed / disrupted), `previous_fingerprints`,
`resolved_incident_keys` and `dq_flags` (WARN rules that applied).

### Coverage record columns

`poll_id`, `collected_at_utc`, `collection_window`, `http_status`, `latency_ms`,
`lines_returned`, `statuses_returned`, `changes_written`

Status periods (start/end per status) are derived later in BigQuery with SQL window functions over
changes, bounded by coverage, so KPIs only count observed time.

### Retention

| Zone | Keep | Reason |
|---|---|---|
| `raw/line_status` | Indefinitely, Standard class | History; ~2 GB/year worst case; review at 4 GB bucket size |
| `processed/line_status_changes` | Indefinitely | Small; rebuildable from raw |
| `coverage/line_status` | Indefinitely | Required to compute KPIs over any past period |
| `tmp/line_status` | 7 days (lifecycle rule) | Partial or failed batches only |

## 4. Data-quality rules

Rules were tested against the two calls of 2026-10-21 before being adopted.

**Principle:** validation gates the processed zone, never raw. Raw is always stored; an ERROR means no
change records from that poll and the coverage record marks it failed. Fixing a rule later means
reprocessing raw, with no history lost.

| Level | Action |
|---|---|
| ERROR | Structurally broken: no change records from this poll; raw kept; coverage marks failure |
| WARN | Unusual but possibly legitimate: keep, flag, log, review |
| CHECK | Consistency across polls: SQL in BigQuery |

Reference: TfL severity codes 0–20 (`/Line/Meta/Severity`), identical for all rail modes on
2026-10-21. Refresh daily; treat as reference data.

### Per poll

| # | Rule | Level |
|---|---|---|
| P1 | HTTP 200 and body is a JSON array | ERROR |
| P2 | All 20 expected line IDs present, no duplicates | ERROR if missing/duplicate · WARN for unknown extra ID |
| P3 | Every line has at least one status | ERROR |

### Per status

| # | Rule | Level |
|---|---|---|
| S1 | `statusSeverity` is in TfL's severity list for the mode | WARN (TfL may add codes) |
| S2 | `statusSeverityDescription` matches the code | WARN |
| S3 | If `status.lineId` is present, it equals `line.id` (Good Service omits it) | ERROR |
| S4 | Not Good Service → non-empty `reason` | WARN |
| S5 | Not Good Service → ≥ 1 validity period with parseable UTC `fromDate`; otherwise `incident_key` is null and the record is flagged | WARN |
| S6 | `validityPeriods[0].isNow` is true | WARN |
| S7 | `fromDate` ≤ `collected_at_utc` + 5 min | WARN |
| S8 | Good Service → empty `reason` and no validity periods | WARN |

Rejected candidates: "reason starts with the line name" (fails for tram "London Tramlink" and the
Elizabeth line); "`status.lineId` always equals `line.id`" (Good Service omits `lineId`).

### Across polls (BigQuery)

| # | Check | Purpose |
|---|---|---|
| C1 | No duplicate `poll_id` | Idempotency |
| C2 | Reprocessing raw produces identical change records | Deterministic logic |
| C3 | Report gaps > 90 s between polls within a session | Coverage for KPIs |
| C4 | Every session starts with `BASELINE` records | On-demand correctness |
| C5 | Incidents open at session end are marked censored | Survival analysis |
| C6 | `fromDate` never changes within an `incident_key` | Verifies the step-2 assumption |
| C7 | Batch record count and SHA-256 match the manifest before promotion from `tmp/` to `raw/` | Archive integrity |
| C8 | Count incidents lasting a single poll and Good ↔ disrupted flips within 60 s | Measures flapping (see findings) |

### Where rules run

- P and S rules: Python validator at collection time
- C rules: SQL in BigQuery
- All rules: pytest, using the saved responses of 2026-10-21 as fixtures

### Note for metric definitions

Reliability must treat 10 Good Service and 18 No Issues as good, and exclude 20 Service Closed
(overnight closures) from the denominator; otherwise every night counts as a failure.

## 5. Hot and cold use

### Hot path (live)

**Streamlit live board** — calls TfL directly, cached 30 s.

| Shows | From |
|---|---|
| Coloured row per line (green good, amber delays, red suspended/closed) | `statusSeverity` |
| Disruption reason | `reason` |
| "Disrupted for 1 h 12 min" | now − `fromDate` (event time) |
| Mode filter | `modeName` |

**MCP live tool** (Cloud Run, calls TfL directly): `get_line_status(line | mode)`.

### Cold path (daily, BigQuery → Sheet → Tableau)

| Table | Grain | Key columns | Rows/year |
|---|---|---|---|
| `daily_line_reliability` | line × day | observed in-service minutes, good minutes, `reliability_pct`, incident count, disrupted minutes by severity | ~7,300 |
| `line_incidents` | incident | `incident_key`, line, start (`fromDate`), end (first `RESOLVED`), duration, censored, worst severity, cause category, text-update count | a few thousand |
| `line_hourly_profile` | line × weekday × hour | disruption minutes, observed minutes | 3,360 (fixed) |

Sheet cell budget: aggregate hourly data to weekday × hour so the published table has a fixed size;
full hourly detail stays in BigQuery.

### Metric definitions (shared by Tableau and MCP)

| Metric | Definition |
|---|---|
| In-service observed minutes | Minutes covered by coverage records, excluding status 20 (Service Closed) |
| Good minutes | In-service observed minutes with status 10 (Good Service) or 18 (No Issues) |
| `reliability_pct` | Good minutes ÷ in-service observed minutes |
| Incident duration | First `RESOLVED` observation − `fromDate` (end precision ~±30 s) |
| Censored incident | Still open when collection stopped; true duration is at least the observed duration |

`fromDate` gives true start times even for incidents that began while the VM was off (captured by
`BASELINE`), so collection gaps affect end times only — handled as censoring.

### Cause categories

First version: keyword rules on `reason` — signal → Signal failure; track → Track fault;
ill / customer → Customer incident; train fault → Train fault; staff → Staff shortage; else Other.
Measure the share of Other before considering a text classifier.

### MCP and RAG

| Question | MCP tool (numbers) | RAG (context) |
|---|---|---|
| Which line was least reliable last week? | `get_reliability(period)` | `reliability_pct` definition |
| How long do signal failures usually last? | `incident_durations(cause)` | What censoring means for the answer |
| Why was the Jubilee line bad on Tuesday? | `list_incidents(line, date)` | Incident reason texts |
| Is the Overground worse at weekends? | `compare_reliability(weekday, weekend)` | Sampling-design caveat |

### Business questions and methods (draft)

| Business question | Method |
|---|---|
| Which lines are least reliable, and when? | Descriptive statistics, seasonality heatmap |
| What causes disruptions, and how long do they last? | Survival analysis (censored durations) |
| Is weekend service worse than weekdays? | Hypothesis testing |
| Is reliability improving or worsening? | Trend analysis (rolling averages) |
| Was today unusually bad? | Anomaly detection on daily incident counts |
| How often do incidents escalate (e.g. Minor → Severe)? | Descriptive statistics over `UPDATE` records |

## Findings from the first live run (2026-10-21 18:13–18:14 UTC)

**Flapping.** The Elizabeth line was Good Service at 18:13:08, Severe Delays at 18:13:38 with
`fromDate` 18:12:12 (earlier than the Good Service response), and Good Service again at 18:14:08.
Likely cause: TfL cache servers returning different versions of the feed.

Decision: the change log stays a faithful record of what TfL returned. Debouncing (ignoring states that
last a single poll) belongs in the derived status-period SQL, where it can be tuned and re-run on all
history. Check C8 measures how often it happens; revisit after one week of data.
