-- Hourly arrivals refresh: load the newest GCS batches into the native tables, then recompute
-- the last 3 complete hours of the serving tables in tfl_portfolio. Run by the
-- tfl-portfolio-refresh timer (:05 past each hour) after the line status and stop disruption
-- refreshes. Idempotent: re-running replaces the same slices, never duplicates them.
-- The first run on empty tables backfills from 2026-10-22 (when arrivals collection started).
--
-- Cost stays flat as history grows: the load reads only today's external partition (and
-- yesterday's just after midnight), and the metrics read only the window they recompute.

DECLARE first_day DATE DEFAULT DATE '2026-10-22';
DECLARE load_from DATE DEFAULT DATE(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR));
-- Complete hours only: batches are published when the hour ends.
DECLARE t1 TIMESTAMP DEFAULT TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR);
-- 3 hours back: an arrival near the end of an hour can be archived in the next hour's batch.
DECLARE t0 TIMESTAMP DEFAULT TIMESTAMP_SUB(t1, INTERVAL 3 HOUR);

-- Backfill on the first run.
IF (SELECT COUNT(*) FROM `transport-for-london-511318.tfl_transport_analytics.arrival_events`
    WHERE source_date >= first_day) = 0 THEN
  SET load_from = first_day;
END IF;


-- 1. Native tables: replace the slices from load_from onwards (one row per Kafka message).

DELETE FROM `transport-for-london-511318.tfl_transport_analytics.arrival_events` WHERE source_date >= load_from;
INSERT INTO `transport-for-london-511318.tfl_transport_analytics.arrival_events`
SELECT
  date AS source_date, mode, line_id, vehicle_id, naptan_id, station_name, platform_name, direction,
  COALESCE(platform_name, direction, '') AS stop_side,
  destination_naptan_id,
  `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(last_snapshot_at), `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(last_expected_arrival), `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(arrived_at),
  detection, visit_key,
  ARRAY(SELECT AS STRUCT c.horizon_minutes, `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(c.predicted_at) AS predicted_at,
                         `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(c.expected_arrival) AS expected_arrival
        FROM UNNEST(checkpoints) AS c),
  `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(archived_at_utc), kafka_partition, kafka_offset
FROM `transport-for-london-511318.tfl_transport_analytics.ext_arrival_events`
WHERE date >= load_from AND event = 'arrival'
QUALIFY ROW_NUMBER() OVER (PARTITION BY kafka_partition, kafka_offset ORDER BY archived_at_utc) = 1;

DELETE FROM `transport-for-london-511318.tfl_transport_analytics.arrival_drops` WHERE source_date >= load_from;
INSERT INTO `transport-for-london-511318.tfl_transport_analytics.arrival_drops`
SELECT
  date, reason, mode, line_id, vehicle_id, naptan_id, station_name,
  COALESCE(platform_name, direction, ''),
  `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(last_snapshot_at), `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(last_expected_arrival),
  TIMESTAMP_DIFF(`transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(last_expected_arrival), `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(last_snapshot_at), MILLISECOND) / 1000,
  `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(archived_at_utc), kafka_partition, kafka_offset
FROM `transport-for-london-511318.tfl_transport_analytics.ext_arrival_drops`
WHERE date >= load_from AND event = 'dropped'
QUALIFY ROW_NUMBER() OVER (PARTITION BY kafka_partition, kafka_offset ORDER BY archived_at_utc) = 1;

DELETE FROM `transport-for-london-511318.tfl_transport_analytics.arrival_coverage` WHERE source_date >= load_from;
INSERT INTO `transport-for-london-511318.tfl_transport_analytics.arrival_coverage`
SELECT
  date, mode, line_id, `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(snapshot_at), predictions, visits_tracked, arrivals, dropped,
  `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(archived_at_utc), kafka_partition, kafka_offset
FROM `transport-for-london-511318.tfl_transport_analytics.ext_arrival_coverage`
WHERE date >= load_from AND event = 'snapshot'
QUALIFY ROW_NUMBER() OVER (PARTITION BY kafka_partition, kafka_offset ORDER BY archived_at_utc) = 1;


-- 2. Serving tables (read by the portfolio, the daily Google Sheet export and MCP).

IF load_from = first_day THEN
  SET t0 = TIMESTAMP(first_day);
END IF;

CREATE TABLE IF NOT EXISTS `transport-for-london-511318.tfl_portfolio.arrival_line_hourly`
PARTITION BY DATE(hour_start) CLUSTER BY mode, line_id
OPTIONS (description = "Hourly service metrics per line: arrivals, average passenger wait, regularity, bunching, drops. From analytics/arrivals.")
AS SELECT * FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_line_hourly`(t0, t1) LIMIT 0;

CREATE TABLE IF NOT EXISTS `transport-for-london-511318.tfl_portfolio.arrival_stop_hourly`
PARTITION BY DATE(hour_start) CLUSTER BY mode, line_id
OPTIONS (description = "Hourly service metrics per line, stop and platform/direction. From analytics/arrivals.")
AS SELECT * FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_stop_hourly`(t0, t1) LIMIT 0;

CREATE TABLE IF NOT EXISTS `transport-for-london-511318.tfl_portfolio.arrival_accuracy_hourly`
PARTITION BY DATE(hour_start) CLUSTER BY mode, line_id
OPTIONS (description = "Hourly prediction error per line and horizon (Tube 'At Platform' arrivals). From analytics/arrivals.")
AS SELECT * FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_accuracy_hourly`(t0, t1) LIMIT 0;

DELETE FROM `transport-for-london-511318.tfl_portfolio.arrival_line_hourly` WHERE hour_start >= t0;
INSERT INTO `transport-for-london-511318.tfl_portfolio.arrival_line_hourly`
SELECT * FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_line_hourly`(t0, t1);

DELETE FROM `transport-for-london-511318.tfl_portfolio.arrival_stop_hourly` WHERE hour_start >= t0;
INSERT INTO `transport-for-london-511318.tfl_portfolio.arrival_stop_hourly`
SELECT * FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_stop_hourly`(t0, t1);

DELETE FROM `transport-for-london-511318.tfl_portfolio.arrival_accuracy_hourly` WHERE hour_start >= t0;
INSERT INTO `transport-for-london-511318.tfl_portfolio.arrival_accuracy_hourly`
SELECT * FROM `transport-for-london-511318.tfl_transport_analytics.fn_arrival_accuracy_hourly`(t0, t1);
