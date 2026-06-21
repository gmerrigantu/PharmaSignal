"""Emerging Signals — monitoring feed of recently increasing / high-priority pairs (§12.2)."""
from __future__ import annotations

import altair as alt
import pathlib as _pl, sys as _sys
_d = _pl.Path(__file__).resolve().parent
_sys.path.insert(0, str(_d if (_d / "lib.py").exists() else _d.parent))
import lib
import streamlit as st

lib.page_setup("Emerging Signals", "📈")
st.title("📈 Emerging Signals")
lib.source_badge()
lib.caveat()

emerging = lib.gold("emerging_signals")

with st.sidebar:
    st.header("Filters")
    levels = st.multiselect("Priority level", ["High", "Moderate", "Low"], default=["High", "Moderate"])
    min_change = st.slider("Minimum % change vs baseline", -100, 400, 0, 10)

df = emerging[emerging["priority_level"].isin(levels)].copy()
df = df[(df["percent_change"] * 100).fillna(-999) >= min_change]
df = df.sort_values("priority_score", ascending=False)
st.caption(f"{len(df):,} emerging signals match filters. Current quarter: {emerging['current_quarter'].iloc[0] if not emerging.empty else '—'}.")

# Signal cards for the top few.
for _, r in df.head(6).iterrows():
    with st.container(border=True):
        cols = st.columns([3, 1, 1, 1, 1])
        cols[0].markdown(f"**{r['drug_name_normalized'].title()} → {r['adverse_event'].title()}**  \n*{r['drug_class']}*")
        cols[1].metric("Priority", f"{r['priority_score']:.2f}", r["priority_level"])
        cols[2].metric("Latest qtr", int(r["current_count"]))
        pc = r["percent_change"]
        cols[3].metric("% change", "n/a" if pc != pc else f"{pc*100:.0f}%")
        cols[4].metric("Anomaly z", "n/a" if r["anomaly_score"] != r["anomaly_score"] else f"{r['anomaly_score']:.1f}")
        st.caption(
            f"Baseline ~{r['trailing_baseline_count']:.1f}/qtr · seriousness {r['seriousness_rate']*100:.0f}% · "
            f"literature articles {int(r['literature_support_count'])} · "
            f"NHANES context {'yes' if r['nhanes_context_available'] else 'no'}"
        )

st.subheader("Priority composition")
st.caption("Higher composite priority is driven by disproportionality, trend anomaly, seriousness, literature, and population context.")
scatter = (
    alt.Chart(df).mark_circle(opacity=0.6).encode(
        x=alt.X("anomaly_score:Q", title="Trend anomaly (z-score)"),
        y=alt.Y("seriousness_rate:Q", title="Seriousness rate"),
        size=alt.Size("current_count:Q", title="Latest qtr count"),
        color=alt.Color("priority_level:N", scale=alt.Scale(domain=["High", "Moderate", "Low"],
                                                            range=["#dc2626", "#f59e0b", "#9ca3af"])),
        tooltip=["drug_name_normalized", "adverse_event", "priority_score", "percent_change"],
    ).properties(height=420).interactive()
)
st.altair_chart(scatter, use_container_width=True)

st.dataframe(
    df[["drug_name_normalized", "adverse_event", "current_count", "trailing_baseline_count",
        "percent_change", "anomaly_score", "priority_score", "priority_level"]].rename(columns={
        "drug_name_normalized": "Drug", "adverse_event": "Event", "current_count": "Latest",
        "trailing_baseline_count": "Baseline", "percent_change": "% chg", "anomaly_score": "z",
        "priority_score": "Priority", "priority_level": "Level"}),
    hide_index=True, width="stretch",
)
