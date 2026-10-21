-- External tables over the stop disruption batches in GCS (docs/endpoints/stop_disruptions.md).
-- Same pattern as analytics/line_status/01_external_tables.sql.

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.ext_stop_disruption_changes` (
  poll_id STRING,
  collected_at_utc STRING,
  collection_window STRING,
  disruption_key STRING,
  fingerprint STRING,
  previous_fingerprint STRING,
  atco_code STRING,
  station_atco_code STRING,
  station_name STRING,
  mode STRING,
  type STRING,
  appearance STRING,
  description STRING,
  from_utc STRING,
  to_utc STRING,
  to_date_raw STRING,
  to_date_kind STRING,
  concerned_lines ARRAY<STRUCT<id STRING, direction STRING>>,
  accessibility_flag BOOL,
  dq_flags ARRAY<STRING>,
  change_type STRING
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/processed/stop_disruption_changes/*.jsonl.gz'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/processed/stop_disruption_changes'
);

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.ext_stop_disruption_coverage` (
  poll_id STRING,
  collected_at_utc STRING,
  collection_window STRING,
  http_status INT64,
  attempts INT64,
  latency_ms INT64,
  valid BOOL,
  records_returned INT64,
  records_kept INT64,
  records_excluded INT64,
  content_hash STRING,
  raw_stored BOOL,
  raw_reason STRING,
  changes_written INT64,
  errors ARRAY<STRING>,
  warnings ARRAY<STRING>
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/coverage/stop_disruptions/*.jsonl.gz'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/coverage/stop_disruptions'
);
