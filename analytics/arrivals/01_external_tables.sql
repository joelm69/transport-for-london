-- External tables over the arrival batches in GCS (docs/endpoints/arrivals.md, section 3).
-- Written by src/streaming/archiver.py; same pattern as analytics/stop_disruptions.
-- Times are ISO strings in the files and are parsed in the views.

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.ext_arrival_events` (
  event STRING,
  mode STRING,
  line_id STRING,
  vehicle_id STRING,
  naptan_id STRING,
  station_name STRING,
  platform_name STRING,
  direction STRING,
  destination_naptan_id STRING,
  last_snapshot_at STRING,
  last_expected_arrival STRING,
  arrived_at STRING,
  detection STRING,
  visit_key STRING,
  checkpoints ARRAY<STRUCT<horizon_minutes INT64, predicted_at STRING, expected_arrival STRING>>,
  archived_at_utc STRING,
  kafka_partition INT64,
  kafka_offset INT64
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/processed/arrival_events/*.jsonl.gz'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/processed/arrival_events'
);

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.ext_arrival_drops` (
  event STRING,
  reason STRING,
  mode STRING,
  line_id STRING,
  vehicle_id STRING,
  naptan_id STRING,
  station_name STRING,
  platform_name STRING,
  direction STRING,
  destination_naptan_id STRING,
  last_snapshot_at STRING,
  last_expected_arrival STRING,
  archived_at_utc STRING,
  kafka_partition INT64,
  kafka_offset INT64
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/processed/arrival_drops/*.jsonl.gz'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/processed/arrival_drops'
);

-- One row per processed line snapshot: the proof of observed time.
CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.ext_arrival_coverage` (
  event STRING,
  mode STRING,
  line_id STRING,
  snapshot_at STRING,
  predictions INT64,
  visits_tracked INT64,
  arrivals INT64,
  dropped INT64,
  archived_at_utc STRING,
  kafka_partition INT64,
  kafka_offset INT64
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/coverage/arrivals/*.jsonl.gz'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/coverage/arrivals'
);
