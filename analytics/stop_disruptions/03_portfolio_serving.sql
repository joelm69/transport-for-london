-- Serving tables for the public portfolio (tfl_portfolio dataset, read-only to the portfolio).
-- Refreshed hourly together with analytics/line_status/03_portfolio_serving.sql by the BigQuery
-- scheduled query "tfl-portfolio-refresh: line status and stop disruptions".

CREATE OR REPLACE TABLE `transport-for-london-511318.tfl_portfolio.station_accessibility_hourly`
OPTIONS (description = "Minutes per station and hour with an accessibility notice, with issue categories")
AS
SELECT station, modes, hour_start, accessibility_minutes, issues
FROM `transport-for-london-511318.tfl_transport_analytics.station_accessibility_hourly`;

CREATE OR REPLACE TABLE `transport-for-london-511318.tfl_portfolio.stop_disruption_observed_hourly`
OPTIONS (description = "Minutes per hour the station notices collector was observing")
AS
SELECT hour_start, observed_minutes
FROM `transport-for-london-511318.tfl_transport_analytics.stop_disruption_observed_hourly`;
