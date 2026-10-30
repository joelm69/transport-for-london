"""Write the rail business recommendation's summary and findings from the latest figures
(one row of tfl_portfolio.rail_recommendation: the last 7 complete London days).

Pure functions, shared by the portfolio and future MCP tools. Numbers always come from the
row; where a headline is a conclusion ("Trains, not track"), a simple rule picks the wording
that the figures support, so the text never claims something the data no longer shows. The
recommendations themselves are not written here: they are the analyst's judgement.
"""

from datetime import date

# How a cause reads in a sentence ("Central (57%, cancellations)").
CAUSE_WORDS = {
    "Train cancellations": "cancellations",
    "Train fault": "train faults",
    "Operational Incident": "an operational incident",
    "Signal failure": "signal failures",
    "Track fault": "track faults",
    "Points failure": "points failures",
    "Power failure": "power failures",
    "Customer incident": "customer incidents",
}


def _pct(value, decimals=0):
    """Service levels keep one decimal (65.2%); shares are whole numbers (54%)."""
    return "–" if value is None else f"{value * 100:.{decimals}f}%"


def _day(value):
    """'29 October' from a date or 'YYYY-MM-DD'."""
    value = date.fromisoformat(value) if isinstance(value, str) else value
    return f"{value.day} {value:%B}"


def _cause(name):
    return CAUSE_WORDS.get(name, (name or "unstated causes").lower())


def _trains_lead(row):
    return (row["train_share"] or 0) >= (row["infrastructure_share"] or 0)


def summary(row):
    """The summary paragraph's figures (the recommendation sentence after it stays fixed)."""
    lead_share, lead_words = ((row["train_share"], "train cancellations and faults") if _trains_lead(row)
                              else (row["infrastructure_share"], "signal, track, points and power failures"))
    return (
        f"Rail lines ran a good service {_pct(row['good_service'], 1)} of the time in the 7 days to "
        f"{_day(row['last_day'])}. Planned works caused {_pct(row['planned_share'])} of the disruption. "
        f"The unplanned {_pct(row['unplanned_share'])} cost {_pct(row['unplanned_service_share'], 1)} of "
        f"service time, and {_pct(lead_share)} of it came from {lead_words}."
    )


def findings(row):
    """Three (headline, text) findings."""
    first = ("Most disruption is unplanned" if (row["unplanned_share"] or 0) > 0.5 else "Most disruption is planned works",
             f"of {round(row['disrupted_hours']):,} disrupted line-hours, {round(row['unplanned_hours']):,} were "
             f"unplanned. Without planned works, good service rises to {_pct(row['good_service_without_planned'], 1)}.")

    trains, track = _pct(row["train_share"]), _pct(row["infrastructure_share"])
    second = (("Trains, not track", f"cancellations and train faults caused {trains} of unplanned disruption. "
                                    f"Signal, track, points and power failures caused {track}.")
              if _trains_lead(row) else
              ("Track, not trains", f"signal, track, points and power failures caused {track} of unplanned "
                                    f"disruption. Cancellations and train faults caused {trains}."))

    lines = list(row.get("top_lines") or [])
    parts = [f"{line['line_name']} ({_pct(line['unplanned_share'])}"
             + (f", {_cause(line['main_cause'])}" if line.get("main_cause") else "") + ")" for line in lines]
    names = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" and {parts[-1]}" if parts else ""
    count = {1: "One line", 2: "Two lines", 3: "Three lines"}.get(len(lines), f"{len(lines)} lines")
    third = (f"{count}, {_pct(row['top_lines_share'])}",
             f"{names}." if names else "no unplanned disruption this week.")
    return [first, second, third]


def period(row):
    """'23–29 October 2026' for the caption."""
    first, last = (date.fromisoformat(v) if isinstance(v, str) else v for v in (row["first_day"], row["last_day"]))
    start = f"{first.day}" if first.month == last.month else f"{first.day} {first:%B}"
    return f"{start}–{last.day} {last:%B %Y}"
