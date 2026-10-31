-- Figures behind the roads business recommendation on the portfolio, over the last 7 complete
-- London days (road data starts 29 October 2026, 18:38 UTC), so the findings and the roads named
-- follow the data. One row. Calculated as on the Tableau roads dashboard: main-road shares are
-- time-weighted (SUM(minutes delayed) / SUM(minutes observed)); incidents are those first seen in
-- the period; medians interpolate like Tableau's MEDIAN. Built on analytics/road/02_views.sql.
--
-- Mostly-delayed roads: TfL rated them Serious or worse at least half of the time observed.
-- Roadworks on a road: works on the road at any time in the period (many started long before it).
-- Works that ended: cleared in the period; overrun = still listed after TfL's planned end (often
-- by minutes: TfL takes a listing down a little after the planned end).

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.road_recommendation` AS
WITH window_days AS (
  SELECT DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 7 DAY) AS first_day,
         DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 1 DAY) AS last_day
),
corridor_hours AS (
  SELECT c.*
  FROM `transport-for-london-511318.tfl_transport_analytics.road_corridor_hourly` AS c, window_days AS w
  WHERE DATE(c.hour_start, 'Europe/London') BETWEEN w.first_day AND w.last_day
),
disruptions AS (
  SELECT d.*, DATE(d.first_seen, 'Europe/London') AS seen_day,
         DATE(d.cleared_at, 'Europe/London') AS cleared_day
  FROM `transport-for-london-511318.tfl_transport_analytics.road_disruptions` AS d, window_days AS w
  WHERE DATE(d.first_seen, 'Europe/London') <= w.last_day
    AND COALESCE(DATE(d.cleared_at, 'Europe/London'), w.last_day) >= w.first_day
),
incidents AS (
  SELECT d.* FROM disruptions AS d, window_days AS w WHERE d.is_incident AND d.seen_day >= w.first_day
),
works AS (
  SELECT * FROM disruptions WHERE NOT is_incident
),
roads AS (
  SELECT
    corridor_id,
    ANY_VALUE(corridor_name) AS corridor_name,
    SAFE_DIVIDE(SUM(delayed_minutes), SUM(observed_minutes)) AS delayed_share
  FROM corridor_hours
  GROUP BY corridor_id
),
road_counts AS (
  SELECT
    r.*,
    (SELECT COUNT(*) FROM incidents AS i, UNNEST(i.corridor_ids) AS c WHERE LOWER(c) = r.corridor_id) AS incidents,
    (SELECT COUNT(*) FROM works AS k, UNNEST(k.corridor_ids) AS c WHERE LOWER(c) = r.corridor_id) AS works
  FROM roads AS r
),
road_figures AS (
  SELECT
    COUNT(*) AS roads,
    COUNTIF(delayed_share > 0) AS roads_delayed,
    COUNTIF(delayed_share >= 0.5) AS roads_mostly_delayed,
    COUNTIF(delayed_share >= 0.5 AND works > 0) AS mostly_delayed_with_works,
    COUNTIF(delayed_share >= 0.5 AND incidents = 0 AND works > 0) AS mostly_delayed_works_only,
    ARRAY_AGG(IF(delayed_share >= 0.5, STRUCT(corridor_name, delayed_share, incidents, works), NULL)
              IGNORE NULLS ORDER BY delayed_share DESC, works DESC LIMIT 8) AS mostly_delayed,
    ARRAY_AGG(STRUCT(corridor_name, works, delayed_share) ORDER BY works DESC LIMIT 3) AS most_works
  FROM road_counts
),
network AS (
  SELECT
    COUNT(DISTINCT DATE(hour_start, 'Europe/London')) AS days,
    MIN(DATE(hour_start, 'Europe/London')) AS data_from,
    SAFE_DIVIDE(SUM(delayed_minutes), SUM(observed_minutes)) AS delayed_share
  FROM corridor_hours
),
cleared AS (
  SELECT sub_category, minutes_to_clear,
         PERCENTILE_CONT(minutes_to_clear, 0.5) OVER () AS median_all,
         PERCENTILE_CONT(minutes_to_clear, 0.5) OVER (PARTITION BY sub_category) AS median_type
  FROM incidents
  WHERE NOT still_open AND minutes_to_clear IS NOT NULL
),
incident_figures AS (
  SELECT
    (SELECT COUNT(*) FROM incidents) AS incidents,
    (SELECT COUNTIF(ARRAY_LENGTH(corridor_ids) > 0) FROM incidents) AS incidents_on_main_roads,
    COUNT(*) AS incidents_cleared,
    ANY_VALUE(median_all) AS median_clear_minutes
  FROM cleared
),
types AS (
  SELECT sub_category, COUNT(*) AS cleared, ANY_VALUE(median_type) AS median_minutes
  FROM cleared
  GROUP BY sub_category
  HAVING COUNT(*) >= 2   -- a type needs at least two cleared incidents to have a typical time
),
type_figures AS (
  SELECT ARRAY_AGG(STRUCT(sub_category, cleared, median_minutes) ORDER BY median_minutes) AS types
  FROM types
),
ended_works AS (
  SELECT overrun_minutes,
         PERCENTILE_CONT(IF(overrun_minutes > 0, overrun_minutes, NULL), 0.5) OVER () AS median_overrun
  FROM works AS k, window_days AS w
  WHERE NOT k.still_open AND k.cleared_day BETWEEN w.first_day AND w.last_day
),
works_figures AS (
  SELECT
    COUNT(*) AS works_ended,
    COUNTIF(overrun_minutes > 0) AS works_overran,
    COUNTIF(overrun_minutes >= 24 * 60) AS works_overran_day,
    SAFE_DIVIDE(COUNTIF(overrun_minutes > 0), COUNT(*)) AS works_overrun_share,
    ANY_VALUE(median_overrun) AS median_overrun_minutes
  FROM ended_works
)
SELECT
  w.first_day,
  w.last_day,
  n.data_from,
  n.days,
  n.delayed_share,
  r.roads,
  r.roads_delayed,
  r.roads_mostly_delayed,
  r.mostly_delayed_with_works,
  r.mostly_delayed_works_only,
  r.mostly_delayed,
  r.most_works,
  i.incidents,
  i.incidents_on_main_roads,
  i.incidents_cleared,
  i.median_clear_minutes,
  t.types,
  k.works_ended,
  k.works_overran,
  k.works_overrun_share,
  k.works_overran_day,
  k.median_overrun_minutes,
  CURRENT_TIMESTAMP() AS computed_at
FROM window_days AS w, network AS n, road_figures AS r, incident_figures AS i, type_figures AS t,
  works_figures AS k;
