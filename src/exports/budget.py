"""Keep the Google Sheet under Tableau's 10 MB limit, every night.

Tableau reads a Google Sheet by downloading it as an Excel file, and refuses one over 10 MB. The
export therefore prepares every tab before writing any of them:

1. Columns no dashboard uses are left blank (config "blank"), so the Sheet keeps the same
   columns and Tableau's data sources still find every field; numbers are rounded to the
   precision the dashboards show (config "round").
2. Older detail is merged where a dashboard only needs it summed (config "collapse"): bus days
   older than a week become one '0 All day' row per route instead of six time bands.
3. The Excel size of all tabs is measured (openpyxl builds the same .xlsx in memory).
4. While it is over the budget, the oldest day is dropped from the tab with the lowest
   "trim" order that still has more than its minimum days, and the size is measured again.

The estimate's budget is 8 MB because Google's own Excel export carries formatting the estimate
doesn't (9.66 MB for an 8.4 MB estimate on 2 November 2026). After writing, the export downloads
Google's Excel file and counts its bytes; above 9.5 MB it trims again with the budget scaled by the
real size, rewrites the trimmed tabs and checks once more. BigQuery keeps the full history; only
the Sheet copy is trimmed.
"""

import io

BUDGET_BYTES = 8_000_000    # the estimate; Google's own export measured 15% larger (2 Nov 2026)
REAL_LIMIT = 9_500_000      # Google's export above this after writing: trim more and write again
REAL_TARGET = 8_800_000     # ...aiming for this


def blank_columns(values, columns):
    """`values` (header row first) with the named columns emptied, in place."""
    if not columns:
        return values
    header = values[0]
    positions = [i for i, name in enumerate(header) if name in columns]
    for row in values[1:]:
        for i in positions:
            row[i] = ""
    return values


def collapse_older(values, rule, today):
    """Rows older than `rule["keep_days"]` days merged into one row per key with `rule["column"]`
    set to `rule["label"]` (e.g. bus time bands into '0 All day'). Counts in `sum` are added;
    `weighted` values are averaged weighted by `rule["weight"]` over the rows that have a value,
    which is how the Tableau formulas average them, so their results don't change."""
    from datetime import date, timedelta

    header = values[0]
    at = {name: i for i, name in enumerate(header)}
    cutoff = (date.fromisoformat(today) - timedelta(days=rule["keep_days"])).isoformat()
    day, weight = at[rule.get("day", "day")], at[rule["weight"]]
    keep, groups = [], {}
    for row in values[1:]:
        if row[day] >= cutoff:
            keep.append(row)
            continue
        key = tuple(row[at[k]] for k in rule["keys"])
        groups.setdefault(key, []).append(row)
    merged = []
    for key, rows in groups.items():
        out = [""] * len(header)
        for name, value in zip(rule["keys"], key):
            out[at[name]] = value
        out[at[rule["column"]]] = rule["label"]
        for name in rule.get("sum", []) + [rule["weight"]]:
            out[at[name]] = sum(r[at[name]] for r in rows if r[at[name]] != "")
        for name in rule.get("weighted", []):
            pairs = [(r[at[name]], r[weight]) for r in rows if r[at[name]] != ""]
            total = sum(w for _, w in pairs)
            out[at[name]] = round(sum(v * w for v, w in pairs) / total, rule.get("digits", 4)) if total else ""
        merged.append(out)
    merged.sort(key=lambda r: [r[at[k]] for k in rule["keys"]])
    return [header] + merged + keep


def round_columns(values, digits):
    """`values` with the named numeric columns rounded ({column: decimals}), in place: fewer
    digits make a smaller file (coordinates to 4 decimals are about 11 m)."""
    if not digits:
        return values
    positions = [(i, digits[name]) for i, name in enumerate(values[0]) if name in digits]
    for row in values[1:]:
        for i, d in positions:
            if isinstance(row[i], float):
                row[i] = round(row[i], d) if d else int(round(row[i]))
    return values


def xlsx_bytes(tabs):
    """The size of an .xlsx holding `tabs` ({tab: values}), built in memory."""
    import openpyxl

    book = openpyxl.Workbook(write_only=True)
    for tab, values in tabs.items():
        sheet = book.create_sheet(tab[:31])
        for row in values:
            sheet.append([None if v == "" else v for v in row])
    out = io.BytesIO()
    book.save(out)
    return len(out.getvalue())


def _days(values, column):
    i = values[0].index(column)
    return sorted({row[i] for row in values[1:]})


def fit(tabs, rules, budget=BUDGET_BYTES, measure=xlsx_bytes, log=print):
    """Drop the oldest days from trimmable tabs until `measure(tabs)` is within `budget`.
    `rules[tab]["trim"]` = {"order": n, "min_days": d, "column": "day"}. Each tab is measured
    once and scaled by the rows it keeps; the whole Sheet is measured again at the end (and
    trimming continues if the estimate was short). Returns the measured size."""
    trimmable = sorted((r["trim"]["order"], tab) for tab, r in rules.items() if "trim" in r and tab in tabs)
    size = measure(tabs)
    log(f"Sheet as Excel: about {size / 1e6:.1f} MB (budget {budget / 1e6:.1f} MB)")
    while size > budget:
        parts = {tab: measure({tab: values}) for tab, values in tabs.items()}
        scale = size / sum(parts.values())   # one workbook is a little smaller than separate ones
        while sum(parts.values()) * scale > budget:
            for _, tab in trimmable:
                trim = rules[tab]["trim"]
                column = trim.get("column", "day")
                days = _days(tabs[tab], column)
                if len(days) > trim["min_days"]:
                    i = tabs[tab][0].index(column)
                    before = len(tabs[tab])
                    tabs[tab] = [tabs[tab][0]] + [row for row in tabs[tab][1:] if row[i] != days[0]]
                    parts[tab] *= (len(tabs[tab]) - 1) / (before - 1)   # data rows, not the header
                    log(f"  dropped {days[0]} from '{tab}' ({len(days) - 1} days left)")
                    break
            else:
                raise RuntimeError(
                    f"The Sheet would be {sum(parts.values()) * scale / 1e6:.1f} MB even with every tab at its "
                    f"minimum days (budget {budget / 1e6:.1f} MB); lower the minimums or the tabs' detail.")
        size = measure(tabs)
    log(f"Sheet as Excel: about {size / 1e6:.1f} MB")
    return size
