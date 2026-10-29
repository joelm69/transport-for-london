# Endpoint analysis card — Roads (disruptions and corridor status)

Status: collector built (2026-10-29): `src/collectors/road.py`, systemd service `tfl-road` (`deploy/tfl-road.service`) · live panel: `src/presentation/road_live.py`

| | |
|---|---|
| Endpoints | `GET /Road/all/Disruption` (works and live incidents) · `GET /Road/all/Status` (24 main road corridors) |
| Refresh (measured) | TfL `max-age` 150 s for both; in 5 minutes 1 disruption cleared and 2 were updated, no corridor changed |
| Size | Disruptions ~0.3 MB (137 records, ~1.4 s); corridors ~13 KB (24 records) |
| Collect every | 300 s (both feeds in one poll) |
| Evidence | Calls on 2026-10-29 between 17:50 and 18:35 UTC |

## 1. Response structure

### Disruptions (one object per disruption)

| Field | Values seen (2026-10-29) | Use |
|---|---|---|
| `id` | `TIMS-237243` | Identity (unique) |
| `category` | Works 127, Collisions 3, Emergency service incidents 2, Breakdowns 2, Asset issues 2, Hazards 1 | Works vs live incident |
| `subCategory` | Utility works 86, TfL works 22, Borough works 12, Vehicle collision 3, Police activity 2, … | Detail |
| `severity` | Minimal 108, Moderate 15, Serious 7, No impact 5, Severe 2 | Heat-map weight, metrics |
| `levelOfInterest` | Low 74, Medium 45, High 18 | Kept |
| `point` | `"[-0.0595,51.6567]"` (text, lon first) | Location; present on all 137 |
| `corridorIds` | e.g. `["a10"]`; empty on 71 (local streets) | Main road affected |
| `location`, `comments`, `currentUpdate` | free text | Shown on hover; fingerprinted |
| `startDateTime`, `endDateTime` | ISO UTC | Planned window |
| `lastModifiedTime`, `currentUpdateDateTime` | ISO UTC | Not fingerprinted (move without visible change) |
| `streets[].closure` | 26 disruptions with a closed street section | Closures |

**Quirks.** `hasClosures` was **false on all 137**, including the 26 with closed street sections:
closures are read from `streets` instead. `status` is only filled when `stripContent` is off.

### Corridors (one object per main road)

`id`, `displayName`, `statusSeverity` (Good 15, Serious 9 at 17:50 UTC),
`statusSeverityDescription` ("No Exceptional Delays", "Serious"), aggregation window.

## 2. Grain, identity and storage

| Stream | Key | Fingerprint | Change types |
|---|---|---|---|
| `road_disruption_changes` | `disruption_id` | category, sub-category, severity, location, comments, current update, start, end, corridors, closed streets | BASELINE, NEW, UPDATE, CLEARED |
| `road_corridor_changes` | `corridor_id` | status, description | BASELINE, NEW, UPDATE, REMOVED |

Raw responses (both feeds in one envelope) are kept at `baseline`, the first poll of each hour
(`checkpoint`), `error` and `invalid`: some disruption is updated on most polls, so storing on
change would keep nearly every response. Every poll writes a coverage row (validity of each
feed, disruption, incident and corridor counts, changes written).

| Zone | Path |
|---|---|
| Raw | `raw/road/date=YYYY-MM-DD/hour=HH/road_{batch_start}.jsonl.gz` |
| Changes | `processed/road_disruption_changes/…`, `processed/road_corridor_changes/…` |
| Coverage | `coverage/road/date=YYYY-MM-DD/coverage_{batch_start}.jsonl.gz` |

Volume (first live test): baseline 137 disruption rows in 21 KB gzipped; raw checkpoint 42 KB.
About 1 GB a year raw, well under that for changes. Peak memory 38 MB.

## 3. Data-quality rules

Each feed is validated on its own: a failure in one never stops the other.

| # | Rule | Level |
|---|---|---|
| P1 | Disruption response is a JSON array | ERROR (disruptions) |
| P2 | At least one disruption | WARN |
| P3 | Disruption `id` unique | ERROR (disruptions) |
| R1 | Disruption has an `id` | Record excluded |
| R2 | `severity` is a known value | WARN |
| R3 | `startDateTime` parses | WARN |
| R4 | `point` parses and lies in Greater London | WARN |
| C1 | Corridor response is a non-empty array | ERROR (corridors) |
| C2 | Corridor `id` present and unique | ERROR (corridors) |
| C3 | `statusSeverity` is a known value | WARN |

## 4. Hot and cold use

**Hot (live, portfolio "London Bikes and Roads"):** road tiles, live incidents on the map, a heat
map of all disruptions weighted by severity. Called directly with the shared 150 s cache.

**Cold (BigQuery → Sheet → Tableau):**

| Business question | Measure |
|---|---|
| Which main roads spend most time at Serious, and when? | Corridor minutes by status, per day and hour |
| How long do incidents take to clear, by type? | First seen → CLEARED (censored while open) |
| Do roadworks finish when planned? | Observed clear time − `endDateTime` |
| Where do disruptions cluster? | Disruption-minutes by area and corridor |
