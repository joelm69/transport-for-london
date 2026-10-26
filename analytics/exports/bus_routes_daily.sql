-- Google Sheet tab: bus_routes_daily
-- All 675 TfL bus routes.
-- Historical metrics from 24 October 2026, 19:00 London time onward.
-- Historical data accumulates; no rolling six-month limit.
-- Routes without collected performance observations remain included with NULL metrics.
--
-- Metrics are measured headways at stops, from tfl_portfolio.arrival_stop_hourly (the 12 routes
-- the VM follows, config/arrivals.json). Its stop_side is the stop letter for buses, not the
-- direction, so each stop's direction comes from the arrival events and where that direction
-- heads from the route reference. Combined exactly from the hourly rows, using headway counts as
-- weights. Time-of-day bands are London time; only completed London days.

WITH params AS (
  SELECT TIMESTAMP('2026-10-24 19:00:00', 'Europe/London') AS project_start
),

route_reference AS (
  SELECT DISTINCT
    LOWER(line_id) AS route
  FROM
    `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
  WHERE
    collection_run_id = (
      SELECT MAX(collection_run_id)
      FROM
        `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
    )
),

towards AS (
  SELECT
    LOWER(line_id) AS route,
    direction,
    stop_name AS towards
  FROM (
    SELECT
      *,
      COUNT(*) OVER (
        PARTITION BY LOWER(line_id), direction, branch_id
      ) AS branch_stops
    FROM
      `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
    WHERE
      collection_run_id = (
        SELECT MAX(collection_run_id)
        FROM
          `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
      )
  )
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY LOWER(line_id), direction
    ORDER BY branch_stops DESC, stop_order DESC
  ) = 1
),

events AS (
  SELECT
    LOWER(e.line_id) AS route,
    e.naptan_id,
    e.direction
  FROM
    `transport-for-london-511318.tfl_transport_analytics.arrival_events` AS e,
    params AS p
  WHERE
    -- source_date is the partition column: filtering on it keeps the scan to the days needed.
    e.source_date >= DATE('2026-10-24')
    AND e.mode = 'bus'
    AND e.last_snapshot_at >= p.project_start
),

stop_direction AS (
  SELECT
    route,
    naptan_id,
    APPROX_TOP_COUNT(direction, 1)[OFFSET(0)].value AS direction
  FROM events
  GROUP BY route, naptan_id
),

hourly AS (
  SELECT
    DATE(h.hour_start, 'Europe/London') AS day,
    LOWER(h.line_id) AS route,
    sd.direction,
    EXTRACT(
      HOUR FROM DATETIME(h.hour_start, 'Europe/London')
    ) AS london_hour,
    h.naptan_id,
    h.headways,
    h.mean_headway_seconds,
    h.average_wait_seconds,
    h.bunched_headways
  FROM
    `transport-for-london-511318.tfl_portfolio.arrival_stop_hourly` AS h
  JOIN stop_direction AS sd
    ON LOWER(h.line_id) = sd.route
    AND h.naptan_id = sd.naptan_id
  CROSS JOIN params AS p
  WHERE
    h.mode = 'bus'
    AND h.hour_start >= p.project_start
    AND DATE(h.hour_start, 'Europe/London')
        < CURRENT_DATE('Europe/London')
    AND h.headways > 0
),

daily_metrics AS (
  SELECT
    hourly.day,
    hourly.route,
    hourly.direction,
    CASE
      WHEN london_hour < 5 THEN '1 Night (00-05)'
      WHEN london_hour < 7 THEN '2 Early (05-07)'
      WHEN london_hour < 10 THEN '3 Morning peak (07-10)'
      WHEN london_hour < 16 THEN '4 Midday (10-16)'
      WHEN london_hour < 19 THEN '5 Evening peak (16-19)'
      ELSE '6 Evening (19-24)'
    END AS time_band,
    COUNT(DISTINCT naptan_id) AS stops,
    SUM(headways) AS headways,
    ROUND(
      SAFE_DIVIDE(
        SUM(mean_headway_seconds * headways),
        SUM(headways)
      ) / 60,
      2
    ) AS mean_gap_minutes,
    ROUND(
      SAFE_DIVIDE(
        SUM(
          average_wait_seconds
          * mean_headway_seconds
          * headways
        ),
        SUM(mean_headway_seconds * headways)
      ) / 60,
      2
    ) AS average_wait_minutes,
    ROUND(
      SAFE_DIVIDE(SUM(bunched_headways), SUM(headways)),
      4
    ) AS bunching_rate
  FROM hourly
  GROUP BY
    hourly.day,
    hourly.route,
    hourly.direction,
    time_band
),

observed_output AS (
  SELECT
    d.day,
    d.route,
    d.direction,
    t.towards,
    d.time_band,
    d.stops,
    d.headways,
    d.mean_gap_minutes,
    d.average_wait_minutes,
    d.bunching_rate
  FROM daily_metrics AS d
  LEFT JOIN towards AS t
    ON t.route = d.route
    AND t.direction = d.direction
),

routes_without_observations AS (
  SELECT
    CAST(NULL AS DATE) AS day,
    r.route,
    CAST(NULL AS STRING) AS direction,
    CAST(NULL AS STRING) AS towards,
    'No historical observations' AS time_band,
    CAST(NULL AS INT64) AS stops,
    CAST(NULL AS INT64) AS headways,
    CAST(NULL AS FLOAT64) AS mean_gap_minutes,
    CAST(NULL AS FLOAT64) AS average_wait_minutes,
    CAST(NULL AS FLOAT64) AS bunching_rate
  FROM route_reference AS r
  WHERE NOT EXISTS (
    SELECT 1
    FROM daily_metrics AS d
    WHERE d.route = r.route
  )
)

SELECT *
FROM observed_output

UNION ALL

SELECT *
FROM routes_without_observations

ORDER BY
  route,
  day,
  direction,
  time_band;
