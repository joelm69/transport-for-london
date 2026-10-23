-- Google Sheet tab `bus_vehicle_routes_daily` (Tableau Public): where each bus went, per bus
-- (registration), route and direction per completed London day, for the last 30 days (a
-- detail tab, so a short window keeps the Sheet small). `towards` names the direction's end of
-- the route. Coverage and distance rules: as bus_vehicles_daily.sql, whose legs this repeats.
WITH stops AS (
  SELECT stop_id, ANY_VALUE(ST_GEOGPOINT(longitude, latitude)) AS geo
  FROM `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
  GROUP BY stop_id
),
towards AS (
  -- Where each direction heads: the last stop of its longest branch in the latest route reference.
  SELECT line_id, direction, stop_name AS towards
  FROM (
    SELECT *, COUNT(*) OVER (PARTITION BY line_id, direction, branch_id) AS branch_stops
    FROM `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
    WHERE collection_run_id = (SELECT MAX(collection_run_id)
                               FROM `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`)
  )
  QUALIFY ROW_NUMBER() OVER (PARTITION BY line_id, direction ORDER BY branch_stops DESC, stop_order DESC) = 1
),
arrivals AS (
  SELECT vehicle_id, line_id, direction, naptan_id, arrived_at
  FROM `transport-for-london-511318.tfl_transport_analytics.arrival_events`
  WHERE source_date >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 31 DAY)
    AND mode = 'bus' AND vehicle_id IS NOT NULL
    AND DATE(arrived_at, 'Europe/London') < CURRENT_DATE('Europe/London')
    AND DATE(arrived_at, 'Europe/London') >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 30 DAY)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY visit_key ORDER BY kafka_partition, kafka_offset) = 1
),
legs AS (
  SELECT a.*,
         ST_DISTANCE(LAG(s.geo) OVER w, s.geo) / 1000 AS leg_km,
         TIMESTAMP_DIFF(a.arrived_at, LAG(a.arrived_at) OVER w, SECOND) AS leg_seconds
  FROM arrivals AS a
  JOIN stops AS s ON s.stop_id = a.naptan_id
  WINDOW w AS (PARTITION BY a.vehicle_id ORDER BY a.arrived_at, a.naptan_id)   -- tie-break: same legs every run
),
counted AS (
  SELECT *, IF(leg_seconds BETWEEN 1 AND 1800
               AND (leg_km <= 2 OR leg_km / (leg_seconds / 3600) <= 60), leg_km, 0) AS km
  FROM legs
)
SELECT
  DATE(c.arrived_at, 'Europe/London') AS day,
  c.vehicle_id AS bus,
  UPPER(c.line_id) AS route,
  c.direction,
  ANY_VALUE(t.towards) AS towards,
  ROUND(SUM(c.km), 1) AS km,
  COUNT(*) AS stops_served,
  FORMAT_TIMESTAMP('%H:%M', MIN(c.arrived_at), 'Europe/London') AS first_seen,
  FORMAT_TIMESTAMP('%H:%M', MAX(c.arrived_at), 'Europe/London') AS last_seen
FROM counted AS c
LEFT JOIN towards AS t ON LOWER(t.line_id) = LOWER(c.line_id) AND t.direction = c.direction
GROUP BY day, bus, route, c.direction
ORDER BY day, bus, km DESC
