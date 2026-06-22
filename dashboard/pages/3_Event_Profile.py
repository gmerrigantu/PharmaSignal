"""Event Profile — start from an adverse event, compare associated drugs (§12.2)."""
from __future__ import annotations

import altair as alt
import pathlib as _pl, sys as _sys
_d = _pl.Path(__file__).resolve().parent
_sys.path.insert(0, str(_d if (_d / "lib.py").exists() else _d.parent))
import lib
import streamlit as st

lib.page_setup("Event Profile", "🩺")
st.title("🩺 Event Profile")
lib.source_badge()
lib.caveat()

scores = lib.gold("signal_scores")
events = sorted(scores["adverse_event"].unique().tolist())
event = st.selectbox("Adverse event", events, index=events.index("PANCREATITIS") if "PANCREATITIS" in events else 0)

e = scores[scores["adverse_event"] == event].copy()
if "seriousness_rate" not in e.columns:
    e["seriousness_rate"] = 0.0
c1, c2, c3 = st.columns(3)
c1.metric("Drugs reporting this event", f"{e['drug_name_normalized'].nunique():,}")
c2.metric("Total reports", f"{int(e['a_drug_event'].sum()):,}")
c3.metric("Flagged drug associations", f"{int(e['disproportionality_flag'].sum()):,}")

st.subheader("Drugs ranked by ROR for this event")
ranked = e.sort_values("ror", ascending=False).head(20)
bar = (
    alt.Chart(ranked).mark_bar().encode(
        x=alt.X("ror:Q", title="ROR (log scale)", scale=alt.Scale(type="log")),
        y=alt.Y("drug_name_normalized:N", sort="-x", title=None),
        color=alt.Color("drug_class:N", title="Class"),
        tooltip=["drug_name_normalized", "ror", "prr", "a_drug_event"],
    ).properties(height=460)
)
st.altair_chart(bar, use_container_width=True)

tbl = ranked[["drug_name_normalized", "drug_class", "a_drug_event", "ror", "ror_ci_lower", "prr", "seriousness_rate"]].copy()
for col in ["ror", "ror_ci_lower", "prr"]:
    tbl[col] = tbl[col].map(lib.fmt)
tbl["seriousness_rate"] = (ranked["seriousness_rate"] * 100).map(lambda v: f"{v:.0f}%")
st.dataframe(
    tbl.rename(columns={
        "drug_name_normalized": "Drug", "drug_class": "Class", "a_drug_event": "Reports",
        "ror": "ROR", "ror_ci_lower": "ROR CI low", "prr": "PRR", "seriousness_rate": "Serious",
    }),
    hide_index=True, width="stretch",
)
