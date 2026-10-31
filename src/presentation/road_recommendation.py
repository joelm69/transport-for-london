"""Write the roads business recommendation's summary and findings from the latest figures
(one row of tfl_portfolio.road_recommendation: the last 7 complete London days).

Pure functions, shared by the portfolio and future MCP tools. Numbers come from the row; where
a headline is a conclusion ("Roadworks end late, but mostly by minutes"), a simple rule picks
wording the figures support. The recommendations keep the analyst's wording; only the roads and
incident types they name come from the data.
"""

from datetime import date


def _pct(value, decimals=0):
    return "–" if value is None else f"{value * 100:.{decimals}f}%"


def _duration(minutes):
    """171.2 -> '2h 51m'; 39.8 -> '40m'."""
    total = round(minutes)
    hours, mins = divmod(total, 60)
    return f"{hours}h {mins:02d}m" if hours else f"{mins}m"


def _join(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def _plural(sub_category):
    """'Traffic signal fault' -> 'traffic signal faults'."""
    return sub_category.lower() + "s"


def _types(row):
    """Incident types with a typical clear time, fastest first."""
    return list(row.get("types") or [])


def _roads_to_review(row):
    """The delayed main roads with the most roadworks on them."""
    roads = sorted(row.get("mostly_delayed") or [], key=lambda r: -r["works"])
    return [r["corridor_name"] for r in roads if r["works"] > 0][:3]


def summary(row):
    """The summary's figures (the recommendation sentence after it stays fixed)."""
    text = (f"TfL rated {row['roads_mostly_delayed']} of its {row['roads']} main roads as seriously delayed "
            "at least half the time")
    if row["roads_mostly_delayed"] and row["mostly_delayed_with_works"] == row["roads_mostly_delayed"]:
        text += ", and every one of them had roadworks on it"
    text += "."
    if row["median_clear_minutes"] is not None:
        text += f" A typical incident was cleared in {_duration(row['median_clear_minutes'])}"
        types = _types(row)
        if len(types) > 1:
            text += f", but {_plural(types[-1]['sub_category'])} took {_duration(types[-1]['median_minutes'])}"
        text += "."
    return text


def findings(row):
    """Three (headline, text) findings."""
    mostly, with_works = row["roads_mostly_delayed"], row["mostly_delayed_with_works"]
    text = (f"{mostly} of {row['roads']} main roads were rated Serious or worse at least half the time "
            f"({_pct(row['delayed_share'])} of all main-road time).")
    if mostly:
        text += (f" All {mostly} had roadworks on them" if with_works == mostly
                 else f" {with_works} of them had roadworks on them")
        if row["mostly_delayed_works_only"]:
            text += f", and {row['mostly_delayed_works_only']} had no incidents at all"
        text += "."
    busiest = (row.get("most_works") or [None])[0]
    if busiest and busiest["delayed_share"] == 0:
        text += (f" Roadworks alone don't explain it: {busiest['corridor_name']} had the most works "
                 f"({busiest['works']}) and was never delayed.")
    headline = ("Delayed main roads all had roadworks" if mostly and with_works == mostly
                else "Some main roads stay delayed for days" if mostly
                else "Main roads were mostly free of serious delays")
    first = (headline, text)

    types = _types(row)
    text = (f"A typical incident was cleared in {_duration(row['median_clear_minutes'])}."
            if row["median_clear_minutes"] is not None else "No incident has cleared yet.")
    if types:
        text += " " + _join([f"{_plural(t['sub_category']).capitalize() if i == 0 else _plural(t['sub_category'])} "
                             f"took {_duration(t['median_minutes'])}" for i, t in enumerate(types)]) + "."
    text += (f" {row['incidents_on_main_roads']} of the {row['incidents']} incidents were on a main road.")
    second = (f"{_plural(types[-1]['sub_category']).capitalize()} take longest to clear" if len(types) > 1
              else "How long incidents take to clear", text)

    ended, overran = row["works_ended"], row["works_overran"]
    if ended:
        text = (f"{overran} of the {ended} roadworks that ended ({_pct(row['works_overrun_share'])}) were still "
                "listed after their planned end")
        if row["median_overrun_minutes"] is not None:
            text += f", by a median of {_duration(row['median_overrun_minutes'])}"
        text += (f"; {row['works_overran_day']} overran by more than a day." if row["works_overran_day"]
                 else "; none overran by more than a day.")
        short = row["median_overrun_minutes"] is not None and row["median_overrun_minutes"] < 60
        headline = ("Roadworks end late, but mostly by minutes" if short and not row["works_overran_day"]
                    else "Roadworks overrun their planned end")
    else:
        text, headline = "No roadworks ended in this period.", "Roadworks overruns"
    return [first, second, (headline, text)]


def recommendations(row):
    """(action, detail) bullets in the analyst's wording, naming this week's roads and types."""
    bullets = []
    roads = _roads_to_review(row)
    if roads:
        bullets.append(("Review the roadworks on the delayed main roads,",
                        f"starting with {_join(roads)}, which have the most works among roads rated Serious."))
    types = _types(row)
    if len(types) > 1:
        bullets.append((f"Set a repair target for {_plural(types[-1]['sub_category'])}:",
                        f"they took {_duration(types[-1]['median_minutes'])} to clear, the longest of any type."))
    if row["works_ended"] and not row["works_overran_day"]:
        bullets.append(("Don't chase short roadwork overruns:",
                        "most end within minutes of plan; act on works that run more than a day late."))
    else:
        bullets.append(("Follow up roadworks that overrun by more than a day",
                        "with the boroughs and utilities responsible."))
    return bullets


def period(row):
    """'29–30 October 2026': from the first day with data, as road collection began on 29 October."""
    first, last = (date.fromisoformat(v) if isinstance(v, str) else v for v in (row["data_from"], row["last_day"]))
    if first == last:
        return f"{last.day} {last:%B %Y}"
    start = f"{first.day}" if first.month == last.month else f"{first.day} {first:%B}"
    return f"{start}–{last.day} {last:%B %Y}"
