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

To do with the user. Questions:

- What is one stored record: a prediction, a vehicle's visit to a stop, or an observed arrival?
- How do we decide a train has **arrived** (prediction drops to ~0 and disappears; `currentLocation`
  "At Platform"; the vehicle's next stop changes)?
- Which modes and horizon: all five, or Tube first; all predictions, or only those within 30 min?
