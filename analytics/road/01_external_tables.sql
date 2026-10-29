-- External tables over the road batches in GCS (docs/endpoints/road.md).
-- Same pattern as analytics/line_status/01_external_tables.sql.

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.ext_road_disruption_changes` (
  poll_id STRING,
  collected_at_utc STRING,
  collection_window STRING,
  disruption_id STRING,
  fingerprint STRING,
  previous_fingerprint STRING,
  category STRING,
  sub_category STRING,
  is_incident BOOL,
  severity STRING,
  level_of_interest STRING,
  location STRING,
  comments STRING,
  current_update STRING,
  corridor_ids ARRAY<STRING>,
  closed_streets ARRAY<STRING>,
  lat FLOAT64,
  lon FLOAT64,
  start_utc STRING,
  end_utc STRING,
  last_modified_utc STRING,
  is_provisional BOOL,
  dq_flags ARRAY<STRING>,
  change_type STRING
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/processed/road_disruption_changes/*.jsonl.gz'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/processed/road_disruption_changes'
);

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.ext_road_corridor_changes` (
  poll_id STRING,
  collected_at_utc STRING,
  collection_window STRING,
  corridor_id STRING,
  fingerprint STRING,
  previous_fingerprint STRING,
  corridor_name STRING,
  status STRING,
  status_description STRING,
  change_type STRING
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/processed/road_corridor_changes/*.jsonl.gz'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/processed/road_corridor_changes'
);

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.ext_road_coverage` (
  poll_id STRING,
  collected_at_utc STRING,
  collection_window STRING,
  disruption_http_status INT64,
  corridor_http_status INT64,
  attempts INT64,
  latency_ms INT64,
  disruptions_valid BOOL,
  corridors_valid BOOL,
  valid BOOL,
  disruptions INT64,
  incidents INT64,
  corridors INT64,
  corridors_not_good INT64,
  records_excluded INT64,
  raw_stored BOOL,
  raw_reason STRING,
  disruption_changes INT64,
  corridor_changes INT64,
  changes_written INT64,
  errors ARRAY<STRING>,
  warnings ARRAY<STRING>
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/coverage/road/*.jsonl.gz'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/coverage/road'
);
