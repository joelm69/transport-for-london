-- Figures behind the bus business recommendation on the portfolio, recalculated hourly over the
-- last 7 complete London days (bus data starts 26 October 2026, 15:00), so the findings follow
-- the data. One row. Weighted exactly as the Tableau bus dashboard's tiles: each export row
-- (route, direction, day, time band) by its number of polls. Built on the export views
-- (analytics/bus_network/02_export_views.sql).
--
-- Extra wait from uneven spacing = average wait - half the typical gap (the wait if buses came
-- evenly). Passenger-hours = extra wait x 5.05 million journeys a day (TfL: 1.842 billion bus
-- journeys in 2024/25, Travel in London 2025), an upper estimate: it assumes passengers arrive
-- at random, which holds on frequent routes.
-- Frequent routes: typical gap of 8 minutes or less. Big routes (for speed): seen at 200 or more
-- polls a day on average, which leaves out very short routes.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.bus_recommendation` AS
WITH window_days AS (
  SELECT DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 7 DAY) AS first_day,
         DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 1 DAY) AS last_day
),
routes AS (
  SELECT r.*
  FROM `transport-for-london-511318.tfl_transport_analytics.bus_routes_daily` AS r, window_days AS w
  WHERE r.day BETWEEN w.first_day AND w.last_day
),
vehicles AS (
  SELECT v.*
  FROM `transport-for-london-511318.tfl_transport_analytics.bus_vehicle_routes_daily` AS v, window_days AS w
  WHERE v.day BETWEEN w.first_day AND w.last_day
),
network AS (
  SELECT
    COUNT(DISTINCT day) AS days,
    MIN(day) AS data_from,
    SAFE_DIVIDE(SUM(bunching_rate * polls), SUM(polls)) AS bunching_rate,
    SAFE_DIVIDE(SUM(average_wait_minutes * polls), SUM(IF(average_wait_minutes IS NOT NULL, polls, NULL))) AS wait_minutes,
    SAFE_DIVIDE(SUM(typical_gap_minutes * polls), SUM(IF(typical_gap_minutes IS NOT NULL, polls, NULL))) AS gap_minutes
  FROM routes
),
daily_bunching AS (
  SELECT MIN(rate) AS bunching_min, MAX(rate) AS bunching_max
  FROM (SELECT day, SAFE_DIVIDE(SUM(bunching_rate * polls), SUM(polls)) AS rate FROM routes GROUP BY day)
),
route_bunching AS (
  SELECT
    route,
    SAFE_DIVIDE(SUM(bunching_rate * polls), SUM(polls)) AS bunching_rate,
    SAFE_DIVIDE(SUM(typical_gap_minutes * polls), SUM(IF(typical_gap_minutes IS NOT NULL, polls, NULL))) AS gap_minutes,
    SAFE_DIVIDE(SUM(average_wait_minutes * polls), SUM(IF(average_wait_minutes IS NOT NULL, polls, NULL))) AS wait_minutes
  FROM routes
  GROUP BY route
),
most_bunched AS (
  SELECT ARRAY_AGG(
    STRUCT(route, bunching_rate, gap_minutes, SAFE_DIVIDE(wait_minutes, gap_minutes / 2) AS wait_ratio)
    ORDER BY bunching_rate DESC LIMIT 5) AS most_bunched
  FROM route_bunching
  WHERE gap_minutes <= 8
),
route_speed AS (
  SELECT route, SAFE_DIVIDE(SUM(km), SUM(polls_seen) * 0.5) AS kmh, COUNT(DISTINCT bus) AS buses
  FROM vehicles
  GROUP BY route
  HAVING SUM(polls_seen) >= 200 * (SELECT COUNT(DISTINCT day) FROM vehicles)
),
slowest AS (
  SELECT ARRAY_AGG(STRUCT(route, kmh, buses) ORDER BY kmh LIMIT 5) AS slowest FROM route_speed
)
SELECT
  w.first_day,
  w.last_day,
  n.data_from,
  n.days,
  n.bunching_rate,
  d.bunching_min,
  d.bunching_max,
  n.wait_minutes,
  n.gap_minutes,
  n.gap_minutes / 2 AS even_wait_minutes,
  n.wait_minutes - n.gap_minutes / 2 AS extra_wait_minutes,
  SAFE_DIVIDE(n.wait_minutes - n.gap_minutes / 2, n.gap_minutes / 2) AS extra_wait_share,
  (n.wait_minutes - n.gap_minutes / 2) * 1842000000 / 365 / 60 AS passenger_hours_per_day,
  (SELECT SAFE_DIVIDE(SUM(km), SUM(polls_seen) * 0.5) FROM vehicles) AS network_kmh,
  mb.most_bunched,
  s.slowest,
  CURRENT_TIMESTAMP() AS computed_at
FROM window_days AS w, network AS n, daily_bunching AS d, most_bunched AS mb, slowest AS s;
