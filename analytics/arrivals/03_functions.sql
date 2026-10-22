-- Arrival metrics as table functions over a time window [t0, t1), reading the native tables in
-- 02_native_tables.sql. The hourly refresh (04_serving_refresh.sql) calls them for the last few
-- hours only, so its cost stays flat as history grows; the views at the end call them over all
-- history for ad-hoc analysis. One copy of the logic for both.
-- Design: docs/endpoints/arrivals.md (sections 2 and 5).
--
-- Headways are measured only inside an observation session, so a collection gap never turns
-- into a fake long headway. Precision: arrival times are about +-30 s (snapshot spacing).
-- Lookbacks: sessions start 1 h before t0 and headways look 2 h back, so a window boundary never
-- cuts a headway. Partition filters (source_date) keep each call to the days it needs.

-- 1. Observation sessions per line: consecutive snapshots less than 5 minutes apart.

CREATE OR REPLACE TABLE FUNCTION `transport-for-london-511318.tfl_transport_analytics.fn_arrival_sessions`(t0 TIMESTAMP, t1 TIMESTAMP) AS (
  WITH snapshots AS (
    SELECT DISTINCT mode, line_id, snapshot_at
    FROM `transport-for-london-511318.tfl_transport_analytics.arrival_coverage`
    WHERE source_date BETWEEN DATE_SUB(DATE(t0), INTERVAL 1 DAY) AND DATE_ADD(DATE(t1), INTERVAL 1 DAY)
      AND snapshot_at >= TIMESTAMP_SUB(t0, INTERVAL 1 HOUR) AND snapshot_at < t1
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
  SELECT mode, line_id, session_no,
         MIN(snapshot_at) AS session_start, MAX(snapshot_at) AS session_end, COUNT(*) AS snapshots
  FROM numbered
  GROUP BY mode, line_id, session_no
);


-- 2. Headways: time since the previous arrival of the same line at the same stop and side
--    (platform for rail, direction for buses), within one observation session. Rows for
--    arrivals in [t0, t1).

CREATE OR REPLACE TABLE FUNCTION `transport-for-london-511318.tfl_transport_analytics.fn_arrival_headways`(t0 TIMESTAMP, t1 TIMESTAMP) AS (
  WITH arrivals AS (
    SELECT *
    FROM `transport-for-london-511318.tfl_transport_analytics.arrival_events`
    WHERE source_date BETWEEN DATE_SUB(DATE(t0), INTERVAL 1 DAY) AND DATE_ADD(DATE(t1), INTERVAL 1 DAY)
      AND arrived_at >= TIMESTAMP_SUB(t0, INTERVAL 2 HOUR) AND arrived_at < t1
    -- One arrival per visit (a visit_key can repeat only after a crash replay).
    QUALIFY ROW_NUMBER() OVER (PARTITION BY visit_key ORDER BY kafka_partition, kafka_offset) = 1
  ),
  in_session AS (
    SELECT a.*, s.session_no
    FROM arrivals AS a
    JOIN `transport-for-london-511318.tfl_transport_analytics.fn_arrival_sessions`(TIMESTAMP_SUB(t0, INTERVAL 2 HOUR), t1) AS s
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
    -- average wait. Kept for counting closures, excluded from wait metrics. (Gaps longer than the
    -- 2-hour lookback are not seen at all.)
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
  WHERE previous_arrived_at IS NOT NULL AND arrived_at >= t0
);


-- 3. Hourly service metrics per line and stop side. A headway belongs to the hour in which
--    the later vehicle arrived. Bunched: under half the median headway for that stop and hour.

CREATE OR REPLACE TABLE FUNCTION `transport-for-london-511318.tfl_transport_analytics.fn_arrival_stop_hourly`(t0 TIMESTAMP, t1 TIMESTAMP) AS (
  WITH h AS (
    SELECT *, TIMESTAMP_TRUNC(arrived_at, HOUR) AS hour_start
    FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_headways`(t0, t1)
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
  GROUP BY h.mode, h.line_id, h.naptan_id, h.stop_side, h.hour_start
);


-- 4. Hourly metrics per line (all stops): observed time, arrivals, drops (near-term share, C4),
--    service gaps, and the passenger-weighted wait, regularity and bunching.

CREATE OR REPLACE TABLE FUNCTION `transport-for-london-511318.tfl_transport_analytics.fn_arrival_line_hourly`(t0 TIMESTAMP, t1 TIMESTAMP) AS (
  WITH headways AS (
    SELECT * FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_headways`(t0, t1)
  ),
  stop_medians AS (
    SELECT line_id, naptan_id, stop_side, TIMESTAMP_TRUNC(arrived_at, HOUR) AS hour_start,
           APPROX_QUANTILES(headway_seconds, 2)[OFFSET(1)] AS median_headway_seconds
    FROM headways
    WHERE NOT suspect AND NOT service_gap
    GROUP BY line_id, naptan_id, stop_side, hour_start
  ),
  service AS (
    SELECT h.mode, h.line_id, TIMESTAMP_TRUNC(h.arrived_at, HOUR) AS hour_start,
           COUNT(*) AS headways,
           SAFE_DIVIDE(SUM(POW(h.headway_seconds, 2)), 2 * SUM(h.headway_seconds)) AS average_wait_seconds,
           SAFE_DIVIDE(STDDEV_SAMP(h.headway_seconds), AVG(h.headway_seconds)) AS headway_cv,
           SAFE_DIVIDE(COUNTIF(h.headway_seconds < 0.5 * m.median_headway_seconds), COUNT(*)) AS bunching_rate
    FROM headways AS h
    JOIN stop_medians AS m
      ON h.line_id = m.line_id AND h.naptan_id = m.naptan_id AND h.stop_side = m.stop_side
     AND TIMESTAMP_TRUNC(h.arrived_at, HOUR) = m.hour_start
    WHERE NOT h.suspect AND NOT h.service_gap
    GROUP BY h.mode, h.line_id, hour_start
  ),
  gaps AS (
    SELECT line_id, TIMESTAMP_TRUNC(arrived_at, HOUR) AS hour_start, COUNTIF(service_gap) AS service_gaps
    FROM headways
    GROUP BY line_id, hour_start
  ),
  observed AS (
    SELECT mode, line_id, hour_start,
           SUM(TIMESTAMP_DIFF(LEAST(session_end, TIMESTAMP_ADD(hour_start, INTERVAL 1 HOUR)),
                              GREATEST(session_start, hour_start), SECOND)) / 60 AS observed_minutes
    FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_sessions`(t0, t1),
    UNNEST(GENERATE_TIMESTAMP_ARRAY(TIMESTAMP_TRUNC(session_start, HOUR),
                                    TIMESTAMP_TRUNC(session_end, HOUR), INTERVAL 1 HOUR)) AS hour_start
    WHERE session_end > session_start AND hour_start >= t0 AND hour_start < t1
    GROUP BY mode, line_id, hour_start
  ),
  arrivals AS (
    SELECT line_id, TIMESTAMP_TRUNC(arrived_at, HOUR) AS hour_start, COUNT(DISTINCT visit_key) AS arrivals
    FROM `transport-for-london-511318.tfl_transport_analytics.arrival_events`
    WHERE source_date BETWEEN DATE_SUB(DATE(t0), INTERVAL 1 DAY) AND DATE_ADD(DATE(t1), INTERVAL 1 DAY)
      AND arrived_at >= t0 AND arrived_at < t1
    GROUP BY line_id, hour_start
  ),
  drops AS (
    -- dropped: every visit that ended without an arrival. dropped_near: those due within
    -- 5 minutes when they vanished, i.e. likely missed or cancelled calls. Far drops are mostly
    -- TfL re-planning trains 15-30 minutes ahead (measured 2026-10-22) and say little about service.
    SELECT line_id, TIMESTAMP_TRUNC(last_snapshot_at, HOUR) AS hour_start,
           COUNT(*) AS dropped, COUNTIF(lead_seconds <= 300) AS dropped_near
    FROM `transport-for-london-511318.tfl_transport_analytics.arrival_drops`
    WHERE source_date BETWEEN DATE_SUB(DATE(t0), INTERVAL 1 DAY) AND DATE_ADD(DATE(t1), INTERVAL 1 DAY)
      AND last_snapshot_at >= t0 AND last_snapshot_at < t1
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
  FROM observed AS o
  LEFT JOIN arrivals AS a USING (line_id, hour_start)
  LEFT JOIN drops AS d USING (line_id, hour_start)
  LEFT JOIN service AS s USING (line_id, hour_start)
  LEFT JOIN gaps AS g USING (line_id, hour_start)
);


-- 5. Prediction accuracy per line, hour and horizon: actual arrival minus TfL's prediction at
--    10, 5 and 2 minutes out (positive = later than predicted).
--    Only arrivals confirmed by "At Platform" (Tube). Where arrival is inferred from a vanishing
--    prediction (tram, bus, some Tube), arrived_at IS the last prediction, so the error would be
--    near zero by construction (measured 2026-10-22: tram 2-minute median error 0 s).

CREATE OR REPLACE TABLE FUNCTION `transport-for-london-511318.tfl_transport_analytics.fn_arrival_accuracy_hourly`(t0 TIMESTAMP, t1 TIMESTAMP) AS (
  WITH errors AS (
    SELECT a.mode, a.line_id, TIMESTAMP_TRUNC(a.arrived_at, HOUR) AS hour_start, c.horizon_minutes,
           TIMESTAMP_DIFF(a.arrived_at, c.expected_arrival, MILLISECOND) / 1000 AS error_seconds
    FROM `transport-for-london-511318.tfl_transport_analytics.arrival_events` AS a, UNNEST(a.checkpoints) AS c
    WHERE a.source_date BETWEEN DATE_SUB(DATE(t0), INTERVAL 1 DAY) AND DATE_ADD(DATE(t1), INTERVAL 1 DAY)
      AND a.arrived_at >= t0 AND a.arrived_at < t1
      AND a.detection = 'at_platform'
  )
  SELECT mode, line_id, hour_start, horizon_minutes,
         COUNT(*) AS predictions,
         AVG(error_seconds) AS mean_error_seconds,
         APPROX_QUANTILES(ABS(error_seconds), 2)[OFFSET(1)] AS median_abs_error_seconds,
         APPROX_QUANTILES(ABS(error_seconds), 10)[OFFSET(9)] AS p90_abs_error_seconds
  FROM errors
  GROUP BY mode, line_id, hour_start, horizon_minutes
);


-- 6. Views over all history (ad-hoc analysis; each use scans everything, so prefer the
--    tfl_portfolio serving tables for anything regular).

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.arrival_headways` AS
SELECT * FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_headways`(TIMESTAMP '2026-10-22', CURRENT_TIMESTAMP());

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.arrival_stop_hourly` AS
SELECT * FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_stop_hourly`(TIMESTAMP '2026-10-22', CURRENT_TIMESTAMP());

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.arrival_line_hourly` AS
SELECT * FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_line_hourly`(TIMESTAMP '2026-10-22', CURRENT_TIMESTAMP());

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.arrival_accuracy_hourly` AS
SELECT * FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_accuracy_hourly`(TIMESTAMP '2026-10-22', CURRENT_TIMESTAMP());
