"""Drug Profile — summarize all report patterns for one drug (§12.2)."""
from __future__ import annotations

import altair as alt
import pathlib as _pl, sys as _sys
_d = _pl.Path(__file__).resolve().parent
_sys.path.insert(0, str(_d if (_d / "lib.py").exists() else _d.parent))
import lib
import streamlit as st

lib.page_setup("Drug Profile", "💊")
st.title("💊 Drug Profile")
lib.source_badge()
lib.caveat()

scores = lib.gold("signal_scores")
drugs = sorted(scores["drug_name_normalized"].unique().tolist())
drug = st.selectbox("Drug", drugs)

d = scores[scores["drug_name_normalized"] == drug].copy()
cls = d["drug_class"].iloc[0] if not d.empty else "—"

c1, c2, c3, c4 = st.columns(4)
c1.metric("Drug class", cls)
c2.metric("Distinct events", f"{d['adverse_event'].nunique():,}")
c3.metric("Total reports", f"{int(d['a_drug_event'].sum()):,}")
c4.metric("Flagged signals", f"{int(d['disproportionality_flag'].sum()):,}")

st.subheader("Top reported adverse events")
top = d.sort_values("a_drug_event", ascending=False).head(20)
bar = (
    alt.Chart(top).mark_bar().encode(
        x=alt.X("a_drug_event:Q", title="Report count"),
        y=alt.Y("adverse_event:N", sort="-x", title=None),
        color=alt.Color("seriousness_rate:Q", title="Serious rate", scale=alt.Scale(scheme="reds")),
        tooltip=["adverse_event", "a_drug_event", "ror", "prr"],
    ).properties(height=480)
)
st.altair_chart(bar, use_container_width=True)

st.subheader("Strongest disproportionality signals")
sig = d.sort_values("ror", ascending=False).head(15)[
    ["adverse_event", "a_drug_event", "ror", "ror_ci_lower", "prr", "chi_square", "seriousness_rate"]
].copy()
for col in ["ror", "ror_ci_lower", "prr", "chi_square"]:
    sig[col] = sig[col].map(lib.fmt)
sig["seriousness_rate"] = (d.sort_values("ror", ascending=False).head(15)["seriousness_rate"] * 100).map(lambda v: f"{v:.0f}%")
st.dataframe(
    sig.rename(columns={
        "adverse_event": "Adverse event", "a_drug_event": "Reports", "ror": "ROR",
        "ror_ci_lower": "ROR CI low", "prr": "PRR", "chi_square": "χ²", "seriousness_rate": "Serious",
    }),
    hide_index=True, width="stretch",
)

# NHANES user profile if available.
if lib.has("nhanes_population_context"):
    nh = lib.gold("nhanes_population_context")
    row = nh[nh["medication_name_normalized"] == drug]
    if not row.empty:
        st.subheader("NHANES medication-user profile (population context)")
        r = row.iloc[0]
        if r.get("very_small_n_flag"):
            st.error(f"Very small NHANES sample (n={int(r['unweighted_sample_count'])}) — estimate is unstable.")
        elif r.get("small_n_flag"):
            st.warning(f"Small NHANES sample (n={int(r['unweighted_sample_count'])}) — interpret with caution.")
        cols = st.columns(5)
        cols[0].metric("Weighted prevalence", f"{r['weighted_prevalence']*100:.2f}%")
        cols[1].metric("Median age", f"{r['median_age']:.0f}")
        cols[2].metric("% female", f"{r['female_percent']:.0f}%")
        cols[3].metric("% BMI ≥ 30", f"{r['bmi_ge_30_percent']:.0f}%")
        cols[4].metric("Median HbA1c", f"{r['hba1c_median']:.1f}")
        st.caption(
            f"Survey cycle {r['survey_cycle']} · unweighted n = {int(r['unweighted_sample_count'])} · "
            f"weight variable {r['weight_variable_used']}. NHANES is not linked to FAERS at the person level."
        )
