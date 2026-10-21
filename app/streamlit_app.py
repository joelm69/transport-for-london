"""London rail live status board (hot path).

Run locally:
    .venv/bin/streamlit run app/streamlit_app.py

Streamlit reruns this script on every interaction, so the TfL call is
cached for 30 s (TfL's own max-age) and shared by all visitors.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.clients.tfl import TflClient, TflError  # noqa: E402
from src.presentation.line_board import MODE_NAMES, board_rows, summary  # noqa: E402

CONFIG = json.loads((PROJECT_ROOT / "config" / "line_status.json").read_text(encoding="utf-8"))
REFRESH_SECONDS = 30
LEVEL_STYLE = {
    "stopped": ("red", "🔴"),
    "delays": ("orange", "🟠"),
    "closed": ("gray", "⚪"),
    "good": ("green", "🟢"),
}

st.set_page_config(page_title="London Rail Live Status", page_icon="🚇", layout="wide")


@st.cache_resource
def tfl_client():
    """One client per server process; it holds no per-visitor state."""
    return TflClient.from_environment(max_attempts=3, timeout=15)


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def fetch_line_status():
    """Cached for all visitors; TfL is called at most once per 30 s."""
    response = tfl_client().get_json(CONFIG["endpoint"], CONFIG["params"])
    return response.data, response.fetched_at_utc


def render_disruption(row):
    colour, dot = LEVEL_STYLE[row["level"]]
    with st.container(border=True):
        left, right = st.columns([3, 1])
        label = f"**{row['line_name']}**"
        if row["mode_name"] != row["line_name"]:  # avoid "Tram · Tram"
            label += f" · {row['mode_name']}"
        left.markdown(f"{dot} {label}")
        left.markdown(f":{colour}[**{row['status']}**]")
        if row["duration"]:
            right.metric("Disrupted for", row["duration"], help="Since TfL's reported start time")
        if row["reason"]:
            st.caption(row["reason"])


st.title("🚇 London Rail — Live Status")
st.caption(
    "Tube, DLR, Overground, Elizabeth line and tram · refreshes every 30 seconds · "
    "Powered by TfL Open Data"
)

selected = st.multiselect(
    "Modes",
    options=list(MODE_NAMES),
    default=list(MODE_NAMES),
    format_func=MODE_NAMES.get,
)


@st.fragment(run_every=REFRESH_SECONDS)
def live_board(modes):
    try:
        data, fetched_at = fetch_line_status()
    except TflError:
        st.error("TfL's live feed is not responding right now. The board will retry automatically.")
        return

    now = datetime.now(timezone.utc)
    rows = board_rows(data, now, CONFIG["severity_classes"], set(modes) or None)
    counts = summary(rows)

    fetched = datetime.fromisoformat(fetched_at)
    age = int((now - fetched).total_seconds())
    st.caption(f"TfL data fetched at {fetched:%H:%M:%S} UTC ({age} s ago)")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Good service", counts["good"])
    c2.metric("Delays", counts["delays"])
    c3.metric("Part or fully stopped", counts["stopped"])
    c4.metric("Closed (night)", counts["closed"])

    problems = [r for r in rows if r["level"] in ("stopped", "delays")]
    if problems:
        st.subheader(f"Disruptions ({len(problems)})")
        for row in problems:
            render_disruption(row)
    else:
        st.success("Good service on all selected lines.")

    others = [r for r in rows if r["level"] in ("good", "closed")]
    if others:
        st.subheader("Running normally")
        cols = st.columns(4)
        for i, row in enumerate(others):
            _, dot = LEVEL_STYLE[row["level"]]
            cols[i % 4].markdown(f"{dot} {row['line_name']}  \n:gray[{row['status']}]")


if selected:
    live_board(selected)
else:
    st.info("Select at least one mode.")
