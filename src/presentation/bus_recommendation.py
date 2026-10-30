"""Write the bus business recommendation's summary and findings from the latest figures
(one row of tfl_portfolio.bus_recommendation: the last 7 complete London days).

Pure functions, shared by the portfolio and future MCP tools. Numbers come from the row; where
a headline is a conclusion ("Bunching is steady", "about half the London average"), a simple
rule picks wording the figures support. The recommendations keep the analyst's wording; only the
routes they name come from the data, so they always point at the routes in the findings.
"""

from datetime import date

TARGET_KMH = 6   # the speed the fleet saving is worked out for


def _pct(value, decimals=0):
    return "–" if value is None else f"{value * 100:.{decimals}f}%"


def _routes(items):
    names = [str(item["route"]) for item in items]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def _range(low, high, fmt):
    return fmt.format(low) if fmt.format(low) == fmt.format(high) else f"{fmt.format(low)}–{fmt.format(high)}"


def _wait_comparison(ratio):
    if ratio >= 1.75:
        return "nearly twice as long as"
    if ratio >= 1.45:
        return "about half as long again as"
    return f"{(ratio - 1) * 100:.0f}% longer than"


def summary(row):
    """The summary's figures (the recommendation sentence after it stays fixed)."""
    extra = row["extra_wait_minutes"]
    if extra is None or extra <= 0.05:
        return ("Buses were spaced evenly enough that uneven spacing added almost nothing to the "
                "average wait.")
    hours = round(row["passenger_hours_per_day"], -3)
    return (
        f"Uneven bus spacing adds about {extra:.1f} minutes to the average wait, "
        f"{_pct(row['extra_wait_share'])} more than an evenly spaced service. That is up to "
        f"{hours:,.0f} passenger-hours a day across about 5 million daily journeys "
        f"(TfL: 1.84 billion journeys in 2024/25)."
    )


def findings(row):
    """Three (headline, text) findings."""
    days = row["days"]
    steady = (row["bunching_max"] - row["bunching_min"]) <= 0.01
    day_words = f"each of the {days} days measured" if days > 1 else "the day measured"
    first = (
        "Bunching is steady" if steady else "Bunching varies day to day",
        f"{_pct(row['bunching_rate'], 1)} of gaps were bunched "
        + (f"({_range(row['bunching_min'] * 100, row['bunching_max'] * 100, '{:.1f}')}% on {day_words}). "
           if days > 1 else f"on {day_words}. ")
        + f"Buses came every {row['gap_minutes']:.0f} minutes, but passengers waited about "
          f"{row['wait_minutes']:.0f} minutes, not {row['even_wait_minutes']:.0f}.",
    )

    bunched = list(row.get("most_bunched") or [])
    if bunched:
        rates = [b["bunching_rate"] for b in bunched]
        top = bunched[0]
        second = (
            "Frequent routes bunch most",
            f"on routes {_routes(bunched)}, {_range(min(rates) * 100, max(rates) * 100, '{:.0f}')}% of gaps "
            f"are bunched. On route {top['route']}, passengers wait {_wait_comparison(top['wait_ratio'])} "
            "even spacing would give.",
        )
    else:
        second = ("Frequent routes bunch most", "no frequent route had enough data this week.")

    slow = list(row.get("slowest") or [])
    network = row["network_kmh"]
    if slow:
        speeds = [s["kmh"] for s in slow]
        compared = ("about half the London average" if max(speeds) <= 0.6 * network
                    else "well below the London average")
        text = (f"{_routes(slow)} average {_range(min(speeds), max(speeds), '{:.1f}')} km/h, {compared} "
                f"of about {network:.0f} km/h.")
        slowest = slow[0]
        if slowest["kmh"] < TARGET_KMH:
            text += (f" Raising route {slowest['route']} to {TARGET_KMH} km/h would need up to "
                     f"{_pct(1 - slowest['kmh'] / TARGET_KMH)} fewer buses for the same service.")
        third = ("The slowest routes need speed, not buses", text)
    else:
        third = ("The slowest routes need speed, not buses", "no route had enough data this week.")
    return [first, second, third]


def recommendations(row):
    """(action, detail) bullets in the analyst's wording, naming this week's routes."""
    bullets = []
    if row.get("most_bunched"):
        bullets.append(("Pilot headway control", f"on routes {_routes(row['most_bunched'])} (low cost)."))
    if row.get("slowest"):
        bullets.append(("Review bus priority", f"on routes {_routes(row['slowest'])} (analysis only)."))
    bullets.append(("Don't add buses yet:", "long waits are mostly by design, and slow routes need road space."))
    return bullets


def period(row):
    """'26–29 October 2026': from the first day with data, as bus collection began on 26 October."""
    first, last = (date.fromisoformat(v) if isinstance(v, str) else v for v in (row["data_from"], row["last_day"]))
    start = f"{first.day}" if first.month == last.month else f"{first.day} {first:%B}"
    return f"{start}–{last.day} {last:%B %Y}"
