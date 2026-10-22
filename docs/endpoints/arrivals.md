# Endpoint analysis card — Arrivals (rail modes)

Status: in progress (2026-10-22) · step 1 (response structure) done; next: grain and identity

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
