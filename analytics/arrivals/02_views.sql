-- Arrival views: clean events -> observation sessions -> headways -> hourly metrics and
-- prediction accuracy. Design: docs/endpoints/arrivals.md (sections 2 and 5).
-- Headways are measured only inside an observation session, so a collection gap never turns
-- into a fake long headway. Precision: arrival times are about +-30 s (snapshot spacing).

-- 1. Clean arrivals: one row per Kafka message (crash duplicates removed), one per visit_key.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.arrivals` AS
WITH parsed AS (
  SELECT
    * EXCEPT (arrived_at, last_snapshot_at, last_expected_arrival, archived_at_utc, checkpoints),
    TIMESTAMP(arrived_at) AS arrived_at,
    TIMESTAMP(last_snapshot_at) AS last_snapshot_at,
    TIMESTAMP(last_expected_arrival) AS last_expected_arrival,
    TIMESTAMP(archived_at_utc) AS archived_at,
    COALESCE(platform_name, direction, '') AS stop_side,
    ARRAY(SELECT AS STRUCT c.horizon_minutes, TIMESTAMP(c.predicted_at) AS predicted_at,
                           TIMESTAMP(c.expected_arrival) AS expected_arrival
          FROM UNNEST(checkpoints) AS c) AS checkpoints
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_arrival_events`
  WHERE event = 'arrival'
)
SELECT * FROM parsed
QUALIFY ROW_NUMBER() OVER (PARTITION BY kafka_partition, kafka_offset ORDER BY archived_at) = 1
    AND ROW_NUMBER() OVER (PARTITION BY visit_key ORDER BY kafka_partition, kafka_offset) = 1;


-- 2. Observation sessions per line: consecutive snapshots less than 5 minutes apart.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.arrival_sessions` AS
WITH snapshots AS (
  SELECT DISTINCT mode, line_id, TIMESTAMP(snapshot_at) AS snapshot_at
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_arrival_coverage`
  WHERE event = 'snapshot'
),
marked AS (
  SELECT *,
    IF(TIMESTAMP_DIFF(snapshot_at,
                      LAG(snapshot_at) OVER (PARTITION BY line_id ORDER BY snapshot_at), SECOND) <= 300,
       0, 1) AS starts_session
  FROM snapshots
),
numbered AS (
  SELECT *, SUM(starts_session) OVER (PARTITION BY line_id ORDER BY snapshot_at) AS session_no
  FROM marked
)
SELECT
  mode, line_id, session_no,
  MIN(snapshot_at) AS session_start,
  MAX(snapshot_at) AS session_end,
  COUNT(*) AS snapshots
FROM numbered
GROUP BY mode, line_id, session_no;


-- 3. Observed minutes per line and hour (the denominator for rates).

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.arrival_observed_hourly` AS
SELECT
  mode, line_id, hour_start,
  SUM(TIMESTAMP_DIFF(LEAST(session_end, TIMESTAMP_ADD(hour_start, INTERVAL 1 HOUR)),
                     GREATEST(session_start, hour_start), SECOND)) / 60 AS observed_minutes
FROM `transport-for-london-511318.tfl_transport_analytics.arrival_sessions`,
UNNEST(GENERATE_TIMESTAMP_ARRAY(TIMESTAMP_TRUNC(session_start, HOUR),
                                TIMESTAMP_TRUNC(session_end, HOUR), INTERVAL 1 HOUR)) AS hour_start
WHERE session_end > session_start
GROUP BY mode, line_id, hour_start;


-- 4. Headways: time since the previous arrival of the same line at the same stop and side
--    (platform for rail, direction for buses), within one observation session.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.arrival_headways` AS
WITH in_session AS (
  SELECT a.*, s.session_no
  FROM `transport-for-london-511318.tfl_transport_analytics.arrivals` AS a
  JOIN `transport-for-london-511318.tfl_transport_analytics.arrival_sessions` AS s
    ON a.line_id = s.line_id
   AND a.arrived_at BETWEEN TIMESTAMP_SUB(s.session_start, INTERVAL 2 MINUTE)
                        AND TIMESTAMP_ADD(s.session_end, INTERVAL 2 MINUTE)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY a.visit_key ORDER BY s.session_no) = 1
)
SELECT *,
  -- Two vehicles cannot share a platform or stop within 30 s: flag, do not silently drop (C5).
  headway_seconds < 30 AS suspect,
  -- Over an hour with the line observed but nothing calling here: the platform or stop was not
  -- in use (closure, terminus platform, curtailment), not a wait anyone experiences. Measured
  -- 2026-10-22: one Jubilee platform had a 294-minute "headway" that alone tripled the line's
  -- average wait. Kept for counting closures, excluded from wait metrics.
  headway_seconds > 3600 AS service_gap
FROM (
  SELECT
    mode, line_id, naptan_id, station_name, stop_side, vehicle_id, session_no,
    arrived_at,
    LAG(arrived_at) OVER w AS previous_arrived_at,
    TIMESTAMP_DIFF(arrived_at, LAG(arrived_at) OVER w, SECOND) AS headway_seconds
  FROM in_session
  WINDOW w AS (PARTITION BY line_id, naptan_id, stop_side, session_no ORDER BY arrived_at)
)
WHERE previous_arrived_at IS NOT NULL;


-- 5. Hourly service metrics per line and stop side. A headway belongs to the hour in which
--    the later vehicle arrived.

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.arrival_stop_hourly` AS
WITH h AS (
  SELECT *, TIMESTAMP_TRUNC(arrived_at, HOUR) AS hour_start
  FROM `transport-for-london-511318.tfl_transport_analytics.arrival_headways`
  WHERE NOT suspect AND NOT service_gap
),
medians AS (
  SELECT line_id, naptan_id, stop_side, hour_start,
         APPROX_QUANTILES(headway_seconds, 2)[OFFSET(1)] AS median_headway_seconds
  FROM h
  GROUP BY line_id, naptan_id, stop_side, hour_start
)
SELECT
  h.mode, h.line_id, h.naptan_id, ANY_VALUE(h.station_name) AS station_name, h.stop_side,
  h.hour_start,
  COUNT(*) AS headways,
  AVG(h.headway_seconds) AS mean_headway_seconds,
  ANY_VALUE(m.median_headway_seconds) AS median_headway_seconds,
  -- Passengers arriving at random: E[wait] = sum(h^2) / (2 * sum(h)).
  SAFE_DIVIDE(SUM(POW(h.headway_seconds, 2)), 2 * SUM(h.headway_seconds)) AS average_wait_seconds,
  SAFE_DIVIDE(STDDEV_SAMP(h.headway_seconds), AVG(h.headway_seconds)) AS headway_cv,
  COUNTIF(h.headway_seconds < 0.5 * m.median_headway_seconds) AS bunched_headways,
  SAFE_DIVIDE(COUNTIF(h.headway_seconds < 0.5 * m.median_headway_seconds), COUNT(*)) AS bunching_rate
FROM h
JOIN medians AS m USING (line_id, naptan_id, stop_side, hour_start)
GROUP BY h.mode, h.line_id, h.naptan_id, h.stop_side, h.hour_start;


-- 6. Hourly metrics per line (all stops), with observed time and the dropped-visit share (C4).

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.arrival_line_hourly` AS
WITH hw AS (
  SELECT h.mode, h.line_id, TIMESTAMP_TRUNC(h.arrived_at, HOUR) AS hour_start, h.headway_seconds,
         h.headway_seconds < 0.5 * m.median_headway_seconds AS bunched
  FROM `transport-for-london-511318.tfl_transport_analytics.arrival_headways` AS h
  JOIN (
    SELECT line_id, naptan_id, stop_side, hour_start, median_headway_seconds
    FROM `transport-for-london-511318.tfl_transport_analytics.arrival_stop_hourly`
  ) AS m
    ON h.line_id = m.line_id AND h.naptan_id = m.naptan_id AND h.stop_side = m.stop_side
   AND TIMESTAMP_TRUNC(h.arrived_at, HOUR) = m.hour_start
  WHERE NOT h.suspect AND NOT h.service_gap
),
service AS (
  SELECT mode, line_id, hour_start,
         COUNT(*) AS headways,
         SAFE_DIVIDE(SUM(POW(headway_seconds, 2)), 2 * SUM(headway_seconds)) AS average_wait_seconds,
         SAFE_DIVIDE(STDDEV_SAMP(headway_seconds), AVG(headway_seconds)) AS headway_cv,
         SAFE_DIVIDE(COUNTIF(bunched), COUNT(*)) AS bunching_rate
  FROM hw
  GROUP BY mode, line_id, hour_start
),
arrivals AS (
  SELECT line_id, TIMESTAMP_TRUNC(arrived_at, HOUR) AS hour_start, COUNT(*) AS arrivals
  FROM `transport-for-london-511318.tfl_transport_analytics.arrivals`
  GROUP BY line_id, hour_start
),
drops AS (
  -- dropped: every visit that ended without an arrival. dropped_near: those due within
  -- 5 minutes when they vanished, i.e. likely missed or cancelled calls. Far drops are mostly
  -- TfL re-planning trains 15-30 minutes ahead (measured 2026-10-22) and say little about service.
  SELECT line_id, TIMESTAMP_TRUNC(TIMESTAMP(last_snapshot_at), HOUR) AS hour_start,
         COUNT(DISTINCT FORMAT('%d-%d', kafka_partition, kafka_offset)) AS dropped,
         COUNT(DISTINCT IF(TIMESTAMP_DIFF(TIMESTAMP(last_expected_arrival), TIMESTAMP(last_snapshot_at), SECOND) <= 300,
                           FORMAT('%d-%d', kafka_partition, kafka_offset), NULL)) AS dropped_near
  FROM `transport-for-london-511318.tfl_transport_analytics.ext_arrival_drops`
  GROUP BY line_id, hour_start
),
gaps AS (
  SELECT line_id, TIMESTAMP_TRUNC(arrived_at, HOUR) AS hour_start, COUNTIF(service_gap) AS service_gaps
  FROM `transport-for-london-511318.tfl_transport_analytics.arrival_headways`
  GROUP BY line_id, hour_start
)
SELECT
  o.mode, o.line_id, o.hour_start, o.observed_minutes,
  COALESCE(a.arrivals, 0) AS arrivals,
  COALESCE(d.dropped, 0) AS dropped,
  COALESCE(d.dropped_near, 0) AS dropped_near,
  SAFE_DIVIDE(COALESCE(d.dropped_near, 0), COALESCE(a.arrivals, 0) + COALESCE(d.dropped_near, 0)) AS dropped_near_share,
  COALESCE(g.service_gaps, 0) AS service_gaps,
  s.headways, s.average_wait_seconds, s.headway_cv, s.bunching_rate
FROM `transport-for-london-511318.tfl_transport_analytics.arrival_observed_hourly` AS o
LEFT JOIN arrivals AS a USING (line_id, hour_start)
LEFT JOIN drops AS d USING (line_id, hour_start)
LEFT JOIN service AS s USING (line_id, hour_start)
LEFT JOIN gaps AS g USING (line_id, hour_start);


-- 7. Prediction accuracy: actual arrival minus TfL's prediction at 10, 5 and 2 minutes out.
--    Positive = the vehicle arrived later than predicted.
--    Only arrivals confirmed by "At Platform" (Tube). Where arrival is inferred from a vanishing
--    prediction (tram, bus, some Tube), arrived_at IS the last prediction, so the error would be
--    near zero by construction (measured 2026-10-22: tram 2-minute median error 0 s).

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.arrival_prediction_errors` AS
SELECT
  a.mode, a.line_id, a.naptan_id, a.visit_key, a.arrived_at,
  TIMESTAMP_TRUNC(a.arrived_at, HOUR) AS hour_start,
  c.horizon_minutes, c.predicted_at, c.expected_arrival,
  TIMESTAMP_DIFF(a.arrived_at, c.expected_arrival, MILLISECOND) / 1000 AS error_seconds
FROM `transport-for-london-511318.tfl_transport_analytics.arrivals` AS a,
UNNEST(a.checkpoints) AS c
WHERE a.detection = 'at_platform';
