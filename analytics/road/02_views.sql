-- Road views: sessions -> disruption periods -> disruptions; corridor periods -> corridor hours.
-- Definitions follow docs/endpoints/road.md (section 4). Only the VM's continuous collection
-- counts; manual and test runs are excluded.

-- 1. Collection sessions -------------------------------------------------------------------
-- A new session starts after a gap above 750 s (2.5 polls at 300 s) OR when the collector
-- restarts (its first poll is a 'baseline'): after a restart it forgets what it saw, so a
-- disruption that cleared during the restart gets no CLEARED record.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.road_sessions` AS
WITH polls AS (
  SELECT
    TIMESTAMP(collected_at_utc) AS polled_at,
    raw_reason = 'baseline' AS is_restart,
    LAG(TIMESTAMP(collected_at_utc)) OVER (ORDER BY TIMESTAMP(collected_at_utc)) AS previous_poll_at
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_road_coverage`
  WHERE disruptions_valid AND corridors_valid AND collection_window = 'continuous'
),
numbered AS (
  SELECT
    polled_at,
    COUNTIF(
      previous_poll_at IS NULL
      OR is_restart
      OR TIMESTAMP_DIFF(polled_at, previous_poll_at, SECOND) > 750
    ) OVER (ORDER BY polled_at) AS session_no
  FROM polls
)
SELECT
  session_no,
  MIN(polled_at) AS session_start,
  TIMESTAMP_ADD(MAX(polled_at), INTERVAL 300 SECOND) AS session_end,  -- last poll covers 300 s
  COUNT(*) AS polls
FROM numbered
GROUP BY session_no;


-- 2. Disruption periods --------------------------------------------------------------------
-- Each BASELINE / NEW / UPDATE row starts a version of a disruption; it lasts until the
-- disruption's next change (an UPDATE or its CLEARED row) or the end of the session.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.road_disruption_periods` AS
WITH changes AS (
  SELECT *, TIMESTAMP(collected_at_utc) AS changed_at
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_road_disruption_changes`
  WHERE collection_window = 'continuous'
),
in_session AS (
  SELECT c.*, s.session_no, s.session_end
  FROM changes AS c
  JOIN `transport-for-london-511318.tfl_transport_analytics.road_sessions` AS s
    ON c.changed_at >= s.session_start AND c.changed_at < s.session_end
),
ordered AS (
  SELECT *, LEAD(changed_at) OVER (PARTITION BY disruption_id, session_no ORDER BY changed_at) AS next_change_at
  FROM in_session
)
SELECT
  disruption_id,
  session_no,
  changed_at AS period_start,
  LEAST(COALESCE(next_change_at, session_end), session_end) AS period_end,
  change_type,
  category,
  sub_category,
  is_incident,
  severity,
  CASE severity WHEN 'Severe' THEN 4 WHEN 'Serious' THEN 3 WHEN 'Moderate' THEN 2
                WHEN 'Minimal' THEN 1 ELSE 0 END AS severity_rank,
  location,
  -- The borough is the last bracketed name in TfL's location: "[A10] GREAT CAMBRIDGE ROAD (EN1 ) (Enfield)".
  REGEXP_EXTRACT(location, r'\(([A-Za-z][A-Za-z &.-]+)\)\s*$') AS borough,
  comments,
  current_update,
  corridor_ids,
  ARRAY_LENGTH(closed_streets) > 0 AS closes_street,
  lat,
  lon,
  TIMESTAMP(start_utc) AS reported_start,
  TIMESTAMP(end_utc) AS planned_end
FROM ordered
WHERE change_type != 'CLEARED';


-- 3. Disruptions: one row per disruption ---------------------------------------------------
-- cleared_at: the poll that first saw it gone (accurate to 5 minutes). Still open at the end of
-- the data = censored. minutes_to_clear runs from TfL's reported start, which can be before
-- collection began; overrun compares the clear time with TfL's planned end (works mostly).

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.road_disruptions` AS
WITH cleared AS (
  SELECT disruption_id, MAX(TIMESTAMP(collected_at_utc)) AS cleared_at
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_road_disruption_changes`
  WHERE change_type = 'CLEARED' AND collection_window = 'continuous'
  GROUP BY disruption_id
),
aggregated AS (
  SELECT
    disruption_id,
    ARRAY_AGG(
      STRUCT(category, sub_category, is_incident, location, borough, comments, corridor_ids,
             lat, lon, reported_start, planned_end)
      ORDER BY period_start DESC LIMIT 1
    )[OFFSET(0)] AS latest,
    MAX(severity_rank) AS worst_severity_rank,
    ARRAY_AGG(severity ORDER BY severity_rank DESC LIMIT 1)[OFFSET(0)] AS worst_severity,
    LOGICAL_OR(closes_street) AS closes_street,
    MIN(period_start) AS first_seen,
    MAX(period_end) AS last_seen,
    COUNTIF(change_type = 'UPDATE') AS updates,
    SUM(TIMESTAMP_DIFF(period_end, period_start, SECOND)) / 60 AS observed_minutes
  FROM `transport-for-london-511318.tfl_transport_analytics.road_disruption_periods`
  GROUP BY disruption_id
)
SELECT
  a.disruption_id,
  a.latest.category,
  a.latest.sub_category,
  a.latest.is_incident,
  a.worst_severity,
  a.worst_severity_rank,
  a.latest.location,
  a.latest.borough,
  a.latest.comments,
  a.latest.corridor_ids,
  a.latest.lat,
  a.latest.lon,
  a.closes_street,
  a.latest.reported_start,
  a.latest.planned_end,
  a.first_seen,
  c.cleared_at,
  c.cleared_at IS NULL AS still_open,
  a.updates,
  a.observed_minutes,
  TIMESTAMP_DIFF(c.cleared_at, a.latest.reported_start, SECOND) / 60 AS minutes_to_clear,
  TIMESTAMP_DIFF(c.cleared_at, a.latest.planned_end, SECOND) / 60 AS overrun_minutes
FROM aggregated AS a
LEFT JOIN cleared AS c USING (disruption_id);


-- 4. Corridor periods and hours ------------------------------------------------------------

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.road_corridor_periods` AS
WITH changes AS (
  SELECT *, TIMESTAMP(collected_at_utc) AS changed_at
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_road_corridor_changes`
  WHERE collection_window = 'continuous'
),
in_session AS (
  SELECT c.*, s.session_no, s.session_end
  FROM changes AS c
  JOIN `transport-for-london-511318.tfl_transport_analytics.road_sessions` AS s
    ON c.changed_at >= s.session_start AND c.changed_at < s.session_end
),
ordered AS (
  SELECT *, LEAD(changed_at) OVER (PARTITION BY corridor_id, session_no ORDER BY changed_at) AS next_change_at
  FROM in_session
)
SELECT
  corridor_id,
  corridor_name,
  session_no,
  changed_at AS period_start,
  LEAST(COALESCE(next_change_at, session_end), session_end) AS period_end,
  status,
  status_description
FROM ordered
WHERE change_type != 'REMOVED';

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.road_corridor_hourly` AS
WITH pieces AS (
  SELECT
    p.*,
    hour_start,
    TIMESTAMP_DIFF(
      LEAST(p.period_end, TIMESTAMP_ADD(hour_start, INTERVAL 1 HOUR)),
      GREATEST(p.period_start, hour_start),
      SECOND
    ) AS seconds
  FROM `transport-for-london-511318.tfl_transport_analytics.road_corridor_periods` AS p,
  UNNEST(GENERATE_TIMESTAMP_ARRAY(
    TIMESTAMP_TRUNC(p.period_start, HOUR),
    TIMESTAMP_TRUNC(TIMESTAMP_SUB(p.period_end, INTERVAL 1 MICROSECOND), HOUR),
    INTERVAL 1 HOUR
  )) AS hour_start
)
SELECT
  corridor_id,
  ANY_VALUE(corridor_name) AS corridor_name,
  hour_start,
  SUM(seconds) / 60 AS observed_minutes,
  SUM(IF(status = 'Good', seconds, 0)) / 60 AS good_minutes,
  SUM(IF(status = 'Serious', seconds, 0)) / 60 AS serious_minutes,
  SUM(IF(status = 'Severe', seconds, 0)) / 60 AS severe_minutes,
  SUM(IF(status = 'Closure', seconds, 0)) / 60 AS closure_minutes,
  SUM(IF(status != 'Good', seconds, 0)) / 60 AS delayed_minutes
FROM pieces
WHERE seconds > 0
GROUP BY corridor_id, hour_start;
