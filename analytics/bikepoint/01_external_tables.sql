-- External tables over the BikePoint batches in GCS (docs/endpoints/bikepoint.md).
-- Same pattern as analytics/line_status/01_external_tables.sql.

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.ext_bikepoint_changes` (
  poll_id STRING,
  collected_at_utc STRING,
  collection_window STRING,
  station_id STRING,
  fingerprint STRING,
  previous_fingerprint STRING,
  station_name STRING,
  bikes INT64,
  standard_bikes INT64,
  e_bikes INT64,
  empty_docks INT64,
  docks INT64,
  out_of_use_docks INT64,
  installed BOOL,
  locked BOOL,
  temporary BOOL,
  lat FLOAT64,
  lon FLOAT64,
  modified_utc STRING,
  dq_flags ARRAY<STRING>,
  change_type STRING
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/processed/bikepoint_changes/*.jsonl.gz'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/processed/bikepoint_changes'
);

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.ext_bikepoint_coverage` (
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
  stations INT64,
  bikes INT64,
  e_bikes INT64,
  empty_docks INT64,
  docks INT64,
  empty_stations INT64,
  full_stations INT64,
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
  uris = ['gs://transport_for_london_bucket/coverage/bikepoint/*.jsonl.gz'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/coverage/bikepoint'
);
