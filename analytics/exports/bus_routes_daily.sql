-- Google Sheet tab `bus_routes_daily` (Tableau Public): the busiest 12 bus routes the VM
-- follows (config/arrivals.json), per route, direction, completed London day and time of day.
-- Source: tfl_portfolio.arrival_stop_hourly (one row per route, stop and hour). Its stop_side
-- is the stop letter for buses, not the direction, so each stop's direction comes from the
-- arrival events, and where that direction heads from the route reference.
--
-- Combined exactly from the hourly rows, using headway counts as weights:
--   total headway time  = mean_headway * headways
--   average wait        = sum(h^2) / (2 sum(h)), and average_wait * 2 * sum(h) = sum(h^2)
--   bunching rate       = bunched headways / headways
-- Time-of-day bands are London time. The last 6 months (the tab's window).
WITH events AS (
  SELECT line_id, naptan_id, direction
  FROM `transport-for-london-511318.tfl_transport_analytics.arrival_events`
  WHERE source_date >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 6 MONTH) AND mode = 'bus'
),
stop_direction AS (
  SELECT line_id, naptan_id, APPROX_TOP_COUNT(direction, 1)[OFFSET(0)].value AS direction
  FROM events
  GROUP BY line_id, naptan_id
),
towards AS (
  -- Where each direction heads: the last stop of its longest branch in the route reference
  -- (TfL leaves the destination empty in bus arrivals).
  SELECT line_id, direction, stop_name AS towards
  FROM (
    SELECT *, COUNT(*) OVER (PARTITION BY line_id, direction, branch_id) AS branch_stops
    FROM `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
    WHERE collection_run_id = (SELECT MAX(collection_run_id)
                               FROM `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`)
  )
  QUALIFY ROW_NUMBER() OVER (PARTITION BY line_id, direction ORDER BY branch_stops DESC, stop_order DESC) = 1
),
hourly AS (
  SELECT
    DATE(h.hour_start, 'Europe/London') AS day,
    h.line_id AS route,
    sd.direction,
    EXTRACT(HOUR FROM DATETIME(h.hour_start, 'Europe/London')) AS london_hour,
    h.naptan_id, h.headways, h.mean_headway_seconds, h.average_wait_seconds, h.bunched_headways
  FROM `transport-for-london-511318.tfl_portfolio.arrival_stop_hourly` AS h
  JOIN stop_direction AS sd USING (line_id, naptan_id)
  WHERE h.mode = 'bus'
    AND DATE(h.hour_start, 'Europe/London') < CURRENT_DATE('Europe/London')
    AND DATE(h.hour_start, 'Europe/London') >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 6 MONTH)
    AND h.headways > 0
)
SELECT
  hourly.day,
  hourly.route,
  hourly.direction,
  ANY_VALUE(t.towards) AS towards,
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
  ROUND(SUM(mean_headway_seconds * headways) / SUM(headways) / 60, 2) AS mean_gap_minutes,
  ROUND(SUM(average_wait_seconds * mean_headway_seconds * headways)
        / SUM(mean_headway_seconds * headways) / 60, 2) AS average_wait_minutes,
  ROUND(SUM(bunched_headways) / SUM(headways), 4) AS bunching_rate
FROM hourly
LEFT JOIN towards AS t ON LOWER(t.line_id) = LOWER(hourly.route) AND t.direction = hourly.direction
GROUP BY hourly.day, hourly.route, hourly.direction, time_band
ORDER BY hourly.day, hourly.route, hourly.direction, time_band
