-- BikePoint views: sessions -> station periods -> station hours -> station days.
-- Definitions follow docs/endpoints/bikepoint.md (section 5). Manual and test runs excluded:
-- only the VM's continuous collection counts.

-- 1. Collection sessions -------------------------------------------------------------------
-- A new session starts after a gap above 300 s (2.5 polls at 120 s) OR when the collector
-- restarts (its first poll is a 'baseline'): after a restart it forgets what it saw, so counts
-- that changed during the restart would otherwise stretch across it.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.bikepoint_sessions` AS
WITH polls AS (
  SELECT
    TIMESTAMP(collected_at_utc) AS polled_at,
    raw_reason = 'baseline' AS is_restart,
    LAG(TIMESTAMP(collected_at_utc)) OVER (ORDER BY TIMESTAMP(collected_at_utc)) AS previous_poll_at
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_bikepoint_coverage`
  WHERE valid AND collection_window = 'continuous'
),
numbered AS (
  SELECT
    polled_at,
    COUNTIF(
      previous_poll_at IS NULL
      OR is_restart
      OR TIMESTAMP_DIFF(polled_at, previous_poll_at, SECOND) > 300
    ) OVER (ORDER BY polled_at) AS session_no
  FROM polls
)
SELECT
  session_no,
  MIN(polled_at) AS session_start,
  TIMESTAMP_ADD(MAX(polled_at), INTERVAL 120 SECOND) AS session_end,  -- last poll covers 120 s
  COUNT(*) AS polls
FROM numbered
GROUP BY session_no;


-- 2. Station periods -----------------------------------------------------------------------
-- Each BASELINE / NEW_STATION / UPDATE row starts a period of a station's counts; it lasts
-- until the station's next change or the end of the session. State: empty (no bike to take),
-- full (nowhere to return one), ok.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.bikepoint_periods` AS
WITH changes AS (
  SELECT *, TIMESTAMP(collected_at_utc) AS changed_at
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_bikepoint_changes`
  WHERE collection_window = 'continuous'
),
in_session AS (
  SELECT c.*, s.session_no, s.session_end
  FROM changes AS c
  JOIN `transport-for-london-511318.tfl_transport_analytics.bikepoint_sessions` AS s
    ON c.changed_at >= s.session_start AND c.changed_at < s.session_end
),
ordered AS (
  SELECT
    *,
    LEAD(changed_at) OVER (PARTITION BY station_id, session_no ORDER BY changed_at) AS next_change_at
  FROM in_session
)
SELECT
  station_id,
  station_name,
  lat,
  lon,
  session_no,
  changed_at AS period_start,
  LEAST(COALESCE(next_change_at, session_end), session_end) AS period_end,
  change_type,
  bikes,
  standard_bikes,
  e_bikes,
  empty_docks,
  docks,
  out_of_use_docks,
  CASE WHEN bikes = 0 THEN 'empty' WHEN empty_docks = 0 THEN 'full' ELSE 'ok' END AS state,
  installed,
  locked
FROM ordered
WHERE change_type != 'REMOVED';


-- 3. Station hours -------------------------------------------------------------------------
-- Periods split at hour boundaries. Averages are time-weighted (counts × seconds held).

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.bikepoint_station_hourly` AS
WITH pieces AS (
  SELECT
    p.*,
    hour_start,
    TIMESTAMP_DIFF(
      LEAST(p.period_end, TIMESTAMP_ADD(hour_start, INTERVAL 1 HOUR)),
      GREATEST(p.period_start, hour_start),
      SECOND
    ) AS seconds
  FROM `transport-for-london-511318.tfl_transport_analytics.bikepoint_periods` AS p,
  UNNEST(GENERATE_TIMESTAMP_ARRAY(
    TIMESTAMP_TRUNC(p.period_start, HOUR),
    TIMESTAMP_TRUNC(TIMESTAMP_SUB(p.period_end, INTERVAL 1 MICROSECOND), HOUR),
    INTERVAL 1 HOUR
  )) AS hour_start
)
SELECT
  station_id,
  ANY_VALUE(station_name) AS station_name,
  ANY_VALUE(lat) AS lat,
  ANY_VALUE(lon) AS lon,
  hour_start,
  MAX(docks) AS docks,
  SUM(seconds) / 60 AS observed_minutes,
  SUM(IF(state = 'empty', seconds, 0)) / 60 AS empty_minutes,
  SUM(IF(state = 'full', seconds, 0)) / 60 AS full_minutes,
  SAFE_DIVIDE(SUM(bikes * seconds), SUM(seconds)) AS avg_bikes,
  SAFE_DIVIDE(SUM(e_bikes * seconds), SUM(seconds)) AS avg_e_bikes,
  SAFE_DIVIDE(SUM(out_of_use_docks * seconds), SUM(seconds)) AS avg_out_of_use_docks
FROM pieces
WHERE seconds > 0
GROUP BY station_id, hour_start;


-- 4. Station days (London calendar days) ---------------------------------------------------
-- Peak hours are weekdays 07:00-10:00 and 16:00-19:00 London time, when commuters need a
-- bike or a dock most.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.bikepoint_station_daily` AS
WITH hours AS (
  SELECT
    *,
    DATE(hour_start, 'Europe/London') AS london_date,
    EXTRACT(DAYOFWEEK FROM DATETIME(hour_start, 'Europe/London')) NOT IN (1, 7)
      AND EXTRACT(HOUR FROM DATETIME(hour_start, 'Europe/London')) IN (7, 8, 9, 16, 17, 18) AS is_peak
  FROM `transport-for-london-511318.tfl_transport_analytics.bikepoint_station_hourly`
)
SELECT
  station_id,
  ARRAY_AGG(station_name ORDER BY hour_start DESC LIMIT 1)[OFFSET(0)] AS station_name,
  ANY_VALUE(lat) AS lat,
  ANY_VALUE(lon) AS lon,
  london_date,
  MAX(docks) AS docks,
  SUM(observed_minutes) AS observed_minutes,
  SUM(empty_minutes) AS empty_minutes,
  SUM(full_minutes) AS full_minutes,
  SUM(IF(is_peak, observed_minutes, 0)) AS peak_observed_minutes,
  SUM(IF(is_peak, empty_minutes, 0)) AS peak_empty_minutes,
  SUM(IF(is_peak, full_minutes, 0)) AS peak_full_minutes,
  SAFE_DIVIDE(SUM(avg_bikes * observed_minutes), SUM(observed_minutes)) AS avg_bikes,
  SAFE_DIVIDE(SUM(avg_e_bikes * observed_minutes), SUM(observed_minutes)) AS avg_e_bikes,
  SAFE_DIVIDE(SUM(avg_out_of_use_docks * observed_minutes), SUM(observed_minutes)) AS avg_out_of_use_docks
FROM hours
GROUP BY station_id, london_date;


-- 5. Network hours, from every poll's totals (coverage) ------------------------------------

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.bikepoint_network_hourly` AS
SELECT
  TIMESTAMP_TRUNC(TIMESTAMP(collected_at_utc), HOUR) AS hour_start,
  COUNT(*) AS polls,
  AVG(stations) AS avg_stations,
  AVG(bikes) AS avg_bikes,
  AVG(e_bikes) AS avg_e_bikes,
  AVG(empty_stations) AS avg_empty_stations,
  AVG(full_stations) AS avg_full_stations,
  MAX(empty_stations) AS max_empty_stations,
  MAX(full_stations) AS max_full_stations,
  AVG(docks - bikes - empty_docks) AS avg_out_of_use_docks,
  AVG(docks) AS avg_docks
FROM `transport-for-london-511318.tfl_transport_analytics.ext_bikepoint_coverage`
WHERE valid AND collection_window = 'continuous'
GROUP BY hour_start;
