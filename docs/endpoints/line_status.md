# Endpoint analysis card — Line status (all rail modes)

Status: steps 1–3 complete · steps 4–5 (data-quality rules, hot/cold use) pending

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
Polls are batched into one file per hour (avoids the small-files problem).

### Paths (storage contract — unchanged when Kafka becomes the writer)

| Zone | Path | Contents |
|---|---|---|
| Raw | `raw/line_status/date=YYYY-MM-DD/hour=HH/line_status_{batch_start}.jsonl.gz` + `manifest.json` | One envelope per poll: `poll_id`, `collected_at_utc`, `collection_window`, `http_status`, `endpoint`, full `response` |
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

*Pending.*

## 5. Hot and cold use

*Pending.*
