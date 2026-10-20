
# TfL Endpoint Inventory

Base URL: https://api.tfl.gov.uk

Status values:
- Identified: path found during API exploration
- Tested: request successfully executed
- Inspected: response structure reviewed
- Approved: selected for the project

| # | Method | Endpoint path | Data domain | Status |
|---:|---|---|---|---|
| 1 | GET | `/AirQuality/` | Air quality | Identified |
| 2 | GET | `/jp_public/api10/XML_STOPSTRUCTURE_REQUEST?sSStopNr={sSStopNr}` | Stop structure (XML) | Identified |
| 3 | GET | `/BikePoint/` | Cycle hire | Identified |
| 4 | GET | `/crowding/{Naptan}/{DayOfWeek}` | Passenger crowding | Identified |
| 5 | GET | `/Journey` | Journey planning | Identified |
| 6 | GET | `/Disruptions/Lifts/v2/` | Lift disruptions | Identified |
| 7 | GET | `/Line` | Lines and services | Identified |
| 8 | GET | `/Mode/{mode}/Arrivals` | Arrival predictions | Identified |
| 9 | GET | `/NetworkStatus` | Network status | Identified |
| 10 | GET | `/Occupancy` | Occupancy | Identified |
| 11 | GET | `/Place` | Places and locations | Identified |
| 12 | GET | `/Road/{ids}/Disruption` | Road disruptions | Identified |

## Notes

- This is an initial list, not a complete API catalogue.
- Record required parameters and authentication requirements when verified.
- Keep the XML stop-structure feed separate from the Unified API endpoints until its source and format are confirmed.
- Do not collect data until the response structure and record identity are understood.


## Initial API observations

### Air quality — GET /AirQuality/

- Request status: HTTP 200
- Authentication: request succeeded using the configured API key
- Response format: JSON
- Update period reported by API: hourly
- Observed fields:
  - `updatePeriod`
  - `updateFrequency`
  - `currentForecast`
  - `publishedDate`
  - `fromDate`
  - `forecastBand`
  - `nO2Band`, `o3Band`, `pM10Band`, `pM25Band`, `sO2Band`
- Data-quality observation: the returned `publishedDate` was in December 2026, while the forecast summary described October 12–14, 2026.
- Next action: investigate timestamp consistency before approving this endpoint for historical analytics.


### Network status — GET /NetworkStatus

- Request status: HTTP 200 (assuming the preceding command printed this status)
- Response format: JSON object
- Top-level fields: `$type`, `operator`, `status`, `message`, `statusLevel`
- Observed values:
  - `operator`: `TfL`
  - `status`: `Normal`
  - `message`: empty string
  - `statusLevel`: `1`
- Potential use: monitor changes in overall network status over time.
- Data-quality considerations:
  - Verify the documented meaning and range of `statusLevel`.
  - Record `fetched_at_utc` for each snapshot.
  - Avoid storing identical snapshots as new status-change events.
- Approval status: pending further assessment.


### Tube line status — GET /Line/Mode/tube/Status

- Request status: HTTP 200
- Response format: JSON array
- Records returned: 11
- Key fields observed:
  - `id`, `name`, `modeName`
  - `lineStatuses[].statusSeverity`
  - `lineStatuses[].statusSeverityDescription`
  - `lineStatuses[].reason`
  - `lineStatuses[].validityPeriods`
  - `disruptions`
- Example observations:
  - Bakerloo: Good Service
  - Central: Minor Delays
- Potential use:
  - Count lines by service-status category.
  - Track changes in line status over time.
  - Analyse disruption reasons and their reported validity periods.
- Data-quality rules:
  - Record `fetched_at_utc` separately from source timestamps.
  - Handle multiple status records per line.
  - Do not assume every line has a disruption or validity period.
- Approval status: candidate for historical monitoring.


### Tube arrivals — GET /Mode/tube/Arrivals

- Request status: HTTP 200
- Response format: JSON array
- Records returned in test: 1,217
- Key fields observed:
  - `id`, `vehicleId`
  - `naptanId`, `stationName`
  - `lineId`, `lineName`
  - `destinationName`, `direction`, `platformName`
  - `timestamp`, `timeToStation`, `expectedArrival`
  - `currentLocation`, `timing`
- Potential analyses:
  - Distribution of predicted waiting times.
  - Arrival frequency by station and line.
  - Changes in predictions between successive observations.
  - Comparison of predicted arrival time with actual arrival, if reliable actual-arrival ground truth becomes available.
- Data-quality rules:
  - Record `fetched_at_utc` for each poll.
  - Convert timestamps to UTC consistently.
  - Investigate the stability and uniqueness of `id` before using it as a deduplication key.
  - Preserve successive observations when studying prediction changes.
  - Test empty responses, missing fields, invalid timestamps, and API errors.
- Approval status: candidate for high-frequency monitoring, subject to payload size and cost tests.