-- External tables over the line status batches in GCS (docs/endpoints/line_status.md).
-- BigQuery reads the gzip JSONL files in place: new hourly batches appear automatically.
-- The `date` column comes from the date=YYYY-MM-DD folder (hive partitioning), so queries
-- filtered on `date` only read the matching folders. Manifests (*.manifest.json) are
-- excluded by the *.jsonl.gz pattern.

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.ext_line_status_changes` (
  poll_id STRING,
  collected_at_utc STRING,
  collection_window STRING,
  line_id STRING,
  line_name STRING,
  mode STRING,
  status_index INT64,
  severity INT64,
  severity_description STRING,
  severity_class STRING,
  reason STRING,
  incident_key STRING,
  status_fingerprint STRING,
  event_from_utc STRING,
  to_date_raw STRING,
  change_type STRING,
  previous_fingerprints ARRAY<STRING>,
  resolved_incident_keys ARRAY<STRING>,
  dq_flags ARRAY<STRING>
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/processed/line_status_changes/*.jsonl.gz'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/processed/line_status_changes'
);

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.ext_line_status_coverage` (
  poll_id STRING,
  collected_at_utc STRING,
  collection_window STRING,
  http_status INT64,
  attempts INT64,
  latency_ms INT64,
  valid BOOL,
  lines_returned INT64,
  statuses_returned INT64,
  changes_written INT64,
  errors ARRAY<STRING>,
  warnings ARRAY<STRING>
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/coverage/line_status/*.jsonl.gz'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/coverage/line_status'
);
