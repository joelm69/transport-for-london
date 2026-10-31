# Glossary: how the platform's figures are defined

The AI agent searches this file (with the endpoint cards in docs/endpoints/ and the comments in
analytics/) before explaining a number, so it explains figures the way the dashboards do. Each
section is one searchable chunk: keep sections short and self-contained.

## Data collection and coverage

The platform polls the TfL Unified API from a VM and Cloud Run jobs and keeps only what changed
(change-only storage). Rail line status is collected every 30 seconds since 21 October 2026,
station notices every minute, buses (all routes) every 30 minutes since 26 October 2026, bike
docking stations every 2 minutes since 29 October 2026 17:10, and road disruptions and main-road
status every 5 minutes since 29 October 2026 18:38 (UTC). History is in BigQuery; the Tableau
dashboards read a Google Sheet exported nightly at 02:00 London time. Business recommendations
use the last 7 complete London days.

## Rail: disrupted share and good service

Disrupted share is the share of a line's in-service minutes that had any status other than Good
Service (planned works included). Good service share is the opposite. Overnight closures are not
counted as disruption. Waterloo & City's normal weekend closure does count. Planned works are
identified from TfL's wording, so some may be misclassified. No passenger numbers are available,
so the impact on journeys is not measured.

## Rail: incidents and causes

An incident is one disruption on a line, identified by TfL's reason; it is counted once per day
even when it spans several hours. Each disruption's cause is read from TfL's reason text: for
example Signal failure, Points failure, Power failure, Train fault, Track fault, Staff shortage,
Customer incident (a person taken ill or on the track), Trespasser, Fire alert, Security alert,
Weather, Industrial action, or Planned works or changes. The main cause of a line is the one with
the most disrupted minutes. Train causes are train faults and staff shortages; infrastructure
causes are signal, points, power and track failures.

## Tube and rail arrivals: headways, waits and bunching

From TfL's live arrival predictions the platform records when each train actually arrives at a
stop. The headway is the time between consecutive train arrivals at the same stop, platform and
line. Average passenger wait is Σh² / (2 × Σh) over the headways h. A train headway is bunched
when it is under half the period's median headway (trains arriving in clumps). Regularity is the
coefficient of variation of headways: 0 is perfectly even. Prediction error is the arrival time
minus TfL's countdown at 10, 5 and 2 minutes before.

## Buses: bunching

Every 30 minutes the platform reads the predicted gaps between successive buses at each stop of
every route (buses due within 30 minutes). A gap is bunched when it is under half that poll's
median gap; the bunching rate is the share of gaps that were bunched. Typical gap is the
gap-weighted average of each poll's median gap. Route and network figures are weighted by the
number of polls, as on the Tableau bus dashboard: SUM(x × polls) / SUM(polls). Gaps over 30
minutes are not captured, so waits on infrequent routes are understated.

## Buses: waiting time and extra wait

Average wait is the exact wait for a passenger arriving at random: sum(gap²) / (2 × sum(gap)). If
buses came evenly the wait would be half the typical gap. Extra wait from uneven spacing = average
wait − half the typical gap. Passenger-hours lost = extra wait × about 5 million daily journeys
(TfL: 1.84 billion bus journeys in 2024/25); an upper estimate, since passengers on infrequent
routes time their arrival. Without timetable data the platform cannot compute Excess Wait Time,
TfL's official measure.

## Buses: speed

Route speed is distance travelled along the route divided by time, door to door, so it includes
stops, signals and time at the end of the route. A move between two polls is kept only when it is
forward and no faster than 25 km/h on average.
Very short routes are left out of the slowest-route ranking.

## Bikes: empty, full and out of use

A docking station is empty when it has no bike to take (0 bikes) and full when there is nowhere
to return a bike (0 free docks). Docks out of use = docks − bikes − free docks: derived, not
reported by TfL, so it includes broken bikes left in docks as well as broken docks. Shares are
time-weighted: minutes empty ÷ minutes observed.

## Bikes: problem share, peaks, headroom

Problem share is the share of time a station was empty or full. Weekday peaks are 07:00–10:00 and
16:00–19:00 London time, Monday to Friday. Headroom is the smaller of a station's average bikes
and average free docks: its spare capacity in its weaker direction. The best stations are those
with the lowest problem share, ties broken by the most headroom. Repeat-failure stations were
empty or full at least half the time. There is no trip data, so figures are shares of station
time, not riders turned away.

## Roads: main-road status

TfL publishes one status for each of its 24 main roads (corridors): Good (no exceptional delays),
Serious, Severe or Closure. It is TfL's own rating for the road as a whole, often driven by
long-running roadworks, not a measured traffic speed. Statuses change rarely: a road can be rated
Serious for days. Delayed share is the share of observed time at Serious or worse.

## Roads: incidents and time to clear

Incidents are live disruptions such as collisions, breakdowns, traffic-signal faults, emergencies
and events; roadworks are counted separately. Time to clear runs from when an incident first
appeared to the first 5-minute check it had gone. The median is used, not the average, because a
few incidents (mostly traffic-signal faults) last more than a day and would distort an average.
About half of incidents are on local roads with no main road named.

## Roads: roadworks overrunning

A roadwork overran when it was still listed after the end date TfL had planned. Overruns are
measured at 5-minute checks, so an overrun of a few minutes may just be TfL updating its list
late. Works' reported start dates often go back years, so works are judged by overrun, not by
duration.
