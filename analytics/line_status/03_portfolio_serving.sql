-- Serving table for the public portfolio, in its own dataset (tfl_portfolio).
-- The portfolio's service account can read only this dataset: no bucket access, no raw data.
-- Refreshed hourly by a BigQuery scheduled query ("TfL portfolio hourly refresh").
--
-- Cost note: the source views read every change/coverage file in GCS. That is a few MB per
-- month of collection; if the archive grows large, switch this to an incremental MERGE of
-- recent dates only.

CREATE OR REPLACE TABLE `transport-for-london-511318.tfl_portfolio.line_disruption_hourly`
OPTIONS (description = "Hourly line disruption metrics, refreshed hourly from tfl_transport_analytics.line_disruption_hourly")
AS
SELECT
  line_id, line_name, mode, hour_start,
  observed_minutes, in_service_minutes, disrupted_minutes, disrupted_share,
  incidents, incident_keys, worst_status, main_cause, causes, flaps
FROM `transport-for-london-511318.tfl_transport_analytics.line_disruption_hourly`;
