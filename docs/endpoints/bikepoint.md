# Endpoint analysis card — BikePoint (Santander Cycles)

Status: collector built (2026-10-29): `src/collectors/bikepoint.py`, systemd service `tfl-bikepoint` (`deploy/tfl-bikepoint.service`)

| | |
|---|---|
| Endpoint | `GET /BikePoint` |
| Refresh (measured) | TfL `max-age` 30 s; about 77 stations change per minute, 118 in two minutes (afternoon, 2026-10-29) |
| Size | ~2.2 MB per call (78 KB gzip), 799 docking stations, ~2 s |
| Collect every | 120 s (agreed 2026-10-29: enough for minutes empty or full per day at half the polls of 60 s) |
| Evidence | Four calls on 2026-10-29 between 16:48 and 16:57 UTC |

## 1. Response structure

A flat JSON array, one object per docking station. Fixed fields sit on the object; everything else
is a list of `additionalProperties` (`key`, `value` as text, `modified`).

| Field | Example | Meaning |
|---|---|---|
| `id` | `BikePoints_1` | Station ID (unique, 799 of 799) |
| `commonName` | "River Street , Clerkenwell" | Station name |
| `lat`, `lon` | 51.529163, -0.10997 | Location (no separate reference fetch needed for maps) |
| `TerminalName` | `001023` | Terminal ID on the street (unique) |
| `NbBikes` | 16 | Bikes docked |
| `NbStandardBikes`, `NbEBikes` | 11, 5 | Split of `NbBikes`; always add up to it |
| `NbEmptyDocks` | 3 | Docks free to return a bike |
| `NbDocks` | 19 | Docks at the station (10–63) |
| `Installed`, `Locked` | true, false | All 799 installed and unlocked |
| `Temporary` | false | 4 temporary stations |
| `InstallDate`, `RemovalDate` | epoch ms, empty | `RemovalDate` set on 3 stations that are still installed |
| `modified` | ISO UTC | Same for every property of a station: when its counts last changed (median 11 min old, max 6.3 h) |

### Snapshot, 2026-10-29 16:50 UTC

| | Count |
|---|---|
| Bikes docked | 7,731 (926 e-bikes, 12%) |
| Empty docks | 11,498 |
| Docks | 21,006 |
| Empty stations (`NbBikes` = 0) | 44 |
| Full stations (`NbEmptyDocks` = 0) | 19 |
| **Docks out of use** (`NbDocks` − `NbBikes` − `NbEmptyDocks`) | **1,777 (8.5%), at 660 of 799 stations** |

The feed never names the out-of-use docks. They are neither holding a bike nor free, which matches
broken or reserved docks. This is derived, not reported by TfL, and is labelled that way everywhere.

## 2. Grain and identity

**Grain:** one row per station per observation.

| Key | Built from | Purpose |
|---|---|---|
| `station_id` | `id` | Identifies a station |
| `fingerprint` | `NbBikes`, `NbStandardBikes`, `NbEBikes`, `NbEmptyDocks`, `NbDocks`, `Installed`, `Locked` | Detects real changes |

Name, location, install and removal dates are reference data, not part of the fingerprint: a
reference table is refreshed from the checkpoint responses.

## 3. Storage layout and retention

| Strategy | Storage per year |
|---|---|
| Keep every poll, 120 s | ~20 GB |
| **Chosen: store changed stations only, raw kept once per hour** | **~0.7 GB raw + ~3 GB changes** |

Same contract as stop disruptions, except raw is never stored "on change": some station changes on
almost every poll, so that rule would keep every response. Raw is kept on `baseline` (first poll of a
session), `checkpoint` (first poll of each hour), `error` and `invalid`. Every poll writes a coverage
row with network totals (stations, bikes, e-bikes, empty and full stations, docks); one change row is
written per station whose fingerprint changed (about 118 per poll, ~90 bytes gzipped each, measured in
the first live test).

| Zone | Path |
|---|---|
| Raw | `raw/bikepoint/date=YYYY-MM-DD/hour=HH/bikepoint_{batch_start}.jsonl.gz` + manifest |
| Changes | `processed/bikepoint_changes/date=YYYY-MM-DD/bikepoint_changes_{batch_start}.jsonl.gz` |
| Coverage | `coverage/bikepoint/date=YYYY-MM-DD/coverage_{batch_start}.jsonl.gz` |

Change record columns: `poll_id`, `collected_at_utc`, `station_id`, `fingerprint`,
`previous_fingerprint`, `bikes`, `standard_bikes`, `e_bikes`, `empty_docks`, `docks`,
`out_of_use_docks` (derived), `installed`, `locked`, `modified_utc`, `change_type` (`BASELINE`,
`UPDATE`, `NEW_STATION`, `REMOVED`).

Running it: a separate systemd service (`tfl-bikepoint`) on the existing VM, isolated from the other
collectors.

## 4. Data-quality rules

All passed on the four calls of 2026-10-29.

| # | Rule | Level |
|---|---|---|
| P1 | HTTP 200 and body is a JSON array | ERROR |
| P2 | At least 700 stations (799 seen) | WARN |
| P3 | `id` unique within the poll | ERROR |
| B1 | `id` and the five count fields present and whole numbers ≥ 0 | Record excluded + WARN |
| B2 | `NbStandardBikes` + `NbEBikes` = `NbBikes` | WARN |
| B3 | `NbBikes` + `NbEmptyDocks` ≤ `NbDocks` | WARN |
| B4 | `lat`/`lon` inside Greater London | WARN |
| B5 | `modified` not in the future (5 min tolerance) | WARN |

## 5. Hot and cold use

**Hot (live "London Bikes" section):** tiles for bikes, e-bikes, empty and full stations, docks out of
use; a map of all stations coloured empty / full / OK. Called directly with the shared 30 s cache.

**Cold (BigQuery → Sheet → Tableau):**

| Table | Grain | Key columns |
|---|---|---|
| `bikepoint_station_hourly` | station × hour | minutes empty, minutes full, minutes observed, average bikes and e-bikes |
| `bikepoint_daily` | station × day | minutes empty, minutes full, peak-hour empty/full minutes, docks out of use |

| Business question | Method |
|---|---|
| Where and when can't people get or return a bike? | Minutes empty / full by station and hour |
| Where should rebalancing vans go, and when? | Ranking of empty-in-the-morning and full-in-the-evening stations |
| How many docks are out of use, and for how long? | Out-of-use docks over time |
| Are e-bikes spread evenly? | E-bike share by area |
