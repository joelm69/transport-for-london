-- Google Sheet tab: bus_route_paths
-- Every bus route drawn as a path for Tableau's London map: one row per route, direction and
-- stop, in stop order (Tableau's line mark with Path = stop_order), with coordinates. The longest
-- branch per direction, so each direction is one clean line. From the latest route reference
-- (route_stop_occurrences); about 55,000 rows. Joins to bus_routes_daily on route and direction.

WITH latest_route_reference AS (
  SELECT *
  FROM `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
  WHERE collection_run_id = (
    SELECT MAX(collection_run_id)
    FROM `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
  )
),

branches AS (
  SELECT *, COUNT(*) OVER (PARTITION BY line_id, direction, branch_id) AS branch_stops
  FROM latest_route_reference
),

longest AS (
  -- One branch per route and direction: the one with the most stops.
  SELECT line_id, direction, branch_id
  FROM branches
  QUALIFY ROW_NUMBER() OVER (PARTITION BY line_id, direction ORDER BY branch_stops DESC, branch_id) = 1
),

stops AS (
  SELECT b.*
  FROM branches AS b
  JOIN longest AS l USING (line_id, direction, branch_id)
)

SELECT
  UPPER(line_id) AS route,
  direction,
  LAST_VALUE(stop_name) OVER (
    PARTITION BY line_id, direction ORDER BY stop_order
    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
  ) AS towards,
  stop_order,
  stop_name,
  ROUND(latitude, 6) AS latitude,
  ROUND(longitude, 6) AS longitude
FROM stops
ORDER BY route, direction, stop_order;
