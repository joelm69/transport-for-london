-- Figures behind the rail business recommendation on the portfolio, recalculated hourly over the
-- last 7 complete London days, so the findings follow the data instead of a fixed week.
-- One row. The portfolio writes the findings from it (headline wording chosen by simple rules);
-- the recommendations themselves stay as written.
--
-- Planned works (rule agreed for the 22-28 Oct recommendation): TfL status Part Closure or
-- Planned Closure, cause "Planned works or changes", or a reason that announces dated works
-- ("Saturday 24 October ...", "until 1200", "from 1930", "operates ... only"). Everything else
-- disrupted is unplanned. Periods are clipped to the window, so a disruption crossing midnight
-- counts only the part inside it.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.rail_recommendation` AS
WITH window_bounds AS (
  SELECT
    TIMESTAMP(DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 7 DAY), 'Europe/London') AS window_start,
    TIMESTAMP(CURRENT_DATE('Europe/London'), 'Europe/London') AS window_end
),
periods AS (
  SELECT
    p.line_name,
    p.state_class,
    p.state_class = 'disrupted' AND NOT p.is_flap AS disrupted,
    p.worst_status IN ('Part Closure', 'Planned Closure')
      OR p.cause_category = 'Planned works or changes'
      OR REGEXP_CONTAINS(LOWER(p.worst_reason),
           r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday) \d{1,2}|until \d|from \d{4}|operates .* only')
      AS planned,
    p.cause_category,
    TIMESTAMP_DIFF(LEAST(p.period_end, w.window_end), GREATEST(p.period_start, w.window_start), SECOND) / 3600
      AS hours
  FROM `transport-for-london-511318.tfl_transport_analytics.line_status_periods` AS p, window_bounds AS w
  WHERE p.period_end > w.window_start AND p.period_start < w.window_end
),
lines AS (
  SELECT
    line_name,
    SUM(IF(state_class != 'closed', hours, 0)) AS in_service_hours,
    SUM(IF(disrupted AND NOT planned, hours, 0)) AS unplanned_hours
  FROM periods
  GROUP BY line_name
),
line_causes AS (
  SELECT line_name, cause_category, SUM(hours) AS hours,
         ROW_NUMBER() OVER (PARTITION BY line_name ORDER BY SUM(hours) DESC) AS rn
  FROM periods
  WHERE disrupted AND NOT planned AND cause_category IS NOT NULL
  GROUP BY line_name, cause_category
),
top_lines AS (
  SELECT ARRAY_AGG(
    STRUCT(l.line_name, l.unplanned_hours, SAFE_DIVIDE(l.unplanned_hours, l.in_service_hours) AS unplanned_share,
           c.cause_category AS main_cause, SAFE_DIVIDE(c.hours, l.unplanned_hours) AS main_cause_share)
    ORDER BY l.unplanned_hours DESC LIMIT 3) AS top_lines
  FROM lines AS l
  LEFT JOIN line_causes AS c ON c.line_name = l.line_name AND c.rn = 1
  WHERE l.unplanned_hours > 0
),
totals AS (
  SELECT
    SUM(IF(state_class != 'closed', hours, 0)) AS in_service_hours,
    SUM(IF(disrupted, hours, 0)) AS disrupted_hours,
    SUM(IF(disrupted AND planned, hours, 0)) AS planned_hours,
    SUM(IF(disrupted AND NOT planned, hours, 0)) AS unplanned_hours,
    SUM(IF(disrupted AND NOT planned AND cause_category IN ('Train cancellations', 'Train fault'), hours, 0))
      AS train_hours,
    SUM(IF(disrupted AND NOT planned AND cause_category IN
           ('Signal failure', 'Track fault', 'Points failure', 'Power failure', 'Track Circuit Failure'), hours, 0))
      AS infrastructure_hours
  FROM periods
)
SELECT
  DATE(w.window_start, 'Europe/London') AS first_day,
  DATE_SUB(DATE(w.window_end, 'Europe/London'), INTERVAL 1 DAY) AS last_day,
  t.in_service_hours,
  t.disrupted_hours,
  t.planned_hours,
  t.unplanned_hours,
  1 - SAFE_DIVIDE(t.disrupted_hours, t.in_service_hours) AS good_service,
  1 - SAFE_DIVIDE(t.unplanned_hours, t.in_service_hours) AS good_service_without_planned,
  SAFE_DIVIDE(t.planned_hours, t.disrupted_hours) AS planned_share,
  SAFE_DIVIDE(t.unplanned_hours, t.disrupted_hours) AS unplanned_share,
  SAFE_DIVIDE(t.unplanned_hours, t.in_service_hours) AS unplanned_service_share,
  SAFE_DIVIDE(t.train_hours, t.unplanned_hours) AS train_share,
  SAFE_DIVIDE(t.infrastructure_hours, t.unplanned_hours) AS infrastructure_share,
  SAFE_DIVIDE((SELECT SUM(x.unplanned_hours) FROM UNNEST(tl.top_lines) AS x), t.unplanned_hours) AS top_lines_share,
  tl.top_lines,
  CURRENT_TIMESTAMP() AS computed_at
FROM totals AS t, window_bounds AS w, top_lines AS tl;
