"""Drug Interactions — co-reported drug-pair signals (future-enhancement #6)."""
from __future__ import annotations

import altair as alt
import pathlib as _pl, sys as _sys
_d = _pl.Path(__file__).resolve().parent
_sys.path.insert(0, str(_d if (_d / "lib.py").exists() else _d.parent))
import lib
import streamlit as st

lib.page_setup("Drug Interactions", "🔗")
st.title("🔗 Drug–Drug Interaction Signals")
lib.source_badge()
st.info(
    "Adverse events reported disproportionately in cases listing **two drugs together**, "
    "compared to each drug alone. A high interaction ratio is a *reporting* signal that "
    "may warrant interaction review — **not** proof of a pharmacological interaction."
)
lib.caveat()

if not lib.has("interaction_signals"):
    st.warning("No interaction_signals table found. Run `make` target `build_interactions`.")
    st.stop()

inter = lib.gold("interaction_signals")

with st.sidebar:
    st.header("Filters")
    only_flagged = st.checkbox("Only flagged interaction signals", value=True)
    min_reports = st.slider("Min combination reports", 1, 50, 5, 1)

df = inter[inter["pair_event_reports"] >= min_reports].copy()
if only_flagged:
    df = df[df["interaction_flag"]]
df = df.sort_values("interaction_ratio", ascending=False)

c1, c2, c3 = st.columns(3)
c1.metric("Co-reported drug pairs", f"{inter[['drug_a','drug_b']].drop_duplicates().shape[0]:,}")
c2.metric("Interaction signals", f"{len(inter):,}")
c3.metric("Flagged", f"{int(inter['interaction_flag'].sum()):,}")

st.subheader("Interaction candidates")
st.caption("Interaction ratio = combination ROR ÷ the stronger single-drug ROR. >1 means the pair reports the event more than either drug alone.")
show = df.head(40).copy()
for c in ["ror_combination", "single_max_ror", "interaction_ratio", "ror_drug_a", "ror_drug_b"]:
    show[c] = show[c].map(lib.fmt)
st.dataframe(
    show[["drug_a", "drug_b", "adverse_event", "co_reports", "pair_event_reports",
          "ror_combination", "single_max_ror", "interaction_ratio", "interaction_flag"]].rename(columns={
        "drug_a": "Drug A", "drug_b": "Drug B", "adverse_event": "Adverse event",
        "co_reports": "Co-reports", "pair_event_reports": "A+B+event",
        "ror_combination": "Combo ROR", "single_max_ror": "Max single ROR",
        "interaction_ratio": "Interaction ×", "interaction_flag": "Flag"}),
    hide_index=True, width="stretch", height=440)

if not df.empty:
    st.subheader("Combination vs. single-drug ROR")
    plot = df.head(20).copy()
    plot["pair"] = plot["drug_a"] + " + " + plot["drug_b"] + "\n" + plot["adverse_event"]
    chart = (
        alt.Chart(plot).mark_circle(opacity=0.7).encode(
            x=alt.X("single_max_ror:Q", title="Stronger single-drug ROR", scale=alt.Scale(type="log")),
            y=alt.Y("ror_combination:Q", title="Combination ROR", scale=alt.Scale(type="log")),
            size=alt.Size("pair_event_reports:Q", title="A+B+event reports"),
            color=alt.Color("interaction_flag:N", title="Flagged"),
            tooltip=["drug_a", "drug_b", "adverse_event", "ror_combination", "single_max_ror", "interaction_ratio"],
        ).properties(height=380).interactive()
    )
    diag = alt.Chart(plot).mark_line(strokeDash=[4, 4], color="#888").encode(
        x="single_max_ror:Q", y="single_max_ror:Q")
    st.altair_chart(chart + diag, use_container_width=True)
    st.caption("Points above the dashed line: the combination reports the event more strongly than either drug alone.")
