-- Google Sheet tab `station_accessibility_daily` (Tableau Public): one row per station per
-- completed London day on which it had an accessibility notice (lifts, step-free access,
-- escalators), all history (about 50 rows a day). Stations without notices are not listed: no notice does
-- not mean step-free. Full history since collection began
-- (21 October 2026).
-- Source: tfl_portfolio.station_accessibility_hourly and stop_disruption_observed_hourly.
--
-- problem_share = minutes with a notice / minutes the notices feed was observed that day.
WITH observed AS (
  SELECT DATE(hour_start, 'Europe/London') AS day, SUM(observed_minutes) AS observed_minutes
  FROM `transport-for-london-511318.tfl_portfolio.stop_disruption_observed_hourly`
  WHERE DATE(hour_start, 'Europe/London') < CURRENT_DATE('Europe/London')
  GROUP BY day
),
stations AS (
  SELECT DATE(hour_start, 'Europe/London') AS day, station,
         SUM(accessibility_minutes) AS accessibility_minutes,
         ARRAY_CONCAT_AGG(modes) AS modes,
         ARRAY_CONCAT_AGG(issues) AS issues
  FROM `transport-for-london-511318.tfl_portfolio.station_accessibility_hourly`
  WHERE DATE(hour_start, 'Europe/London') < CURRENT_DATE('Europe/London')
  GROUP BY day, station
)
SELECT
  s.day,
  s.station,
  ARRAY_TO_STRING(ARRAY(SELECT DISTINCT m FROM UNNEST(s.modes) AS m ORDER BY m), ', ') AS modes,
  ROUND(s.accessibility_minutes, 1) AS accessibility_minutes,
  ROUND(o.observed_minutes, 1) AS observed_minutes,
  ROUND(LEAST(1, SAFE_DIVIDE(s.accessibility_minutes, o.observed_minutes)), 4) AS problem_share,
  ARRAY_TO_STRING(ARRAY(SELECT DISTINCT i FROM UNNEST(s.issues) AS i ORDER BY i), ', ') AS issues
FROM stations AS s
JOIN observed AS o USING (day)
WHERE s.accessibility_minutes > 0
ORDER BY s.day, s.station
