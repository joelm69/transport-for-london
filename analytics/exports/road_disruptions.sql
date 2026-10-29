-- Google Sheet tab: road_disruptions
-- One row per road disruption (live incident or roadworks) seen in the last 30 completed London
-- days: type, worst severity, borough, main roads, location, when TfL says it started and was
-- planned to end, when it cleared, minutes to clear and overrun. Feeds "how long do incidents
-- take to clear?", "do roadworks finish on time?" and the map of where disruptions cluster.
-- Still-open disruptions are included with still_open = TRUE (their times are censored).
--
-- Source: tfl_transport_analytics.road_disruptions (analytics/road/02_views.sql). The clear time
-- is the first 5-minute poll that no longer listed the disruption.

SELECT
  disruption_id,
  IF(is_incident, 'Incident', 'Works') AS kind,
  category,
  sub_category,
  worst_severity,
  borough,
  ARRAY_TO_STRING(corridor_ids, ', ') AS main_roads,
  location,
  lat,
  lon,
  closes_street,
  DATETIME(reported_start, 'Europe/London') AS reported_start,
  DATETIME(planned_end, 'Europe/London') AS planned_end,
  DATETIME(first_seen, 'Europe/London') AS first_seen,
  DATETIME(cleared_at, 'Europe/London') AS cleared_at,
  still_open,
  ROUND(minutes_to_clear, 1) AS minutes_to_clear,
  ROUND(overrun_minutes, 1) AS overrun_minutes,
  updates
FROM `transport-for-london-511318.tfl_transport_analytics.road_disruptions`
WHERE DATE(first_seen, 'Europe/London') < CURRENT_DATE('Europe/London')
  AND COALESCE(DATE(cleared_at, 'Europe/London'), CURRENT_DATE('Europe/London'))
      >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 30 DAY)
ORDER BY first_seen, disruption_id
