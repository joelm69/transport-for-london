-- History for the portfolio's "Ask the data" chat: bike stations, main roads, road incidents and
-- all bus routes, copied into the serving dataset (tfl_portfolio, the only one the portfolio's
-- service account can read). The last 35 London days; the chat analyses complete days only, so
-- each table is rebuilt once a day, when a new day has completed. Run hourly inside the
-- scheduled query "tfl-portfolio-refresh" (the first run creates the tables; see the bottom).

-- Santander Cycles: one row per docking station and hour (analytics/bikepoint/02_views.sql).
IF IFNULL((SELECT MAX(DATE(hour_start, 'Europe/London')) FROM `transport-for-london-511318.tfl_portfolio.bike_station_hourly`),
          DATE '2000-01-01') < DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 1 DAY) THEN
  CREATE OR REPLACE TABLE `transport-for-london-511318.tfl_portfolio.bike_station_hourly`
  PARTITION BY DATE(hour_start) CLUSTER BY station_id
  OPTIONS (description = "Bike docking stations by hour, last 35 London days, rebuilt daily")
  AS
  SELECT station_id, station_name, hour_start, docks, observed_minutes, empty_minutes, full_minutes,
         avg_bikes, avg_e_bikes, avg_out_of_use_docks
  FROM `transport-for-london-511318.tfl_transport_analytics.bikepoint_station_hourly`
  WHERE DATE(hour_start, 'Europe/London') >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 35 DAY);
END IF;

-- Main roads: one row per TfL corridor and hour, minutes at each status (analytics/road/02_views.sql).
IF IFNULL((SELECT MAX(DATE(hour_start, 'Europe/London')) FROM `transport-for-london-511318.tfl_portfolio.road_corridor_hourly`),
          DATE '2000-01-01') < DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 1 DAY) THEN
  CREATE OR REPLACE TABLE `transport-for-london-511318.tfl_portfolio.road_corridor_hourly`
  OPTIONS (description = "Main road corridors by hour, last 35 London days, rebuilt daily")
  AS
  SELECT corridor_id, corridor_name, hour_start, observed_minutes, good_minutes, serious_minutes, severe_minutes,
         closure_minutes, delayed_minutes
  FROM `transport-for-london-511318.tfl_transport_analytics.road_corridor_hourly`
  WHERE DATE(hour_start, 'Europe/London') >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 35 DAY);

  -- Road incidents first seen in each hour the collector was running, so an hour with none is a
  -- real zero, not a missing hour.
  CREATE OR REPLACE TABLE `transport-for-london-511318.tfl_portfolio.road_incident_hourly`
  OPTIONS (description = "Road incidents first seen per hour of collection, last 35 London days, rebuilt daily")
  AS
  WITH hours AS (
    SELECT DISTINCT hour_start
    FROM `transport-for-london-511318.tfl_transport_analytics.road_corridor_hourly`
    WHERE DATE(hour_start, 'Europe/London') >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 35 DAY)
  ),
  incidents AS (
    SELECT TIMESTAMP_TRUNC(first_seen, HOUR) AS hour_start, COUNT(*) AS incidents,
           COUNTIF(worst_severity_rank >= 3) AS serious_incidents,
           APPROX_QUANTILES(IF(NOT still_open, minutes_to_clear, NULL), 2)[OFFSET(1)] AS median_minutes_to_clear
    FROM `transport-for-london-511318.tfl_transport_analytics.road_disruptions`
    WHERE is_incident
    GROUP BY hour_start
  )
  SELECT h.hour_start, IFNULL(i.incidents, 0) AS incidents, IFNULL(i.serious_incidents, 0) AS serious_incidents,
         i.median_minutes_to_clear
  FROM hours AS h LEFT JOIN incidents AS i USING (hour_start);

  -- Incidents first seen per hour by type (collisions, breakdowns...), zero when none, for the
  -- chat's questions about accidents by hour, day and trend.
  CREATE OR REPLACE TABLE `transport-for-london-511318.tfl_portfolio.road_incident_type_hourly`
  OPTIONS (description = "Road incidents first seen per hour of collection by type, last 35 London days, rebuilt daily")
  AS
  WITH hours AS (
    SELECT DISTINCT hour_start
    FROM `transport-for-london-511318.tfl_transport_analytics.road_corridor_hourly`
    WHERE DATE(hour_start, 'Europe/London') >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 35 DAY)
  ),
  types AS (
    SELECT TIMESTAMP_TRUNC(first_seen, HOUR) AS hour_start,
           COUNTIF(is_incident AND category = 'Collisions') AS collisions,
           COUNTIF(is_incident AND category = 'Breakdowns') AS breakdowns,
           COUNTIF(is_incident AND category = 'Asset issues') AS asset_faults,
           COUNTIF(is_incident AND category = 'Planned events') AS events,
           COUNTIF(NOT is_incident) AS works_started
    FROM `transport-for-london-511318.tfl_transport_analytics.road_disruptions`
    GROUP BY hour_start
  )
  SELECT h.hour_start, IFNULL(t.collisions, 0) AS collisions, IFNULL(t.breakdowns, 0) AS breakdowns,
         IFNULL(t.asset_faults, 0) AS asset_faults, IFNULL(t.events, 0) AS events,
         IFNULL(t.works_started, 0) AS works_started
  FROM hours AS h LEFT JOIN types AS t USING (hour_start);

  -- One row per road disruption (incidents and works), for types, areas, daily counts and clear
  -- times. hour_start is when it was first seen.
  CREATE OR REPLACE TABLE `transport-for-london-511318.tfl_portfolio.road_disruption_events`
  OPTIONS (description = "Road disruptions first seen in the last 35 London days, one row each, rebuilt daily")
  AS
  SELECT disruption_id, category, sub_category, is_incident, worst_severity, worst_severity_rank, borough,
         location, closes_street, first_seen AS hour_start, still_open,
         IF(minutes_to_clear > 0, minutes_to_clear, NULL) AS minutes_to_clear
  FROM `transport-for-london-511318.tfl_transport_analytics.road_disruptions`
  WHERE DATE(first_seen, 'Europe/London') >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 35 DAY);
END IF;

-- All bus routes: one row per route, direction, day and time band (analytics/bus_network). The
-- bands have no single hour, so hour_start is the band's start (London time), which lets the chat
-- group by day, weekday and band.
IF IFNULL((SELECT MAX(day) FROM `transport-for-london-511318.tfl_portfolio.bus_route_bands`), DATE '2000-01-01')
   < DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 1 DAY) THEN
  CREATE OR REPLACE TABLE `transport-for-london-511318.tfl_portfolio.bus_route_bands`
  OPTIONS (description = "All bus routes by day and time band, last 35 London days, rebuilt daily")
  AS
  SELECT day, route, direction, time_band,
         TIMESTAMP(DATETIME(day, TIME(CAST(REGEXP_EXTRACT(time_band, r'\((\d\d)-') AS INT64), 0, 0)),
                   'Europe/London') AS hour_start,
         polls, buses, typical_gap_minutes, bunching_rate, longest_gap_minutes, average_wait_minutes,
         median_wait_minutes
  FROM `transport-for-london-511318.tfl_transport_analytics.bus_routes_daily`
  WHERE day >= DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 35 DAY);
END IF;
