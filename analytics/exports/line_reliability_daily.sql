-- Google Sheet tab `line_reliability_daily` (Tableau Public): one row per line per London day.
-- Source: the hourly serving table tfl_portfolio.line_disruption_hourly. Completed London days
-- only, so a row never changes after it is written; the last 6 months (the tab's window).
--
-- Incidents are counted once per day even when one spans several hours (incident_keys are
-- distinct per hour). main_cause is the cause behind the most disrupted minutes that day.
WITH h AS (
  SELECT *, DATE(hour_start, 'Europe/London') AS day
  FROM `transport-for-london-511318.tfl_portfolio.line_disruption_hourly`
  WHERE DATE(hour_start, 'Europe/London') < CURRENT_DATE('Europe/London')
    AND DATE(hour_start, 'Europe/London') >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 6 MONTH)
),
causes AS (
  SELECT day, line_id, main_cause,
         ROW_NUMBER() OVER (PARTITION BY day, line_id ORDER BY SUM(disrupted_minutes) DESC, main_cause) AS rn
  FROM h
  WHERE main_cause IS NOT NULL
  GROUP BY day, line_id, main_cause
),
incidents AS (
  SELECT day, line_id, COUNT(DISTINCT k) AS incidents
  FROM h, UNNEST(incident_keys) AS k
  GROUP BY day, line_id
)
SELECT
  h.day,
  h.mode,
  h.line_id,
  ANY_VALUE(h.line_name) AS line_name,
  ROUND(SUM(observed_minutes), 1) AS observed_minutes,
  ROUND(SUM(in_service_minutes), 1) AS in_service_minutes,
  ROUND(SUM(disrupted_minutes), 1) AS disrupted_minutes,
  ROUND(SAFE_DIVIDE(SUM(disrupted_minutes), SUM(in_service_minutes)), 4) AS disrupted_share,
  IFNULL(ANY_VALUE(i.incidents), 0) AS incidents,
  SUM(flaps) AS flaps,
  ANY_VALUE(c.main_cause) AS main_cause
FROM h
LEFT JOIN incidents AS i USING (day, line_id)
LEFT JOIN causes AS c ON c.day = h.day AND c.line_id = h.line_id AND c.rn = 1
GROUP BY h.day, h.mode, h.line_id
ORDER BY h.day, h.mode, h.line_id
