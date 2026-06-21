"""PharmaSignal — Executive Overview (Streamlit entrypoint).

Run: streamlit run dashboard/app.py   (or `make dashboard`)
"""
from __future__ import annotations

import altair as alt
import pathlib as _pl, sys as _sys
_d = _pl.Path(__file__).resolve().parent
_sys.path.insert(0, str(_d if (_d / "lib.py").exists() else _d.parent))
import lib
import streamlit as st

lib.page_setup("Overview", "🧭")

st.title("🧭 PharmaSignal — Executive Overview")
st.caption(
    "A cloud-native pharmacovigilance, population-context, and biomedical-literature "
    "intelligence platform. Domain: metabolic / GLP-1 therapies."
)
lib.source_badge()
lib.caveat()

try:
    scores = lib.gold("signal_scores")
    emerging = lib.gold("emerging_signals")
    health = lib.gold("pipeline_health")
except FileNotFoundError as exc:
    st.error(str(exc))
    st.stop()

th = lib.thresholds()
latest = health.iloc[0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Drug-event pairs scored", f"{len(scores):,}")
c2.metric("Disproportionality flags", f"{int(scores['disproportionality_flag'].sum()):,}")
c3.metric("High-priority emerging", f"{int((emerging['priority_level'] == 'High').sum()):,}")
c4.metric("FAERS reports indexed", f"{int(latest['rows_raw']):,}")

st.caption(
    f"Latest run: **{latest['source']}** · period **{latest['source_period']}** · "
    f"status **{latest['status']}** · refreshed {str(latest['run_timestamp'])[:19]} UTC"
)

st.subheader("Top emerging signals (composite priority)")
st.caption(
    "Priority blends disproportionality, trend anomaly, seriousness, literature "
    "support, and population context. Components are always shown — never hidden."
)
top = emerging.sort_values("priority_score", ascending=False).head(15).copy()
display = top[[
    "drug_name_normalized", "adverse_event", "drug_class", "current_count",
    "percent_change", "anomaly_score", "seriousness_rate", "priority_score", "priority_level",
]].rename(columns={
    "drug_name_normalized": "Drug", "adverse_event": "Adverse event", "drug_class": "Class",
    "current_count": "Latest qtr", "percent_change": "% change", "anomaly_score": "Anomaly z",
    "seriousness_rate": "Serious rate", "priority_score": "Priority", "priority_level": "Level",
})
st.dataframe(display, hide_index=True, width="stretch")

st.subheader("Signal landscape")
st.caption("Bubble: x = disproportionality (log ROR), y = report count, size = serious reports, color = drug class.")
plot_df = scores[scores["a_drug_event"] >= th.minimum_reports].copy()
plot_df["log_ror"] = plot_df["ror"].clip(lower=0.01).apply(lambda x: __import__("math").log10(x))
chart = (
    alt.Chart(plot_df)
    .mark_circle(opacity=0.55)
    .encode(
        x=alt.X("log_ror:Q", title="log10(ROR)  →  stronger disproportionality"),
        y=alt.Y("a_drug_event:Q", title="Report count (a)", scale=alt.Scale(type="log")),
        size=alt.Size("seriousness_rate:Q", title="Serious rate"),
        color=alt.Color("drug_class:N", title="Drug class"),
        tooltip=["drug_name_normalized", "adverse_event", "ror", "prr", "a_drug_event"],
    )
    .properties(height=420)
    .interactive()
)
st.altair_chart(chart, use_container_width=True)

st.info(
    "Use the pages in the sidebar: **Signal Explorer**, **Drug Profile**, "
    "**Event Profile**, **Emerging Signals**, **Literature Evidence**, "
    "**NHANES Context**, **Pipeline Health**, and **Methodology**."
)
