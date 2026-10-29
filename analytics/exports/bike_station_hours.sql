-- Google Sheet tab: bike_station_hours
-- One row per docking station, day type (weekday / weekend) and London hour of day, over the
-- last 28 completed days: how many minutes the station was observed, empty and full in that
-- hour. Feeds "when does each station fail riders?" (empty in the morning in the suburbs, full
-- in the City). Shares are left to Tableau (SUM(empty) / SUM(observed)) so they stay correct
-- when stations or hours are grouped. Join to bike_stations_daily on station_id for location.
--
-- Source: tfl_transport_analytics.bikepoint_station_hourly (analytics/bikepoint/02_views.sql).

SELECT
  IF(EXTRACT(DAYOFWEEK FROM DATETIME(hour_start, 'Europe/London')) IN (1, 7), 'Weekend', 'Weekday') AS day_type,
  EXTRACT(HOUR FROM DATETIME(hour_start, 'Europe/London')) AS hour,
  station_id,
  ANY_VALUE(station_name) AS station_name,
  COUNT(DISTINCT DATE(hour_start, 'Europe/London')) AS days,
  ROUND(SUM(observed_minutes), 1) AS observed_minutes,
  ROUND(SUM(empty_minutes), 1) AS empty_minutes,
  ROUND(SUM(full_minutes), 1) AS full_minutes
FROM `transport-for-london-511318.tfl_transport_analytics.bikepoint_station_hourly`
WHERE DATE(hour_start, 'Europe/London') < CURRENT_DATE('Europe/London')
  AND DATE(hour_start, 'Europe/London') >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 28 DAY)
GROUP BY day_type, hour, station_id
ORDER BY day_type, hour, station_name
