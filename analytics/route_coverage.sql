CREATE OR REPLACE VIEW
  `transport-for-london-511318.tfl_transport_analytics.route_coverage`
AS
SELECT
  collection_run_id,
  line_id,
  direction,
  branch_id,
  COUNT(DISTINCT stop_id) AS distinct_stops,
  COUNT(*) AS stop_occurrences,
  COUNT(DISTINCT sequence_number) AS distinct_sequence_numbers,
  MIN(stop_order) AS first_stop_order,
  MAX(stop_order) AS last_stop_order
FROM
  `transport-for-london-511318.tfl_transport_analytics.route_stop_occurrences`
GROUP BY
  collection_run_id,
  line_id,
  direction,
  branch_id;
