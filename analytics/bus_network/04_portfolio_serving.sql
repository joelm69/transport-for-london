-- The bus business recommendation's figures (last 7 complete London days), read by the portfolio
-- to write its findings. Definitions in analytics/bus_network/03_recommendation.sql. Run hourly
-- with the other serving tables, but rebuilt only when a new day has completed: the views read
-- ~270 MB, and the figures only change once a day.
IF (SELECT MAX(last_day) FROM `transport-for-london-511318.tfl_portfolio.bus_recommendation`)
   < DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 1 DAY) THEN
  CREATE OR REPLACE TABLE `transport-for-london-511318.tfl_portfolio.bus_recommendation`
  OPTIONS (description = "Bus recommendation figures over the last 7 complete London days, rebuilt daily")
  AS
  SELECT * FROM `transport-for-london-511318.tfl_transport_analytics.bus_recommendation`;
END IF;
