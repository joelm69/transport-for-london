-- Google Sheet tab: bus_routes_daily
-- All 675 TfL bus routes, per route, direction, completed London day and time of day.
-- Sheet data starts 26 October 2026, 15:00 London time; the last 30 days are kept in the Sheet
-- (BigQuery keeps everything). Only routes that ran: no placeholder rows for routes not seen.
--
-- Source: the network-wide bus poll every 30 minutes (scripts/bus_network_snapshot.py, external
-- table bus_network_routes): per route and direction, the buses running and the predicted gaps
-- between successive buses at each stop (buses due within 30 minutes). Per day and time band:
--   buses          average number of buses running at a poll
--   typical gap    gap-weighted average of each poll's median gap
--   bunching rate  share of gaps under half that poll's median gap
--   longest gap    the longest predicted gap seen
--   average wait   for a passenger arriving at random: sum(gap^2) / (2 sum(gap)), exact
--   median wait    the wait half of such passengers beat (typical across the day's polls)
-- Time-of-day bands are London time.

WITH params AS (
  SELECT
    TIMESTAMP('2026-10-26 15:00:00', 'Europe/London') AS sheet_start,
    GREATEST(
      DATE('2026-10-26'),
      DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 30 DAY)
    ) AS window_start
),

latest_route_reference AS (
  SELECT *
  FROM `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
  WHERE collection_run_id = (
    SELECT MAX(collection_run_id)
    FROM `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
  )
),

towards AS (
  -- Where each direction heads: the last stop of its longest branch.
  SELECT LOWER(line_id) AS route, direction, stop_name AS towards
  FROM (
    SELECT *, COUNT(*) OVER (PARTITION BY line_id, direction, branch_id) AS branch_stops
    FROM latest_route_reference
  )
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY line_id, direction ORDER BY branch_stops DESC, stop_order DESC
  ) = 1
),

polls AS (
  SELECT
    DATE(r.snapshot_at, 'Europe/London') AS day,
    LOWER(r.line_id) AS route,
    r.direction,
    EXTRACT(HOUR FROM DATETIME(r.snapshot_at, 'Europe/London')) AS london_hour,
    r.buses,
    r.gaps,
    r.median_gap_seconds,
    r.max_gap_seconds,
    r.bunched_gaps,
    r.gap_seconds_sum,
    r.gap_seconds_sq_sum,
    r.median_wait_seconds
  FROM `transport-for-london-511318.tfl_transport_analytics.bus_network_routes` AS r
  CROSS JOIN params AS p
  WHERE
    -- `date` is the poll's UTC folder: the filter reads only those days' files.
    r.date >= DATE_SUB(p.window_start, INTERVAL 1 DAY)
    AND r.snapshot_at >= p.sheet_start
    AND DATE(r.snapshot_at, 'Europe/London') >= p.window_start
    AND DATE(r.snapshot_at, 'Europe/London') < CURRENT_DATE('Europe/London')
),

daily_metrics AS (
  SELECT
    day,
    route,
    direction,
    CASE
      WHEN london_hour < 5 THEN '1 Night (00-05)'
      WHEN london_hour < 7 THEN '2 Early (05-07)'
      WHEN london_hour < 10 THEN '3 Morning peak (07-10)'
      WHEN london_hour < 16 THEN '4 Midday (10-16)'
      WHEN london_hour < 19 THEN '5 Evening peak (16-19)'
      ELSE '6 Evening (19-24)'
    END AS time_band,
    COUNT(*) AS polls,
    ROUND(AVG(buses), 1) AS buses,
    ROUND(SAFE_DIVIDE(SUM(median_gap_seconds * gaps), SUM(gaps)) / 60, 2) AS typical_gap_minutes,
    ROUND(SAFE_DIVIDE(SUM(bunched_gaps), SUM(gaps)), 4) AS bunching_rate,
    ROUND(MAX(max_gap_seconds) / 60, 1) AS longest_gap_minutes,
    -- Passenger waits (polls from 2026-10-27; null before): the average is exact across polls,
    -- the median is the gap-weighted average of each poll's median wait (a typical value).
    ROUND(SAFE_DIVIDE(SUM(gap_seconds_sq_sum), 2 * SUM(gap_seconds_sum)) / 60, 2) AS average_wait_minutes,
    ROUND(SAFE_DIVIDE(SUM(median_wait_seconds * gap_seconds_sum),
                      SUM(IF(median_wait_seconds IS NULL, NULL, gap_seconds_sum))) / 60, 2) AS median_wait_minutes
  FROM polls
  GROUP BY day, route, direction, time_band
)

SELECT
  d.day,
  UPPER(d.route) AS route,
  d.direction,
  t.towards,
  d.time_band,
  d.polls,
  d.buses,
  d.typical_gap_minutes,
  d.bunching_rate,
  d.longest_gap_minutes,
  d.average_wait_minutes,
  d.median_wait_minutes
FROM daily_metrics AS d
LEFT JOIN towards AS t
  ON t.route = d.route
  AND t.direction = d.direction
ORDER BY route, day, direction, time_band;
