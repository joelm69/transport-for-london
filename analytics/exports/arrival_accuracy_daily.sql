-- Google Sheet tab `arrival_accuracy_daily` (Tableau Public): how far TfL's countdown was from
-- the actual arrival, per Tube line, completed London day and horizon (prediction made 10, 5
-- or 2 minutes before). error = actual - predicted, so positive = later than predicted.
--
-- Tube only: accuracy needs a confirmed arrival ("At Platform"). Where an arrival is inferred
-- from a vanishing prediction (tram, bus, some Tube), the last prediction IS the arrival time,
-- so the error would be zero by construction (analytics/arrivals/03_functions.sql, function 5).
-- Read from the event table rather than the hourly serving table, because daily medians
-- can't be rebuilt from hourly ones.
WITH arrivals AS (
  SELECT *
  FROM `transport-for-london-511318.tfl_transport_analytics.arrival_events`
  WHERE source_date >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 6 MONTH)   -- the tab's window
    AND detection = 'at_platform'
    AND DATE(arrived_at, 'Europe/London') < CURRENT_DATE('Europe/London')
    AND arrived_at >= TIMESTAMP('2026-10-26 15:00:00', 'Europe/London')   -- Sheet start (data from 26 October 2026, 15:00 London time)
  -- One arrival per visit (a visit_key can repeat only after a crash replay).
  QUALIFY ROW_NUMBER() OVER (PARTITION BY visit_key ORDER BY kafka_partition, kafka_offset) = 1
),
errors AS (
  SELECT a.mode, a.line_id, DATE(a.arrived_at, 'Europe/London') AS day, c.horizon_minutes,
         TIMESTAMP_DIFF(a.arrived_at, c.expected_arrival, MILLISECOND) / 1000 AS error_seconds
  FROM arrivals AS a, UNNEST(a.checkpoints) AS c
),
names AS (
  SELECT line_id, ANY_VALUE(line_name) AS line_name
  FROM `transport-for-london-511318.tfl_portfolio.line_disruption_hourly`
  GROUP BY line_id
)
SELECT
  e.day,
  e.mode,
  e.line_id,
  IFNULL(n.line_name, e.line_id) AS line_name,
  e.horizon_minutes,
  COUNT(*) AS predictions,
  ROUND(AVG(e.error_seconds), 1) AS mean_error_seconds,
  ROUND(APPROX_QUANTILES(ABS(e.error_seconds), 2)[OFFSET(1)], 1) AS median_abs_error_seconds,
  ROUND(APPROX_QUANTILES(ABS(e.error_seconds), 10)[OFFSET(9)], 1) AS p90_abs_error_seconds,
  ROUND(COUNTIF(ABS(e.error_seconds) <= 60) / COUNT(*), 4) AS within_1_min_share
FROM errors AS e
LEFT JOIN names AS n USING (line_id)
GROUP BY e.day, e.mode, e.line_id, line_name, e.horizon_minutes
ORDER BY e.day, e.line_id, e.horizon_minutes
