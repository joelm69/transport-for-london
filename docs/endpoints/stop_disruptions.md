# Endpoint analysis card — Stop disruptions (rail modes)

Status: steps 1–3 complete · steps 4–5 (data-quality rules, hot/cold use) pending

| | |
|---|---|
| Endpoint | `GET /StopPoint/Mode/tube,dlr,overground,elizabeth-line,tram/Disruption` |
| Refresh (measured) | TfL `max-age` 30 s; content changes slowly (planned works, station incidents) |
| Size | ~37 KB per call (5 KB gzip), ~63 records about ~52 stations (2026-10-21) |
| Collect every | 60 s |
| Evidence | Two calls on 2026-10-21, 20:59:35 and 21:01:53 UTC |

## Not collected: `/Line/Mode/{modes}/Disruption`

Assessed in step 1 and rejected. Its records duplicate the disruptions already carried by line
status (same four lines, identical text), it has **no line ID field** (the line would have to be
parsed from the text, which fails for "London Tramlink" and the Elizabeth line), and
`affectedRoutes` / `affectedStops` were empty. Line status already provides the same disruptions with
a reliable `line_id`.

## 1. Response structure

A flat JSON array, one object per (station, disruption message):

| Field | Values seen | Meaning |
|---|---|---|
| `atcoCode`, `stationAtcoCode` | `940GZZLUBSC` | Stop and parent-station IDs (match the project's stop data) |
| `commonName` | "Barons Court Underground Station" | Station name |
| `mode` | tube 26, elizabeth-line 16, overground 14, dlr 5, tram 2 | |
| `type` | Information 31, Interchange Message 29, Part Closure 3 | Kind of disruption |
| `appearance` | RealTime 36, Information 16, PlannedWork 11 | Live incident vs planned works |
| `description` | free text | e.g. lift outages, no step-free access, trains not calling |
| `fromDate`, `toDate` | ISO UTC | See field assessment |
| `concernedLines` | present on 1 of 63 records | Lines and directions affected |

Accessibility is a major theme: many messages concern lifts and step-free access.

## 2. Grain and identity

**Grain:** one row per (station, type, fromDate) disruption per observation.

| Key | Built from | Purpose |
|---|---|---|
| `disruption_key` | `atcoCode` + `type` + `fromDate` | Identifies one disruption |
| `fingerprint` | `description` + `appearance` + `toDate` **only when PlannedWork** | Detects real changes |

Comparison between polls sorts records first: TfL returns the same records in a different order on
each call, which explains the "changes every call" result of the freshness measurement.

### Correction to step 1

Step 1 reported identical duplicate records in a single response. That was wrong: the example
printout showed the same record twice. A field-by-field check found all 63 records distinct. Eight
(station, description) pairs repeat, but each pair differs in `type` and in `fromDate` and/or `toDate`
(the same message published as both "Information" and "Interchange Message"). That is why `type`
is part of `disruption_key`; deduplicating on (station, description) would delete real records.

### Field assessment

| Field | Use | Evidence |
|---|---|---|
| `fromDate` | Event time (TfL-reported start) | Unchanged for all 55 matched disruptions across both calls |
| `toDate`, PlannedWork | Planned end date; usable | Real dates (Nov 2026 – May 2027) |
| `toDate`, RealTime | Stored as `to_date_raw`, **never used** | Rolling placeholder: moved 23:58 → 00:00 between calls ~2 min apart (≈ call time + 3 h) |
| Record order | Ignored | Shuffled between calls with identical content |

## 3. Storage layout and retention

### Volume and raw strategy

| Strategy | Storage per year |
|---|---|
| Keep every poll, 60 s | 2.7 GB |
| Keep every poll, 5 min | 0.55 GB |
| **Chosen: poll every 60 s, store raw only on change + 1 checkpoint per hour** | **~0.08 GB** |

A poll's content hash is computed from the sorted records with the RealTime `toDate` removed. Raw is
stored when the hash differs from the last stored poll (`raw_reason = change`), on the first poll of a
session (`baseline`) and on the first poll of each hour (`checkpoint`). Every poll gets a coverage
record with the hash, so unchanged polls are still provably observed. Checkpoints guarantee one
complete real response per hour for rebuilding if the change logic ever proves wrong.

### Paths (same zones and contract as line status)

| Zone | Path | Contents |
|---|---|---|
| Raw | `raw/stop_disruptions/date=YYYY-MM-DD/hour=HH/stop_disruptions_{batch_start}.jsonl.gz` + `<file>.manifest.json` | Envelopes with `raw_reason` and the full response |
| Changes | `processed/stop_disruption_changes/date=YYYY-MM-DD/stop_disruption_changes_{batch_start}.jsonl.gz` | One row per disruption whose fingerprint changed |
| Coverage | `coverage/stop_disruptions/date=YYYY-MM-DD/coverage_{batch_start}.jsonl.gz` | One row per poll: time, HTTP status, records, `content_hash`, `raw_stored` |
| Temporary | `tmp/stop_disruptions/...` | Local batches until verified |

### Change record columns

`poll_id`, `collected_at_utc`, `collection_window`, `disruption_key`, `fingerprint`,
`previous_fingerprint`, `atco_code`, `station_atco_code`, `station_name`, `mode`, `type`,
`appearance`, `description`, `from_utc`, `to_utc` (PlannedWork only), `to_date_raw`,
`concerned_lines`, `accessibility_flag` (lift / step-free / escalator mentioned), `change_type`
(`BASELINE`, `NEW_INCIDENT`, `UPDATE`, `RESOLVED`).

### Running it

A separate systemd service (`tfl-stop-disruptions`) on the same e2-micro VM, isolated from the
line status collector so a failure in one cannot stop the other.

### Retention

| Zone | Keep | Reason |
|---|---|---|
| `raw/stop_disruptions` | Indefinitely | ~0.08 GB/year with change-only storage |
| `processed/stop_disruption_changes` | Indefinitely | Small; rebuildable from raw |
| `coverage/stop_disruptions` | Indefinitely | Proves observation time |
| `tmp/stop_disruptions` | 7 days | Partial batches only |

## 4. Data-quality rules

*Pending.*

## 5. Hot and cold use

*Pending.*
