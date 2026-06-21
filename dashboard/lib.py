"""Shared helpers for the PharmaSignal Streamlit dashboard.

Importing this module puts ``src/`` on the path so pages can import the
``pharmasignal`` package. All gold-table reads go through cached accessors here so
the dashboard stays fast and reads precomputed tables only (requirements §13.2).
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from pharmasignal import config  # noqa: E402
from pharmasignal.serving import lakehouse  # noqa: E402

CAVEAT = (
    "⚠️ **Hypothesis-generating only.** FAERS/openFDA data are *spontaneous reports*. "
    "Disproportionality signals (ROR/PRR) reflect **reporting associations**, not "
    "causality, clinical risk, incidence, or prevalence. NHANES is aggregate "
    "population context and is **not** linked to FAERS at the person level."
)


def page_setup(title: str, icon: str = "🔬") -> None:
    st.set_page_config(page_title=f"PharmaSignal · {title}", page_icon=icon, layout="wide")


def caveat() -> None:
    st.warning(CAVEAT)


@st.cache_data(show_spinner=False)
def gold(name: str) -> pd.DataFrame:
    return lakehouse.read_gold(name)


def has(name: str) -> bool:
    return lakehouse.gold_exists(name)


def source_badge() -> None:
    src = lakehouse.active_source()
    label = {
        "demo": "🟡 DEMO DATA (synthetic — run `make pipeline` for live openFDA data)",
        "pipeline": "🟢 Live pipeline data",
        "none": "🔴 No data — run `make demo`",
    }.get(src, src)
    st.caption(f"Data source: {label}")


def thresholds():
    return config.load_thresholds()


def drug_domain():
    return config.load_drug_domain()


def fmt(value: float) -> str:
    """Compact numeric formatter for ratio columns."""
    if value is None or value != value:
        return "n/a"
    if value >= 100:
        return f"{value:,.0f}"
    if value >= 10:
        return f"{value:,.1f}"
    return f"{value:,.2f}"
