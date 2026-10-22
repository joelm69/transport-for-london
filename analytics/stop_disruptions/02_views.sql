-- Stop disruption views: sessions -> periods -> incidents -> station days -> accessibility days.
-- Definitions follow docs/endpoints/stop_disruptions.md (section 5). Smoke tests excluded.

-- 1. Collection sessions -------------------------------------------------------------------
-- A new session starts after a gap above 150 s (2.5 polls at 60 s) OR when the collector
-- restarts (its first poll has raw_reason = 'baseline'): after a restart the collector forgets
-- what it saw, so a notice that vanished during the restart gets no RESOLVED record. Ending the
-- session at the restart stops that notice from stretching across it.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.stop_disruption_sessions` AS
WITH polls AS (
  SELECT
    TIMESTAMP(collected_at_utc) AS polled_at,
    raw_reason = 'baseline' AS is_restart,
    LAG(TIMESTAMP(collected_at_utc)) OVER (ORDER BY TIMESTAMP(collected_at_utc)) AS previous_poll_at
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_stop_disruption_coverage`
  WHERE valid AND collection_window != 'smoke_test'
),
numbered AS (
  SELECT
    polled_at,
    COUNTIF(
      previous_poll_at IS NULL
      OR is_restart
      OR TIMESTAMP_DIFF(polled_at, previous_poll_at, SECOND) > 150
    ) OVER (ORDER BY polled_at) AS session_no
  FROM polls
)
SELECT
  session_no,
  MIN(polled_at) AS session_start,
  TIMESTAMP_ADD(MAX(polled_at), INTERVAL 60 SECOND) AS session_end,  -- last poll covers 60 s
  COUNT(*) AS polls
FROM numbered
GROUP BY session_no;


-- 2. Notice periods ------------------------------------------------------------------------
-- Each BASELINE / NEW_INCIDENT / UPDATE row starts a version of a notice; it lasts until the
-- notice's next change (LEAD) - an UPDATE or its RESOLVED row - or the end of the session.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.stop_disruption_periods` AS
WITH changes AS (
  SELECT *, TIMESTAMP(collected_at_utc) AS changed_at
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_stop_disruption_changes`
  WHERE collection_window != 'smoke_test'
),
in_session AS (
  SELECT c.*, s.session_no, s.session_end
  FROM changes AS c
  JOIN `transport-for-london-511318.tfl_transport_analytics.stop_disruption_sessions` AS s
    ON c.changed_at >= s.session_start AND c.changed_at < s.session_end
),
ordered AS (
  SELECT
    *,
    LEAD(changed_at) OVER (PARTITION BY disruption_key, session_no ORDER BY changed_at) AS next_change_at
  FROM in_session
)
SELECT
  disruption_key,
  session_no,
  changed_at AS period_start,
  LEAST(COALESCE(next_change_at, session_end), session_end) AS period_end,
  next_change_at IS NULL AS runs_to_session_end,
  change_type,
  atco_code,
  station_atco_code,
  station_name,
  mode,
  type,
  appearance,
  description,
  TIMESTAMP(from_utc) AS reported_start,
  TIMESTAMP(to_utc) AS planned_end,       -- PlannedWork only
  to_date_kind,
  accessibility_flag,
  -- Main accessibility problem, checked in order: a "no step-free access ... due to a faulty
  -- lift" notice is about a lift.
  CASE
    WHEN NOT accessibility_flag THEN NULL
    WHEN REGEXP_CONTAINS(LOWER(description), r'\blifts?\b') THEN 'Lifts'
    WHEN REGEXP_CONTAINS(LOWER(description), r'escalator') THEN 'Escalators'
    WHEN REGEXP_CONTAINS(LOWER(description), r'step[- ]free') THEN 'Step-free access'
    WHEN REGEXP_CONTAINS(LOWER(description), r'\bramps?\b|boarding') THEN 'Ramps & boarding'
    WHEN REGEXP_CONTAINS(LOWER(description), r'wheelchair') THEN 'Wheelchair access'
    ELSE 'Other access issue'
  END AS accessibility_issue,
  concerned_lines
FROM ordered
WHERE change_type != 'RESOLVED';


-- 3. Incidents: one row per notice (disruption_key) ---------------------------------------

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.stop_disruption_incidents` AS
WITH resolved AS (
  SELECT disruption_key, MAX(TIMESTAMP(collected_at_utc)) AS resolved_at
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_stop_disruption_changes`
  WHERE change_type = 'RESOLVED' AND collection_window != 'smoke_test'
  GROUP BY disruption_key
),
aggregated AS (
  SELECT
    disruption_key,
    ARRAY_AGG(
      STRUCT(atco_code, station_atco_code, station_name, mode, type, appearance, description,
             reported_start, planned_end, to_date_kind)
      ORDER BY period_start DESC LIMIT 1
    )[OFFSET(0)] AS latest,
    LOGICAL_OR(accessibility_flag) AS accessibility_flag,
    MIN(period_start) AS first_seen,
    MAX(period_end) AS last_seen,
    COUNTIF(change_type = 'UPDATE') AS updates,
    SUM(TIMESTAMP_DIFF(period_end, period_start, SECOND)) / 60 AS observed_minutes
  FROM `transport-for-london-511318.tfl_transport_analytics.stop_disruption_periods`
  GROUP BY disruption_key
),
classified AS (
  SELECT
    a.*,
    -- Ended only if the RESOLVED record closes the latest observed version
    -- (a notice can be resolved and later reappear with the same key).
    IF(r.resolved_at IS NOT NULL AND r.resolved_at >= a.last_seen, r.resolved_at, NULL) AS ended_at
  FROM aggregated AS a
  LEFT JOIN resolved AS r USING (disruption_key)
)
SELECT
  disruption_key,
  latest.atco_code,
  latest.station_atco_code,
  latest.station_name,
  latest.mode,
  latest.type,
  latest.appearance,
  latest.description,
  latest.reported_start,
  latest.planned_end,
  latest.to_date_kind,
  accessibility_flag,
  first_seen,
  last_seen,
  ended_at,
  ended_at IS NULL AS is_censored,          -- still open when observation stopped
  updates,
  observed_minutes,
  -- Lifetime from TfL's reported start to the observed end (or last observation, if censored).
  TIMESTAMP_DIFF(COALESCE(ended_at, last_seen), latest.reported_start, MINUTE) / 60 AS duration_hours,
  -- Planned works only: positive = finished later than planned.
  IF(latest.appearance = 'PlannedWork' AND ended_at IS NOT NULL,
     TIMESTAMP_DIFF(ended_at, latest.planned_end, MINUTE) / 60, NULL) AS overrun_hours,
  -- Flicker: appeared and disappeared within 2 minutes of being seen.
  ended_at IS NOT NULL
    AND TIMESTAMP_DIFF(ended_at, GREATEST(latest.reported_start, first_seen), SECOND) < 120 AS is_flap
FROM classified;


-- 4. Station x day (London dates) ---------------------------------------------------------
-- Overlapping notices at one station are merged first ("gaps and islands"), so a station
-- with two simultaneous notices is not counted twice. Flickers are excluded.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.station_disruption_daily` AS
WITH intervals AS (
  SELECT p.atco_code, p.station_name, p.mode, category, p.period_start AS start_at, p.period_end AS end_at
  FROM `transport-for-london-511318.tfl_transport_analytics.stop_disruption_periods` AS p
  JOIN `transport-for-london-511318.tfl_transport_analytics.stop_disruption_incidents` AS i
    USING (disruption_key)
  CROSS JOIN UNNEST(ARRAY_CONCAT(
    ['any'],
    IF(p.accessibility_flag, ['accessibility'], []),
    IF(p.type = 'Part Closure', ['part_closure'], [])
  )) AS category
  WHERE NOT i.is_flap
),
marked AS (
  -- Latest end time among all earlier intervals of the same station and category.
  SELECT
    *,
    MAX(end_at) OVER (
      PARTITION BY atco_code, category ORDER BY start_at, end_at
      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS previous_max_end
  FROM intervals
),
islands AS (
  -- An interval starting after everything before it has ended starts a new island.
  SELECT
    *,
    COUNTIF(previous_max_end IS NULL OR start_at > previous_max_end) OVER (
      PARTITION BY atco_code, category ORDER BY start_at, end_at ROWS UNBOUNDED PRECEDING
    ) AS island
  FROM marked
),
merged AS (
  SELECT atco_code, ANY_VALUE(station_name) AS station_name, ANY_VALUE(mode) AS mode,
         category, MIN(start_at) AS start_at, MAX(end_at) AS end_at
  FROM islands
  GROUP BY atco_code, category, island
),
by_day AS (
  SELECT
    m.*,
    london_date,
    TIMESTAMP_DIFF(
      LEAST(m.end_at, TIMESTAMP(DATE_ADD(london_date, INTERVAL 1 DAY), 'Europe/London')),
      GREATEST(m.start_at, TIMESTAMP(london_date, 'Europe/London')),
      SECOND
    ) AS seconds
  FROM merged AS m,
  UNNEST(GENERATE_DATE_ARRAY(
    DATE(m.start_at, 'Europe/London'),
    DATE(TIMESTAMP_SUB(m.end_at, INTERVAL 1 MICROSECOND), 'Europe/London')
  )) AS london_date
),
observed AS (
  -- Minutes per London day that the collector was actually watching.
  SELECT
    london_date,
    SUM(TIMESTAMP_DIFF(
      LEAST(s.session_end, TIMESTAMP(DATE_ADD(london_date, INTERVAL 1 DAY), 'Europe/London')),
      GREATEST(s.session_start, TIMESTAMP(london_date, 'Europe/London')),
      SECOND
    )) / 60 AS observed_minutes
  FROM `transport-for-london-511318.tfl_transport_analytics.stop_disruption_sessions` AS s,
  UNNEST(GENERATE_DATE_ARRAY(
    DATE(s.session_start, 'Europe/London'),
    DATE(TIMESTAMP_SUB(s.session_end, INTERVAL 1 MICROSECOND), 'Europe/London')
  )) AS london_date
  GROUP BY london_date
)
SELECT
  d.atco_code,
  ANY_VALUE(d.station_name) AS station_name,
  ANY_VALUE(d.mode) AS mode,
  d.london_date,
  ANY_VALUE(o.observed_minutes) AS observed_minutes,
  SUM(IF(category = 'any', seconds, 0)) / 60 AS notice_minutes,
  SUM(IF(category = 'accessibility', seconds, 0)) / 60 AS accessibility_minutes,
  SUM(IF(category = 'part_closure', seconds, 0)) / 60 AS part_closure_minutes
FROM by_day AS d
JOIN observed AS o USING (london_date)
WHERE seconds > 0
GROUP BY d.atco_code, d.london_date;


-- 5. Accessibility by mode and day --------------------------------------------------------

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.accessibility_daily` AS
SELECT
  mode,
  london_date,
  ANY_VALUE(observed_minutes) AS observed_minutes,
  COUNT(DISTINCT IF(accessibility_minutes > 0, atco_code, NULL)) AS stations_with_accessibility_issues,
  SUM(accessibility_minutes) / 60 AS accessibility_station_hours,
  COUNT(DISTINCT IF(part_closure_minutes > 0, atco_code, NULL)) AS stations_part_closed
FROM `transport-for-london-511318.tfl_transport_analytics.station_disruption_daily`
GROUP BY mode, london_date;


-- 6. Station accessibility by hour ----------------------------------------------------------
-- Minutes per station and hour with at least one accessibility notice. Stations are combined
-- by name (an interchange can have a rail and an Underground code with the same notice), and
-- overlapping notices are merged first (gaps and islands). Flickers are excluded. Only hours
-- with accessibility minutes are kept; the denominator is stop_disruption_observed_hourly.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.station_accessibility_hourly` AS
WITH intervals AS (
  SELECT
    REGEXP_REPLACE(p.station_name, r' (?:Underground|Rail|DLR) Station$| Tram Stop$| Station$', '') AS station,
    p.mode,
    p.accessibility_issue AS issue,
    p.period_start AS start_at,
    p.period_end AS end_at
  FROM `transport-for-london-511318.tfl_transport_analytics.stop_disruption_periods` AS p
  JOIN `transport-for-london-511318.tfl_transport_analytics.stop_disruption_incidents` AS i
    USING (disruption_key)
  WHERE p.accessibility_flag AND NOT i.is_flap
),
marked AS (
  SELECT *, MAX(end_at) OVER (
    PARTITION BY station ORDER BY start_at, end_at
    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
  ) AS previous_max_end
  FROM intervals
),
islands AS (
  SELECT *, COUNTIF(previous_max_end IS NULL OR start_at > previous_max_end) OVER (
    PARTITION BY station ORDER BY start_at, end_at ROWS UNBOUNDED PRECEDING
  ) AS island
  FROM marked
),
merged AS (
  SELECT station, island, MIN(start_at) AS start_at, MAX(end_at) AS end_at,
         ARRAY_AGG(DISTINCT mode IGNORE NULLS) AS modes
  FROM islands
  GROUP BY station, island
),
merged_hours AS (
  SELECT
    station,
    hour_start,
    SUM(TIMESTAMP_DIFF(
      LEAST(end_at, TIMESTAMP_ADD(hour_start, INTERVAL 1 HOUR)),
      GREATEST(start_at, hour_start),
      SECOND
    )) / 60 AS accessibility_minutes,
    ARRAY_CONCAT_AGG(modes) AS all_modes
  FROM merged,
  UNNEST(GENERATE_TIMESTAMP_ARRAY(
    TIMESTAMP_TRUNC(start_at, HOUR),
    TIMESTAMP_TRUNC(TIMESTAMP_SUB(end_at, INTERVAL 1 MICROSECOND), HOUR),
    INTERVAL 1 HOUR
  )) AS hour_start
  GROUP BY station, hour_start
),
issue_hours AS (
  SELECT station, hour_start, ARRAY_AGG(DISTINCT issue IGNORE NULLS) AS issues
  FROM intervals,
  UNNEST(GENERATE_TIMESTAMP_ARRAY(
    TIMESTAMP_TRUNC(start_at, HOUR),
    TIMESTAMP_TRUNC(TIMESTAMP_SUB(end_at, INTERVAL 1 MICROSECOND), HOUR),
    INTERVAL 1 HOUR
  )) AS hour_start
  GROUP BY station, hour_start
)
SELECT
  m.station,
  ARRAY(SELECT DISTINCT x FROM UNNEST(m.all_modes) AS x ORDER BY x) AS modes,
  m.hour_start,
  m.accessibility_minutes,
  i.issues
FROM merged_hours AS m
LEFT JOIN issue_hours AS i USING (station, hour_start)
WHERE m.accessibility_minutes > 0;


-- 7. Observed minutes per hour (the denominator for availability) -------------------------

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.stop_disruption_observed_hourly` AS
SELECT
  hour_start,
  SUM(TIMESTAMP_DIFF(
    LEAST(s.session_end, TIMESTAMP_ADD(hour_start, INTERVAL 1 HOUR)),
    GREATEST(s.session_start, hour_start),
    SECOND
  )) / 60 AS observed_minutes
FROM `transport-for-london-511318.tfl_transport_analytics.stop_disruption_sessions` AS s,
UNNEST(GENERATE_TIMESTAMP_ARRAY(
  TIMESTAMP_TRUNC(s.session_start, HOUR),
  TIMESTAMP_TRUNC(TIMESTAMP_SUB(s.session_end, INTERVAL 1 MICROSECOND), HOUR),
  INTERVAL 1 HOUR
)) AS hour_start
GROUP BY hour_start;
