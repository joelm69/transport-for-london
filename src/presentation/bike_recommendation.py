"""Write the bikes business recommendation's summary and findings from the latest figures
(one row of tfl_portfolio.bike_recommendation: the last 7 complete London days).

Pure functions, shared by the portfolio and future MCP tools. Numbers come from the row; where
a headline is a conclusion ("Peaks are covered; nights are not"), a simple rule picks wording the
figures support. The recommendations keep the analyst's wording; only the stations they name
come from the data, so they always point at the stations in the findings.
"""

from datetime import date

PEAK_HOURS = {7, 8, 9, 16, 17, 18}      # weekdays, London time, as on the dashboard
NIGHT_HOURS = {21, 22, 23, 0, 1, 2, 3, 4, 5}


def _pct(value, decimals=0):
    return "–" if value is None else f"{value * 100:.{decimals}f}%"


def _one_in(share):
    return f"1 in {round(1 / share)}"


def _station(name, figure=None):
    """'Mudchute DLR, Cubitt Town' -> 'Mudchute DLR (Cubitt Town)', or with a figure
    'Mudchute DLR (Cubitt Town, 89%)', so lists read clearly when names contain commas."""
    place, _, area = name.partition(", ")
    inside = ", ".join(part for part in (area.strip(), figure) if part)
    return f"{place.strip()} ({inside})" if inside else place.strip()


def _join(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def _shares(items):
    return _join([_station(i["station_name"], _pct(i["share"])) for i in items])


def summary(row):
    """The summary's figures (the recommendation sentence after it stays fixed)."""
    peaks = ""
    if row["peak_empty_share"] is not None:
        peaks = (f"In the weekday peaks about {_pct(row['peak_empty_share'])} of stations were empty and "
                 f"{_pct(row['peak_full_share'])} full. ")
    return (
        f"{peaks}{row['repeat_stations']} stations were empty or full at least half the time, and "
        f"about {_one_in(row['out_of_use_share'])} docks ({_pct(row['out_of_use_share'])}) couldn't be used."
    )


def findings(row):
    """Three (headline, text) findings."""
    peak, worst, hour = row["peak_empty_share"], row["worst_hour_empty_share"], row["worst_hour"]
    if peak is not None and hour not in PEAK_HOURS and worst >= 2 * peak:
        headline = ("Peaks are covered; nights are not" if hour in NIGHT_HOURS
                    else "The worst hours are outside the peaks")
    else:
        headline = "Stations run empty in the busy hours"
    text = (f"about {_pct(peak)} of stations were empty in the weekday peaks. " if peak is not None else "")
    text += (f"The worst hour was {hour:02d}:00, when {_pct(worst)} (about {_one_in(worst)}) were empty")
    eight = row.get("weekday_8am_empty_share")
    text += (f"; by 08:00 on weekdays it was back to {_pct(eight)}." if eight is not None else ".")
    first = (headline, text[0].upper() + text[1:])

    over = f" over the {row['days']} days" if row["days"] > 1 else ""
    text = (f"{row['repeat_stations']} stations were empty or full at least half the time{over}. The worst "
            f"10% ({row['top_tenth_stations']} stations) account for {_pct(row['top_tenth_share'])} of all "
            "empty or full time.")
    if row.get("most_empty"):
        text += f" Most often empty: {_shares(row['most_empty'])}."
    if row.get("most_full"):
        text += f" Most often full: {_shares(row['most_full'])}."
    second = ("A small group of stations fails again and again" if row["top_tenth_share"] >= 0.25
              else "Failures are spread across the network", text)

    text = (f"About {row['out_of_use_docks']:,.0f} docks were out of use on average: "
            f"{_pct(row['out_of_use_share'], 1)} of the network's {row['docks']:,}.")
    worst_docks = row.get("most_out_of_use") or []
    if worst_docks:
        examples = _join([_station(s["station_name"], f"{s['out_of_use_docks']:.0f} of {s['docks']}")
                          for s in worst_docks])
        text += (f" At {row['quarter_out_stations']} stations a quarter or more of the docks were out of "
                 f"use, including {examples}.")
    third = (f"{_one_in(row['out_of_use_share']).capitalize()} docks can't be used", text)
    return [first, second, third]


def recommendations(row):
    """(action, detail) bullets in the analyst's wording, naming this week's stations."""
    bullets = []
    empty = [_station(s["station_name"]) for s in row.get("most_empty") or []]
    full = [_station(s["station_name"]) for s in row.get("most_full") or []]
    if empty or full:
        parts = ([f"refill {_join(empty)}"] if empty else []) + ([f"clear bikes from {_join(full)}"] if full else [])
        bullets.append(("Target rebalancing at the repeat-failure stations:", "; ".join(parts) + "."))
    if row.get("most_out_of_use"):
        names = _join([_station(s["station_name"]) for s in row["most_out_of_use"]])
        bullets.append((f"Prioritise repairs at the {row['quarter_out_stations']} stations with a quarter or more "
                        "docks out of use,", f"starting with {names}."))
    bullets.append(("Don't add bikes or stations network-wide yet:",
                    "the peaks are mostly covered, and the problems are local."))
    return bullets


def period(row):
    """'29–30 October 2026': from the first day with data, as bike collection began on 29 October."""
    first, last = (date.fromisoformat(v) if isinstance(v, str) else v for v in (row["data_from"], row["last_day"]))
    if first == last:
        return f"{last.day} {last:%B %Y}"
    start = f"{first.day}" if first.month == last.month else f"{first.day} {first:%B}"
    return f"{start}–{last.day} {last:%B %Y}"
