-- Google Sheet tab: bus_vehicle_routes_daily
-- Where each bus went: one row per bus (registration), route and direction per completed London
-- day, on all 675 bus routes. A detail tab, so the Sheet keeps only the last 7 days (BigQuery
-- keeps everything). Sheet data starts 26 October 2026, 15:00 London time.
--
-- Source and distance rules: as bus_vehicles_daily.sql (same `moves` and `counted` legs); each leg counts
-- for the route and direction the bus was on at the later poll.

WITH params AS (
  SELECT
    TIMESTAMP('2026-10-26 15:00:00', 'Europe/London') AS sheet_start,
    GREATEST(
      DATE('2026-10-26'),
      DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 7 DAY)
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

branch_km AS (
  -- Distance along each branch from its first stop, summed stop to stop.
  SELECT
    LOWER(line_id) AS route,
    direction,
    branch_id,
    stop_id,
    stop_name,
    COUNT(*) OVER (PARTITION BY line_id, direction, branch_id) AS branch_stops,
    SUM(seg_km) OVER (PARTITION BY line_id, direction, branch_id ORDER BY stop_order) AS km_from_start,
    SUM(seg_km) OVER (PARTITION BY line_id, direction, branch_id) AS route_km,
    ROW_NUMBER() OVER (PARTITION BY line_id, direction, branch_id ORDER BY stop_order DESC) = 1 AS is_last
  FROM (
    SELECT *,
      IFNULL(ST_DISTANCE(
        LAG(ST_GEOGPOINT(longitude, latitude)) OVER (PARTITION BY line_id, direction, branch_id ORDER BY stop_order),
        ST_GEOGPOINT(longitude, latitude)) / 1000, 0) AS seg_km
    FROM latest_route_reference
  )
),

stop_km AS (
  -- Each stop's position on its route and direction, from the longest branch serving it.
  SELECT route, direction, stop_id, km_from_start, route_km
  FROM branch_km
  QUALIFY ROW_NUMBER() OVER (PARTITION BY route, direction, stop_id ORDER BY branch_stops DESC) = 1
),

towards AS (
  -- Where each direction heads: the last stop of its longest branch.
  SELECT route, direction, stop_name AS towards
  FROM branch_km
  WHERE is_last
  QUALIFY ROW_NUMBER() OVER (PARTITION BY route, direction ORDER BY branch_stops DESC) = 1
),

polls AS (
  SELECT
    v.snapshot_at,
    v.vehicle_id,
    LOWER(v.line_id) AS route,
    v.direction,
    s.km_from_start AS position_km,
    s.route_km
  FROM `transport-for-london-511318.tfl_transport_analytics.bus_network_vehicles` AS v
  CROSS JOIN params AS p
  LEFT JOIN stop_km AS s
    ON s.route = LOWER(v.line_id)
    AND s.direction = v.direction
    AND s.stop_id = v.next_naptan_id
  WHERE
    -- `date` is the poll's UTC folder: the filter reads only those days' files.
    v.date >= DATE_SUB(p.window_start, INTERVAL 1 DAY)
    AND v.snapshot_at >= p.sheet_start
    AND DATE(v.snapshot_at, 'Europe/London') >= p.window_start
    AND DATE(v.snapshot_at, 'Europe/London') < CURRENT_DATE('Europe/London')
    AND v.vehicle_id IS NOT NULL
),

legs AS (
  SELECT
    *,
    LAG(route) OVER w AS prev_route,
    LAG(direction) OVER w AS prev_direction,
    LAG(position_km) OVER w AS prev_position_km,
    LAG(route_km) OVER w AS prev_route_km,
    TIMESTAMP_DIFF(snapshot_at, LAG(snapshot_at) OVER w, MINUTE) AS leg_minutes
  FROM polls
  WINDOW w AS (PARTITION BY vehicle_id ORDER BY snapshot_at)
),

moves AS (
  -- Distance along the route between two polls of the same bus:
  --   same route and direction: how far its next stop moved on;
  --   same route, other direction: it turned at the terminus, so the rest of the previous
  --   direction plus the way into the new one.
  SELECT
    *,
    CASE
      WHEN leg_minutes IS NULL OR leg_minutes > 45 THEN NULL
      WHEN route != prev_route OR position_km IS NULL OR prev_position_km IS NULL THEN NULL
      WHEN direction = prev_direction THEN position_km - prev_position_km
      ELSE (prev_route_km - prev_position_km) + position_km
    END AS move_km
  FROM legs
),

counted AS (
  -- Kept only when physically possible: forward, and no faster than 25 km/h on average (the
  -- fastest London buses, the SL express routes, average about that). Anything else is a
  -- prediction artefact, typically a bus waiting at a terminus whose direction TfL flips before
  -- it leaves; it counts as 0, so the total is a lower bound. Also 0: the first poll, a gap over
  -- 45 minutes, a change of route or a stop off the route reference.
  SELECT
    *,
    IF(move_km BETWEEN 0 AND leg_minutes * 25 / 60, move_km, 0) AS km
  FROM moves
)

SELECT
  DATE(c.snapshot_at, 'Europe/London') AS day,
  c.vehicle_id AS bus,
  UPPER(c.route) AS route,
  c.direction,
  ANY_VALUE(t.towards) AS towards,
  ROUND(SUM(c.km), 1) AS km,
  COUNT(*) AS polls_seen,
  FORMAT_TIMESTAMP('%H:%M', MIN(c.snapshot_at), 'Europe/London') AS first_seen,
  FORMAT_TIMESTAMP('%H:%M', MAX(c.snapshot_at), 'Europe/London') AS last_seen
FROM counted AS c
LEFT JOIN towards AS t
  ON t.route = c.route
  AND t.direction = c.direction
GROUP BY day, bus, route, c.direction
ORDER BY day, bus, km DESC;
