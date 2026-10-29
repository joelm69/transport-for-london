-- Google Sheet tab: road_corridor_hours
-- One row per main road corridor (24) per hour of each completed London day: minutes observed
-- and minutes at each TfL status. Feeds "which main roads spend most time at Serious, and
-- when?". Shares are left to Tableau (SUM(delayed) / SUM(observed)) so they stay correct when
-- hours or days are grouped. The last 30 days are kept in the Sheet (BigQuery keeps everything);
-- collection began 29 October 2026, 18:38 UTC.
--
-- Source: tfl_transport_analytics.road_corridor_hourly (analytics/road/02_views.sql).

SELECT
  DATE(hour_start, 'Europe/London') AS day,
  EXTRACT(HOUR FROM DATETIME(hour_start, 'Europe/London')) AS hour,
  IF(EXTRACT(DAYOFWEEK FROM DATETIME(hour_start, 'Europe/London')) IN (1, 7), 'Weekend', 'Weekday') AS day_type,
  corridor_id,
  corridor_name,
  ROUND(observed_minutes, 1) AS observed_minutes,
  ROUND(delayed_minutes, 1) AS delayed_minutes,
  ROUND(serious_minutes, 1) AS serious_minutes,
  ROUND(severe_minutes, 1) AS severe_minutes,
  ROUND(closure_minutes, 1) AS closure_minutes
FROM `transport-for-london-511318.tfl_transport_analytics.road_corridor_hourly`
WHERE DATE(hour_start, 'Europe/London') < CURRENT_DATE('Europe/London')
  AND DATE(hour_start, 'Europe/London') >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 30 DAY)
ORDER BY day, hour, corridor_name
