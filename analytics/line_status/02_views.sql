-- Line status views: change records -> sessions -> status periods -> hourly and daily metrics.
-- Definitions follow docs/endpoints/line_status.md (section 5, metric definitions).
-- Smoke-test collections are excluded everywhere.

-- 1. Valid polls and collection sessions -------------------------------------------------
-- A session is a run of valid polls with no gap above 90 seconds (check C3). Periods never
-- extend across a gap, so KPIs only count time that was actually observed.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.line_status_sessions` AS
WITH polls AS (
  SELECT
    TIMESTAMP(collected_at_utc) AS polled_at,
    LAG(TIMESTAMP(collected_at_utc)) OVER (ORDER BY TIMESTAMP(collected_at_utc)) AS previous_poll_at
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_line_status_coverage`
  WHERE valid AND collection_window != 'smoke_test'
),
numbered AS (
  SELECT
    polled_at,
    COUNTIF(
      previous_poll_at IS NULL
      OR TIMESTAMP_DIFF(polled_at, previous_poll_at, SECOND) > 90
    ) OVER (ORDER BY polled_at) AS session_no
  FROM polls
)
SELECT
  session_no,
  MIN(polled_at) AS session_start,
  -- The last poll represents the following 30-second interval.
  TIMESTAMP_ADD(MAX(polled_at), INTERVAL 30 SECOND) AS session_end,
  COUNT(*) AS polls
FROM numbered
GROUP BY session_no;


-- 2. Status periods ---------------------------------------------------------------------
-- Every change poll records a line's full status set (BASELINE / ONGOING guarantee this),
-- so a line's state holds from one change point to the next, within one session.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.line_status_periods` AS
WITH change_points AS (
  SELECT
    line_id,
    ANY_VALUE(line_name) AS line_name,
    ANY_VALUE(mode) AS mode,
    TIMESTAMP(collected_at_utc) AS period_start,
    CASE
      WHEN LOGICAL_OR(severity_class = 'disrupted') THEN 'disrupted'
      WHEN LOGICAL_AND(severity_class = 'closed') THEN 'closed'
      ELSE 'good'
    END AS state_class,
    -- Lower TfL severity codes are more severe (1 Closed ... 10 Good Service).
    ARRAY_AGG(
      IF(severity_class = 'disrupted', STRUCT(severity, severity_description, reason, incident_key), NULL)
      IGNORE NULLS ORDER BY severity LIMIT 1
    )[SAFE_OFFSET(0)] AS worst,
    ARRAY_AGG(DISTINCT incident_key IGNORE NULLS) AS incident_keys
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_line_status_changes`
  WHERE collection_window != 'smoke_test'
  GROUP BY line_id, poll_id, collected_at_utc
),
in_session AS (
  SELECT cp.*, s.session_no, s.session_end
  FROM change_points AS cp
  JOIN `transport-for-london-511318.tfl_transport_analytics.line_status_sessions` AS s
    ON cp.period_start >= s.session_start AND cp.period_start < s.session_end
),
bounded AS (
  SELECT
    *,
    LEAST(
      COALESCE(
        LEAD(period_start) OVER (PARTITION BY line_id, session_no ORDER BY period_start),
        session_end
      ),
      session_end
    ) AS period_end,
    LAG(state_class) OVER (PARTITION BY line_id, session_no ORDER BY period_start) AS previous_class,
    LEAD(state_class) OVER (PARTITION BY line_id, session_no ORDER BY period_start) AS next_class
  FROM in_session
)
SELECT
  line_id,
  line_name,
  mode,
  session_no,
  period_start,
  period_end,
  TIMESTAMP_DIFF(period_end, period_start, SECOND) AS duration_seconds,
  state_class,
  worst.severity AS worst_severity,
  worst.severity_description AS worst_status,
  worst.reason AS worst_reason,
  CASE
    WHEN worst.reason IS NULL THEN NULL
    WHEN REGEXP_CONTAINS(LOWER(worst.reason), r'signal') THEN 'Signal failure'
    WHEN REGEXP_CONTAINS(LOWER(worst.reason), r'track') THEN 'Track fault'
    WHEN REGEXP_CONTAINS(LOWER(worst.reason), r'faulty train|train fault') THEN 'Train fault'
    WHEN REGEXP_CONTAINS(LOWER(worst.reason), r'cancellation') THEN 'Train cancellations'
    WHEN REGEXP_CONTAINS(LOWER(worst.reason), r'\bill\b|customer|passenger') THEN 'Customer incident'
    WHEN REGEXP_CONTAINS(LOWER(worst.reason), r'staff') THEN 'Staff shortage'
    WHEN REGEXP_CONTAINS(LOWER(worst.reason), r'fire|alarm') THEN 'Fire alert'
    WHEN REGEXP_CONTAINS(LOWER(worst.reason), r'power') THEN 'Power supply'
    WHEN REGEXP_CONTAINS(LOWER(worst.reason), r'planned|engineering|closure') THEN 'Planned works'
    ELSE 'Other'
  END AS cause_category,
  incident_keys,
  -- Debounce (check C8): a flap is a brief switch to a DIFFERENT state and back, e.g.
  -- Good -> Severe Delays (30 s) -> Good. A short period inside an ongoing disruption
  -- (TfL editing its message) is not a flap. Raw changes keep every flip.
  TIMESTAMP_DIFF(period_end, period_start, SECOND) < 60
    AND next_class IS NOT NULL AND next_class != state_class
    AND (previous_class IS NULL OR previous_class != state_class) AS is_flap
FROM bounded
WHERE period_end > period_start;


-- 3. Hourly disruption metrics per line ---------------------------------------------------
-- Periods are split at hour boundaries so each hour gets its exact share of seconds.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.line_disruption_hourly` AS
WITH pieces AS (
  SELECT
    p.*,
    hour_start,
    TIMESTAMP_DIFF(
      LEAST(p.period_end, TIMESTAMP_ADD(hour_start, INTERVAL 1 HOUR)),
      GREATEST(p.period_start, hour_start),
      SECOND
    ) AS seconds
  FROM `transport-for-london-511318.tfl_transport_analytics.line_status_periods` AS p,
  UNNEST(GENERATE_TIMESTAMP_ARRAY(
    TIMESTAMP_TRUNC(p.period_start, HOUR),
    TIMESTAMP_TRUNC(TIMESTAMP_SUB(p.period_end, INTERVAL 1 MICROSECOND), HOUR),
    INTERVAL 1 HOUR
  )) AS hour_start
),
counted AS (
  SELECT *, state_class = 'disrupted' AND NOT is_flap AS counts_as_disruption
  FROM pieces
  WHERE seconds > 0
),
aggregated AS (
  SELECT
    line_id,
    ANY_VALUE(line_name) AS line_name,
    ANY_VALUE(mode) AS mode,
    hour_start,
    SUM(seconds) / 60 AS observed_minutes,
    SUM(IF(state_class != 'closed', seconds, 0)) / 60 AS in_service_minutes,
    SUM(IF(counts_as_disruption, seconds, 0)) / 60 AS disrupted_minutes,
    ARRAY_CONCAT_AGG(IF(counts_as_disruption, incident_keys, [])) AS all_keys,
    ARRAY_AGG(IF(counts_as_disruption, STRUCT(worst_severity, worst_status), NULL)
              IGNORE NULLS ORDER BY worst_severity LIMIT 1)[SAFE_OFFSET(0)].worst_status AS worst_status,
    ARRAY_AGG(IF(counts_as_disruption, STRUCT(seconds, cause_category), NULL)
              IGNORE NULLS ORDER BY seconds DESC LIMIT 1)[SAFE_OFFSET(0)].cause_category AS main_cause,
    COUNTIF(is_flap AND state_class = 'disrupted') AS flaps
  FROM counted
  GROUP BY line_id, hour_start
)
SELECT
  * EXCEPT (all_keys),
  SAFE_DIVIDE(disrupted_minutes, in_service_minutes) AS disrupted_share,
  ARRAY(SELECT DISTINCT k FROM UNNEST(all_keys) AS k ORDER BY k) AS incident_keys,
  (SELECT COUNT(DISTINCT k) FROM UNNEST(all_keys) AS k) AS incidents
FROM aggregated;


-- 4. Daily disruption metrics per line (London calendar days) ------------------------------
-- Hours are grouped by London date, so "Monday" means Monday for London passengers.
-- Incidents are counted as distinct incident keys per day, so an incident spanning
-- several hours counts once.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.line_disruption_daily` AS
WITH aggregated AS (
  SELECT
    line_id,
    ANY_VALUE(line_name) AS line_name,
    ANY_VALUE(mode) AS mode,
    DATE(hour_start, 'Europe/London') AS london_date,
    SUM(observed_minutes) AS observed_minutes,
    SUM(in_service_minutes) AS in_service_minutes,
    SUM(disrupted_minutes) AS disrupted_minutes,
    ARRAY_CONCAT_AGG(incident_keys) AS all_keys,
    ARRAY_AGG(IF(disrupted_minutes > 0, STRUCT(disrupted_minutes, worst_status), NULL)
              IGNORE NULLS ORDER BY disrupted_minutes DESC LIMIT 1)[SAFE_OFFSET(0)].worst_status AS worst_status,
    ARRAY_AGG(IF(disrupted_minutes > 0, STRUCT(disrupted_minutes, main_cause), NULL)
              IGNORE NULLS ORDER BY disrupted_minutes DESC LIMIT 1)[SAFE_OFFSET(0)].main_cause AS main_cause,
    SUM(flaps) AS flaps
  FROM `transport-for-london-511318.tfl_transport_analytics.line_disruption_hourly`
  GROUP BY line_id, london_date
)
SELECT
  * EXCEPT (all_keys),
  SAFE_DIVIDE(disrupted_minutes, in_service_minutes) AS disrupted_share,
  (SELECT COUNT(DISTINCT k) FROM UNNEST(all_keys) AS k) AS incidents
FROM aggregated;
