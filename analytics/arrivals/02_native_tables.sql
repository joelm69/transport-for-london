-- Native tables for arrivals: parsed types, one row per Kafka message, partitioned by the date
-- of the GCS batch (source_date) so each hourly load replaces only today's slice.
-- Loaded by 04_serving_refresh.sql. Why not query the external tables directly: every query
-- would re-read and re-parse all the compressed JSON in GCS, and the cost would grow daily.

CREATE TABLE IF NOT EXISTS `transport-for-london-511318.tfl_transport_analytics.arrival_events` (
  source_date DATE,
  mode STRING,
  line_id STRING,
  vehicle_id STRING,
  naptan_id STRING,
  station_name STRING,
  platform_name STRING,
  direction STRING,
  stop_side STRING,            -- platform for rail, direction for buses: where headways are measured
  destination_naptan_id STRING,
  last_snapshot_at TIMESTAMP,
  last_expected_arrival TIMESTAMP,
  arrived_at TIMESTAMP,
  detection STRING,
  visit_key STRING,
  checkpoints ARRAY<STRUCT<horizon_minutes INT64, predicted_at TIMESTAMP, expected_arrival TIMESTAMP>>,
  archived_at TIMESTAMP,
  kafka_partition INT64,
  kafka_offset INT64
)
PARTITION BY source_date
CLUSTER BY mode, line_id;

CREATE TABLE IF NOT EXISTS `transport-for-london-511318.tfl_transport_analytics.arrival_drops` (
  source_date DATE,
  reason STRING,
  mode STRING,
  line_id STRING,
  vehicle_id STRING,
  naptan_id STRING,
  station_name STRING,
  stop_side STRING,
  last_snapshot_at TIMESTAMP,
  last_expected_arrival TIMESTAMP,
  lead_seconds FLOAT64,        -- how far ahead the visit was due when it vanished
  archived_at TIMESTAMP,
  kafka_partition INT64,
  kafka_offset INT64
)
PARTITION BY source_date
CLUSTER BY mode, line_id;

CREATE TABLE IF NOT EXISTS `transport-for-london-511318.tfl_transport_analytics.arrival_coverage` (
  source_date DATE,
  mode STRING,
  line_id STRING,
  snapshot_at TIMESTAMP,
  predictions INT64,
  visits_tracked INT64,
  arrivals INT64,
  dropped INT64,
  archived_at TIMESTAMP,
  kafka_partition INT64,
  kafka_offset INT64
)
PARTITION BY source_date
CLUSTER BY mode, line_id;

-- TfL timestamps as TIMESTAMP. Bus predictions carry 7 decimal places of seconds
-- ('2026-10-22T17:32:17.1305498Z', seen 2026-10-22) and TIMESTAMP() accepts at most 6:
-- keep microseconds, drop the rest (far below the +-30 s precision of arrival times).
CREATE OR REPLACE FUNCTION `transport-for-london-511318.tfl_transport_analytics.parse_tfl_time`(value STRING) AS (
  TIMESTAMP(REGEXP_REPLACE(value, r'(\.\d{6})\d+', r'\1'))
);
