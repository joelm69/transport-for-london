-- External tables over the network-wide bus polls (scripts/bus_network_snapshot.py, every 30
-- minutes): small summaries written to GCS as gzip NDJSON, one file per poll. `date` comes from
-- the folder name (hive partitioning), so a query filtering on it reads only those days' files.
-- The slim raw polls (raw/bus_network/, deleted after 30 days) are not exposed here: they exist
-- to recompute a summary if its logic changes.

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.bus_network_vehicles` (
  snapshot_at TIMESTAMP,
  vehicle_id STRING,
  line_id STRING,
  direction STRING,
  next_naptan_id STRING,
  next_stop STRING,
  eta_seconds INT64,
  destination STRING
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/processed/bus_network/vehicles/*'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/processed/bus_network/vehicles',
  require_hive_partition_filter = true
);

CREATE OR REPLACE EXTERNAL TABLE `transport-for-london-511318.tfl_transport_analytics.bus_network_routes` (
  snapshot_at TIMESTAMP,
  line_id STRING,
  direction STRING,
  buses INT64,
  gaps INT64,
  median_gap_seconds FLOAT64,
  max_gap_seconds INT64,
  bunched_gaps INT64
)
WITH PARTITION COLUMNS (date DATE)
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  compression = 'GZIP',
  uris = ['gs://transport_for_london_bucket/processed/bus_network/routes/*'],
  hive_partition_uri_prefix = 'gs://transport_for_london_bucket/processed/bus_network/routes',
  require_hive_partition_filter = true
);
