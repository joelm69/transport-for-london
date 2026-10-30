-- Figures behind the bikes business recommendation on the portfolio, over the last 7 complete
-- London days (bike data starts 29 October 2026, 17:10), so the findings and the stations named
-- follow the data. One row. Shares are time-weighted as on the Tableau bikes dashboard:
-- SUM(minutes empty) / SUM(minutes observed). Built on analytics/bikepoint/02_views.sql.
--
-- Repeat-failure stations: empty or full at least half of the time observed over the period.
-- Out-of-use docks: each station's time-weighted average, summed over the network (derived:
-- docks - bikes - free docks, so it includes broken bikes left in docks).

CREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.bike_recommendation` AS
WITH window_days AS (
  SELECT DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 7 DAY) AS first_day,
         DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 1 DAY) AS last_day
),
days AS (
  SELECT d.*
  FROM `transport-for-london-511318.tfl_transport_analytics.bikepoint_station_daily` AS d, window_days AS w
  WHERE d.london_date BETWEEN w.first_day AND w.last_day
),
network AS (
  SELECT
    COUNT(DISTINCT london_date) AS days,
    MIN(london_date) AS data_from,
    SAFE_DIVIDE(SUM(empty_minutes), SUM(observed_minutes)) AS empty_share,
    SAFE_DIVIDE(SUM(full_minutes), SUM(observed_minutes)) AS full_share,
    SAFE_DIVIDE(SUM(peak_empty_minutes), SUM(peak_observed_minutes)) AS peak_empty_share,
    SAFE_DIVIDE(SUM(peak_full_minutes), SUM(peak_observed_minutes)) AS peak_full_share
  FROM days
),
stations AS (
  SELECT
    station_id,
    ARRAY_AGG(station_name ORDER BY london_date DESC LIMIT 1)[OFFSET(0)] AS station_name,
    ARRAY_AGG(docks ORDER BY london_date DESC LIMIT 1)[OFFSET(0)] AS docks,
    SAFE_DIVIDE(SUM(empty_minutes), SUM(observed_minutes)) AS empty_share,
    SAFE_DIVIDE(SUM(full_minutes), SUM(observed_minutes)) AS full_share,
    SUM(empty_minutes + full_minutes) AS problem_minutes,
    SAFE_DIVIDE(SUM(avg_out_of_use_docks * observed_minutes), SUM(observed_minutes)) AS out_of_use_docks
  FROM days
  GROUP BY station_id
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY problem_minutes DESC) AS rank, COUNT(*) OVER () AS total
  FROM stations
),
station_figures AS (
  SELECT
    COUNT(*) AS stations,
    SUM(docks) AS docks,
    COUNTIF(empty_share + full_share >= 0.5) AS repeat_stations,
    CAST(ROUND(COUNT(*) * 0.1) AS INT64) AS top_tenth_stations,
    SAFE_DIVIDE(SUM(IF(rank <= ROUND(total * 0.1), problem_minutes, 0)), SUM(problem_minutes)) AS top_tenth_share,
    SUM(out_of_use_docks) AS out_of_use_docks,
    COUNTIF(SAFE_DIVIDE(out_of_use_docks, docks) >= 0.25) AS quarter_out_stations
  FROM ranked
),
most_empty AS (
  SELECT ARRAY_AGG(STRUCT(station_name, empty_share AS share) ORDER BY empty_share DESC LIMIT 3) AS most_empty
  FROM stations
  WHERE empty_share >= 0.5
),
most_full AS (
  SELECT ARRAY_AGG(STRUCT(station_name, full_share AS share) ORDER BY full_share DESC LIMIT 3) AS most_full
  FROM stations
  WHERE full_share >= 0.5
),
most_out_of_use AS (
  SELECT ARRAY_AGG(STRUCT(station_name, out_of_use_docks, docks) ORDER BY out_of_use_docks DESC LIMIT 3)
    AS most_out_of_use
  FROM stations
  WHERE SAFE_DIVIDE(out_of_use_docks, docks) >= 0.25
),
hours AS (
  SELECT
    EXTRACT(HOUR FROM DATETIME(h.hour_start, 'Europe/London')) AS hour,
    EXTRACT(DAYOFWEEK FROM DATETIME(h.hour_start, 'Europe/London')) NOT IN (1, 7) AS weekday,
    h.empty_minutes,
    h.observed_minutes
  FROM `transport-for-london-511318.tfl_transport_analytics.bikepoint_station_hourly` AS h, window_days AS w
  WHERE DATE(h.hour_start, 'Europe/London') BETWEEN w.first_day AND w.last_day
),
worst_hour AS (
  SELECT hour AS worst_hour, empty_share AS worst_hour_empty_share
  FROM (SELECT hour, SAFE_DIVIDE(SUM(empty_minutes), SUM(observed_minutes)) AS empty_share FROM hours GROUP BY hour)
  ORDER BY empty_share DESC
  LIMIT 1
),
eight_am AS (
  SELECT SAFE_DIVIDE(SUM(empty_minutes), SUM(observed_minutes)) AS weekday_8am_empty_share
  FROM hours
  WHERE weekday AND hour = 8
)
SELECT
  w.first_day,
  w.last_day,
  n.data_from,
  n.days,
  n.empty_share,
  n.full_share,
  n.peak_empty_share,
  n.peak_full_share,
  wh.worst_hour,
  wh.worst_hour_empty_share,
  e.weekday_8am_empty_share,
  s.stations,
  s.docks,
  s.repeat_stations,
  s.top_tenth_stations,
  s.top_tenth_share,
  s.out_of_use_docks,
  SAFE_DIVIDE(s.out_of_use_docks, s.docks) AS out_of_use_share,
  s.quarter_out_stations,
  me.most_empty,
  mf.most_full,
  mo.most_out_of_use,
  CURRENT_TIMESTAMP() AS computed_at
FROM window_days AS w, network AS n, station_figures AS s, worst_hour AS wh, eight_am AS e,
  most_empty AS me, most_full AS mf, most_out_of_use AS mo;
