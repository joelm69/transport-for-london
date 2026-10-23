-- Google Sheet tab `bus_vehicles_daily` (Tableau Public): one row per bus (registration) per
-- completed London day, for the last 6 months: distance travelled, routes worked and time in
-- service. Feeds the most- and least-travelled buses.
--
-- Coverage: only the 12 routes the VM follows (config/arrivals.json). A bus is seen only while
-- it works one of them, so a bus that spends part of its day elsewhere shows less distance.
--
-- Distance: straight-line distance between consecutive stops the bus reached (stop locations
-- from the route reference), summed. Roads are longer than straight lines, so this is a lower
-- bound. A leg is skipped when the bus wasn't seen for over 30 minutes (layover, garage run,
-- or a stretch on a route we don't follow) or when it is implausible (over 2 km at over 60 km/h:
-- a stray prediction). The same legs are used in bus_vehicle_routes_daily.sql.
WITH stops AS (
  SELECT stop_id, ANY_VALUE(ST_GEOGPOINT(longitude, latitude)) AS geo
  FROM `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
  GROUP BY stop_id
),
arrivals AS (
  SELECT vehicle_id, line_id, direction, naptan_id, arrived_at
  FROM `transport-for-london-511318.tfl_transport_analytics.arrival_events`
  WHERE source_date >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 6 MONTH)
    AND mode = 'bus' AND vehicle_id IS NOT NULL
    AND DATE(arrived_at, 'Europe/London') < CURRENT_DATE('Europe/London')
    AND DATE(arrived_at, 'Europe/London') >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 6 MONTH)
  -- One arrival per visit (a visit_key can repeat only after a crash replay).
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
  DATE(arrived_at, 'Europe/London') AS day,
  vehicle_id AS bus,
  -- The route it served most stops on that day, then every route it worked.
  APPROX_TOP_COUNT(UPPER(line_id), 1)[OFFSET(0)].value AS main_route,
  ARRAY_TO_STRING(ARRAY_AGG(DISTINCT UPPER(line_id) ORDER BY UPPER(line_id)), ', ') AS routes,
  COUNT(DISTINCT line_id) AS route_count,
  ROUND(SUM(km), 1) AS km,
  COUNT(*) AS stops_served,
  FORMAT_TIMESTAMP('%H:%M', MIN(arrived_at), 'Europe/London') AS first_seen,
  FORMAT_TIMESTAMP('%H:%M', MAX(arrived_at), 'Europe/London') AS last_seen,
  ROUND(TIMESTAMP_DIFF(MAX(arrived_at), MIN(arrived_at), MINUTE) / 60, 1) AS hours_seen
FROM counted
GROUP BY day, bus
ORDER BY day, km DESC
