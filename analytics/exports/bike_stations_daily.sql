-- Google Sheet tab: bike_stations_daily
-- One row per Santander Cycles docking station per completed London day: minutes with no bike
-- to take (empty) and nowhere to return one (full), in all and in the weekday peaks (07-10 and
-- 16-19), plus time-weighted average bikes, e-bikes and docks out of use. Feeds the stations
-- that fail riders most and the rebalancing ranking. The last 30 days are kept in the Sheet
-- (BigQuery keeps everything); collection began 29 October 2026, 17:10 UTC.
--
-- Source: the BikePoint collector (every 2 minutes, changed stations only), through
-- tfl_transport_analytics.bikepoint_station_daily (analytics/bikepoint/02_views.sql).
-- Docks out of use = docks - bikes - free docks: derived, not reported by TfL.

SELECT
  london_date AS day,
  station_id,
  station_name,
  lat,
  lon,
  docks,
  ROUND(observed_minutes, 1) AS observed_minutes,
  ROUND(empty_minutes, 1) AS empty_minutes,
  ROUND(full_minutes, 1) AS full_minutes,
  ROUND(peak_observed_minutes, 1) AS peak_observed_minutes,
  ROUND(peak_empty_minutes, 1) AS peak_empty_minutes,
  ROUND(peak_full_minutes, 1) AS peak_full_minutes,
  ROUND(avg_bikes, 1) AS avg_bikes,
  ROUND(avg_e_bikes, 1) AS avg_e_bikes,
  ROUND(avg_out_of_use_docks, 1) AS avg_out_of_use_docks
FROM `transport-for-london-511318.tfl_transport_analytics.bikepoint_station_daily`
WHERE london_date < CURRENT_DATE('Europe/London')
  AND london_date >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 30 DAY)
ORDER BY day, station_name
