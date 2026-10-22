# Endpoint analysis card — Arrivals (rail modes)

Status: in progress (2026-10-22) · sections 1–5 drafted; streaming prototype running in Docker; not yet on the VM

| | |
|---|---|
| Endpoint | `GET /Mode/{mode}/Arrivals?count=-1`, one call per mode: tube, dlr, overground, elizabeth-line, tram |
| Refresh (measured) | New Tube snapshot every ~25–50 s; about 1 in 3 responses is **older** than one already received |
| Size | ~9,900 predictions per poll of all five modes, 6.3 MB JSON (0.42 MB gzip) |
| Collect every | To decide (30 s gives ~1.2 GB gzip a day if every poll were stored, so it won't be) |
| Evidence | Two polls of all modes 30 s apart, 2026-10-22 07:22 and 07:23 UTC (morning peak); Tube polled every 10 s for 3 min, 07:25–07:28 UTC |

## 1. Response structure

A flat JSON array, one object per prediction: "vehicle V on line L is expected at stop S at time T".

| Field | Example | Meaning |
|---|---|---|
| `id` | `682670490` | Prediction ID. **Not unique**: see below |
| `vehicleId` | `234` | Train number. **Empty on every DLR record** |
| `naptanId`, `stationName` | `940GZZLUNEN`, "North Ealing Underground Station" | Stop the prediction is for |
| `lineId`, `lineName` | `piccadilly` | |
| `platformName` | "Eastbound - Platform 2" | Free text per mode (DLR: "2") |
| `direction` | inbound / outbound | |
| `destinationNaptanId`, `destinationName` | Cockfosters | Missing on 224 of 4,510 Tube records |
| `currentLocation` | "At Platform", "Between X and Y" | Tube only; empty for other modes |
| `towards` | "Watford" | |
| `timestamp` | `2026-10-22T07:22:13.35Z` | When TfL made the prediction (event time) |
| `expectedArrival` | `2026-10-22T07:22:14Z` | The prediction |
| `timeToStation` | seconds | **Only present without `count=-1`**; derive as `expectedArrival − timestamp` |
| `timeToLive` | equals `expectedArrival` | Not useful |
| `timing` | `read`, `sent` | Tube only; mostly placeholder dates (`0001-01-01`) |

### The `count` parameter

Without it the endpoint returns **one prediction per stop** (260 for the whole Tube in the morning
peak). `count=-1` returns all of them: 4,510 for the Tube, 270 stops, up to 88 per stop.

| Mode | Predictions | Stops | Vehicles | JSON | gzip | Horizon p50 / max |
|---|---|---|---|---|---|---|
| tube | 4,510 | 270 | 465 | 3.00 MB | 0.24 MB | 12 / 30 min |
| overground | 3,065 | 113 | 255 | 1.89 MB | 0.11 MB | 52 / 119 min |
| elizabeth-line | 1,912 | 46 | 133 | 1.17 MB | 0.06 MB | 47 / 120 min |
| dlr | 272 | 45 | none | 0.16 MB | 0.01 MB | 6 / 26 min |
| tram | 164 | 38 | 18 | 0.09 MB | <0.01 MB | 13 / 34 min |

Overground and Elizabeth line predictions reach two hours ahead: mostly the timetable rather than
live tracking. Only about a third of them fall within 30 minutes.

### Findings

1. **`id` identifies a vehicle at a stop, not one visit.** 258 Tube ids appear twice in one
   response: the same train is predicted at the same stop twice, for example Bakerloo train 236 at
   Elephant & Castle at 07:23 (arriving, terminating) and 07:29 (departing north again). A visit key
   needs the expected time as well.
2. **Responses go back in time.** TfL serves this endpoint from several caches holding different
   snapshots. Over 19 Tube polls, the latest `timestamp` went 07:25:03, :05, :03, :33, :05, :03,
   :33, :05, 07:26:23 … Consumers must order snapshots by TfL's `timestamp` (event time) and drop a
   snapshot older than the last one accepted, never trust arrival order.
3. **DLR cannot be tracked by vehicle.** `vehicleId` is empty and only 45 of 272 ids survive
   between polls 30 s apart. DLR supports per-stop countdown questions only.
4. **Volume rules out storing every poll.** 0.42 MB gzip × 2,880 polls a day ≈ 1.2 GB a day,
   ~440 GB a year. Line status and stop disruptions store on change; nearly every arrival
   prediction changes every poll, so the stored form must be derived **events** (e.g. "train 234
   arrived at North Ealing at 07:23:05"), not snapshots.
5. **Processing is too heavy for the e2-micro.** Parsing ~10,000 records every 30 s, and keeping
   each vehicle's recent predictions in memory, needs more than the 1 GB VM can spare beside the two
   collectors (~470 MB in use). This is the workload that justifies Kafka on a separate machine.

## 2. Grain and identity

### Decisions (2026-10-22)

| Question | Decision | Why |
|---|---|---|
| What is stored | **Option C**: observed arrivals, plus prediction checkpoints for each arrival | Answers waits, bunching and prediction accuracy at a small fraction of storing every prediction (option A, ~1.2 GB/day) |
| Modes | **Tube and tram first**; Overground and Elizabeth line next; DLR last | Tube and tram have vehicle IDs and live (not timetable) predictions; DLR has no vehicle ID |
| Horizon | Predictions **within 30 minutes** of TfL's `timestamp` only | Beyond that it is mostly timetable; cuts Overground and Elizabeth line volume by about two thirds when they are added |

### Records

**Arrival** (one per vehicle per visit to a stop):

| Column | Meaning |
|---|---|
| `visit_key` | `line_id` + `vehicle_id` + `naptan_id` + visit number (a train can call at a stop twice: terminus reversals) |
| `line_id`, `vehicle_id`, `naptan_id`, `station_name`, `platform_name`, `direction`, `destination_naptan_id` | From the last prediction |
| `arrived_at` | Detected arrival time (rule in "Arrival detection") |
| `detection` | Which rule fired, so its accuracy can be audited |
| `last_snapshot_at` | Last TfL `timestamp` that still predicted this visit |

**Prediction checkpoint** (up to one per arrival per horizon): the prediction that was current
when the train was 10, 5 and 2 minutes away, by TfL's `timestamp`. Columns: `visit_key`,
`horizon_minutes`, `predicted_at` (TfL `timestamp`), `expected_arrival`. Error = `arrived_at −
expected_arrival`.

### Arrival detection

Decided 2026-10-22 (combination rule), from a 10-minute recording of Tube and tram in the morning
peak (07:33–07:43 UTC): 18 Tube and 10 tram snapshots accepted, **52 stale responses rejected**
(two in three responses were older than one already accepted). Accepted snapshots were a median
36 s apart for the Tube and 63 s for tram, which sets the timing resolution.

A visit is tracked by its earliest current prediction per (`line_id`, `vehicle_id`, `naptan_id`).
What happened when a visit's prediction disappeared between two accepted snapshots:

| Disappearance | Tube | Tram | Meaning |
|---|---|---|---|
| Within 90 s of `expectedArrival`, vehicle still in the feed | 1,647 (1,318 "At Platform") | 27 | **Arrival** |
| More than 90 s before `expectedArrival`, vehicle still in the feed | 83 | 47 | Re-planned (e.g. Hammersmith & City 430 lost Aldgate, Barbican, Farringdon while between Euston Square and King's Cross): **not an arrival** |
| The vehicle left the feed | 155 | 6 | Out of service or lost tracking: **not an arrival** |

Timing at the last sighting differs by mode: Tube predictions vanish a median **25 s after** the
expected time (p10 55 s after, p90 on time); tram predictions vanish a median **46 s before** it.

**Rule (combination):**

1. A visit counts as an arrival only if, when it disappears, the vehicle is still in the feed and
   the last prediction was within 90 s of `expectedArrival`.
2. `arrived_at`:
   - **Tube:** the TfL `timestamp` of the first snapshot where the visit showed `currentLocation`
     "At Platform"; if never seen, the last `expectedArrival`. `detection` = `at_platform` or
     `disappeared`.
   - **Tram:** the last `expectedArrival` (predictions vanish before arrival). `detection` =
     `disappeared`.
3. Disappearances that fail rule 1 are kept as `dropped` records with the reason (`replanned`,
   `vehicle_gone`), so the rejection rate can be monitored but never counted as service.

Precision: about ±30 s for the Tube (snapshot spacing), better where "At Platform" is seen. Fine
for headways and waits measured in minutes; prediction error below ~30 s is not meaningful.

## 3. Pipeline, storage layout and retention

### Why Kafka here

Line status and stop disruptions are small and change rarely: one process polls, compares with
the last poll and writes changes. Arrivals are different: ~4,500 Tube predictions change every
poll, the useful output (arrivals) has to be derived by keeping state across snapshots, and
several consumers need the same stream (arrival detection, the archive, later the live board and
MCP). Kafka decouples them: each step can fail, restart and be added without the others.

```
TfL ──► producer ──► [arrivals.predictions] ──► detector ──► [arrivals.events] ──► archiver ──► GCS
        filters         one message per line      LineTracker     arrival / dropped    hourly batches
                        per snapshot, key=line    per line        / snapshot events    commit after publish
```

| Step | Code | Does | Memory (measured, Docker) |
|---|---|---|---|
| Producer | `src/streaming/producer.py` | Polls Tube and tram **in parallel** every 15 s, keeps newer snapshots only, slims (30 min, trackable vehicles, 10 fields), publishes one message per line | ~32 MB |
| Kafka | `apache/kafka:4.0.0`, one KRaft node | Buffers both topics on disk | ~225 MB (heap 192 MB) |
| Detector | `src/streaming/detector.py` + `arrival_detection.py` | One `LineTracker` per line; emits arrival / dropped events | ~25 MB, ~4,200 visits tracked |
| Archiver | `src/streaming/archiver.py` + `archive.py` | Writes events to hourly batches with `BatchWriter`, publishes to GCS, then commits offsets | ~25 MB local; ~80 MB with the GCS library (as the collectors) |

Keys and order: every message is keyed by `line_id`, so Kafka delivers each line's snapshots
and events in order.

### Memory safety (the e2-micro has ~477 MB free beside the collectors)

| Risk | Default | Set to |
|---|---|---|
| Kafka log cleaner buffer on the heap | 128 MB (Kafka failed to start with a 192 MB heap) | 8 MB (only the small offsets topic is compacted) |
| Producer send queue if Kafka is down | 1 GB | 16 MB; a full queue skips the snapshot (the next replaces it) |
| Consumer pre-fetch | 64 MB per partition | 8 MB (detector), 4 MB (archiver) |
| Detector state | grows with visits | bounded: visits within 30 min, removed when they close |
| Kafka data | on disk | 2 h and 250 MB per partition, then deleted |

On the VM each process also gets a systemd `MemoryMax`, and the out-of-memory score is set so the
kernel stops Kafka before either collector.

### Delivery guarantee: at-least-once

The archiver commits a Kafka offset only after the batch holding that event is published to GCS
and read back. Events after the last publish sit in BatchWriter's fsynced tmp file; after a crash
`recover()` publishes them and Kafka redelivers them, so an event can be archived twice. Every
record carries `kafka_partition` and `kafka_offset`; BigQuery keeps one row per (partition,
offset). Kafka retention (2 h) must stay longer than the batch period (1 h).

### Volume (measured 2026-10-22, morning peak)

| Record | Rate | Compressed size | Per year (estimate) |
|---|---|---|---|
| Arrival (with up to 3 checkpoints) | ~90–250 per minute | 63 B | ~3.5 GB |
| Dropped visit | ~20–27 per 100 arrivals | 36 B | ~0.5 GB |

Snapshot lag (TfL `timestamp` to detector) was a median 14 s with parallel fetching, 122 s before.

### Paths

| Zone | Path | Contents |
|---|---|---|
| Events | `processed/arrival_events/date=YYYY-MM-DD/arrival_events_{batch_start}.jsonl.gz` + manifest | One row per arrival, nested `checkpoints` |
| Drops | `processed/arrival_drops/date=YYYY-MM-DD/arrival_drops_{batch_start}.jsonl.gz` + manifest | Visits that ended without an arrival, with `reason` |
| Coverage | `coverage/arrivals/date=YYYY-MM-DD/coverage_{batch_start}.jsonl.gz` | One row per processed line snapshot (`snapshot` event from the detector): `mode`, `line_id`, `snapshot_at`, `predictions`, `visits_tracked`, `arrivals`, `dropped`. Waits and rates use observed time only |
| Raw (7 days) | `raw/arrival_predictions/date=YYYY-MM-DD/hour=HH/arrival_predictions_{batch_start}.jsonl.gz` + manifest | Every accepted line snapshot of **slimmed** predictions (one message per line), ~350 MB/day. Kept 7 days by a GCS lifecycle rule on this prefix, so detection can be re-run on the last week. Written by `StreamBatchWriter` (compresses each record as it arrives; never holds the batch in memory). Decided 2026-10-22: an hourly checkpoint was rejected because it cannot rebuild arrivals (detection needs consecutive snapshots) |

Batch files are cut by `archived_at_utc` (processing time) so a late event never reopens a
published hour; event times (`arrived_at`, `last_snapshot_at`) are columns.

### Running it

Not yet on the VM. A Docker rehearsal (`docker/kafka_test/compose.yaml`) runs all four pieces
with memory limits that match the VM; `docker/kafka_test/measure.sh` records their memory.

### Retention

| Zone | Keep | Reason |
|---|---|---|
| Kafka topics | 2 h / 250 MB | A buffer, not the archive |
| `processed/arrival_events`, `processed/arrival_drops` | Indefinitely | ~4 GB a year |
| `coverage/arrivals` | Indefinitely | Proves observation time |
| `raw/arrival_predictions` | **7 days** (lifecycle rule) | Re-run detection on the last week; ~2.5 GB steady |
| `tmp/arrivals` | Until published | Recovered on restart |

## 4. Data-quality rules

Same levels as the other feeds: ERROR (nothing from this input is used), EXCLUDED (one record
skipped and counted), WARN (kept and flagged), CHECK (BigQuery, across records).

### Per snapshot (producer)

| # | Rule | Level |
|---|---|---|
| S1 | HTTP 200 and a JSON array | ERROR (counted as `errors`) |
| S2 | Snapshot `timestamp` newer than the last accepted for the mode | EXCLUDED (counted as `stale`; two in three responses in the morning peak) |
| S3 | Snapshot age (fetch time − TfL `timestamp`) under 5 minutes | WARN: TfL's feed has stalled |
| S4 | At least one prediction | WARN |

### Per prediction (producer)

| # | Rule | Level |
|---|---|---|
| R1 | `vehicleId` present and not all zeros ("000") | EXCLUDED: cannot be tracked |
| R2 | `expectedArrival` and `timestamp` parse | EXCLUDED |
| R3 | Lead time (`expectedArrival − timestamp`) within 30 minutes | EXCLUDED by design (horizon) |
| R4 | Lead time not below −5 minutes | WARN: a prediction for a train long overdue |

### Per event (detector)

| # | Rule | Level |
|---|---|---|
| E1 | Arrival only if the vehicle is still in the feed and the last prediction was within 90 s | Otherwise a `dropped` event with the reason |
| E2 | Checkpoints only for a horizon crossing actually observed | Built into the tracker (a visit first seen 4 min out has no 10- or 5-minute checkpoint) |
| E3 | Out-of-order snapshot for a line (older than the last processed) | EXCLUDED (counted as `out_of_order`) |

### Across records (BigQuery CHECK)

| # | Check | Expectation |
|---|---|---|
| C1 | One row per (`kafka_partition`, `kafka_offset`) | Duplicates only after a crash; dropped in the view |
| C2 | One arrival per `visit_key` | Duplicates mean a detection bug |
| C3 | Two arrivals of the same vehicle at the same stop less than 2 minutes apart | Should not happen (double detection) |
| C4 | Dropped visits / (arrivals + dropped) per line per hour | Measured 16–21% in the morning peak (18% replay, 21% in Docker); investigate above 35% |
| C5 | Headway under 30 s at the same platform | Suspect (two trains cannot share a platform) |
| C6 | Hours with arrivals but no coverage rows, or coverage with no arrivals during service | Pipeline fault |

## 5. Hot and cold use

### Hot path (live)

| Use | Source |
|---|---|
| Live board "next trains" per station | The TfL API directly (Streamlit), as now; no Kafka needed for a page view |
| Bunching right now: two trains on a line within a minute of each other | A Kafka consumer of `arrivals.events`, later served to MCP |
| "Is the Victoria line running regularly right now?" | Headways from the last 30 minutes of events (MCP live tool) |

### Cold path (BigQuery → Sheet → Tableau, MCP history tools)

| Metric | Definition | Why |
|---|---|---|
| Headway | Time between consecutive arrivals at the same stop, platform and line | The basic measure of service frequency |
| Average passenger wait | Σh² / (2 · Σh) over the headways h in a period | Passengers arriving at random wait longer when trains bunch; this is the measure TfL's own "excess wait time" builds on |
| Regularity | Coefficient of variation of headways (std / mean) | 0 = perfectly even; higher = irregular |
| Bunching rate | Share of headways under half the period's median | "Trains arriving in clumps" |
| Prediction error | `arrived_at − expected_arrival` at 10, 5 and 2 minutes: mean (bias) and median absolute error | How far off TfL's countdowns are, by line and horizon |
| Observed time | From coverage rows | All rates use only the time we were watching |

### Business questions (from docs/requirements.md)

| Question | Metric | Method |
|---|---|---|
| What is the typical wait at a station, and how variable is it? | Average passenger wait, headway distribution | Descriptive statistics by station, line, hour of day |
| Do trains arrive in clumps instead of evenly? | Bunching rate, regularity | By line and hour; compare peaks with off-peak |
| How far off are TfL's countdown predictions, by line? | Prediction error by horizon | Bias and spread; does accuracy drop during disruptions (join line status)? |
| Do disruptions show up in waits before the status changes? | Headways vs line status periods | Join on line and time |

### Open items

- GCS lifecycle rule (delete `raw/arrival_predictions/` after 7 days): set when deploying, with approval.
- Verified 2026-10-22: a graceful archiver restart published 3,441 arrivals and 927 drops and
  committed offset 4,368, exactly the events in the topic (no loss, no duplicate).
- Overground and Elizabeth line, then DLR (per-stop countdown only).
- VM deployment: systemd units with memory limits; 24-hour watch of memory.
