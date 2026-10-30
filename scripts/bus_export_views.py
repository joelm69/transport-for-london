"""Regenerate analytics/bus_network/02_export_views.sql from the two bus export queries.

    python scripts/bus_export_views.py

The views let BigQuery calculate the bus recommendation exactly as the Sheet (and so the
Tableau dashboard) does. Run this after changing either export, then apply the file in BigQuery.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORTS = {
    "bus_routes_daily": "ORDER BY route, day, direction, time_band;",
    "bus_vehicle_routes_daily": "ORDER BY day, bus, km DESC;",
}
HEADER = """-- Views over the two bus Sheet exports, so the portfolio's bus recommendation is calculated
-- exactly as the Tableau dashboard is. GENERATED: each view's body is its export query
-- (analytics/exports/bus_routes_daily.sql, bus_vehicle_routes_daily.sql) without comments and
-- ORDER BY. tests/unit/test_bus_recommendation.py fails if an export changes and this file
-- doesn't; regenerate with scripts/bus_export_views.py.
"""


def view_body(name):
    """The export query without its comment lines and final ORDER BY."""
    lines = [line for line in (ROOT / "analytics" / "exports" / f"{name}.sql").read_text().splitlines()
             if not line.startswith("--")]
    sql = "\n".join(lines).strip()
    order_by = EXPORTS[name]
    if not sql.endswith(order_by):
        raise SystemExit(f"{name}.sql no longer ends with {order_by!r}: update EXPORTS")
    return sql[: -len(order_by)].rstrip()


def render():
    views = "".join(
        f"\nCREATE OR REPLACE VIEW `transport-for-london-511318.tfl_transport_analytics.{name}` AS\n"
        f"{view_body(name)};\n" for name in EXPORTS)
    return HEADER + views


if __name__ == "__main__":
    (ROOT / "analytics" / "bus_network" / "02_export_views.sql").write_text(render())
    print("Wrote analytics/bus_network/02_export_views.sql")
