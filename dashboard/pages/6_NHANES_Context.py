"""NHANES Context — medication-user population context (§10, §12.2)."""
from __future__ import annotations

import altair as alt
import pathlib as _pl, sys as _sys
_d = _pl.Path(__file__).resolve().parent
_sys.path.insert(0, str(_d if (_d / "lib.py").exists() else _d.parent))
import lib
import streamlit as st

lib.page_setup("NHANES Context", "👥")
st.title("👥 NHANES Population Context")
lib.source_badge()
st.info(
    "NHANES provides **aggregate population context** — how common a medication/class "
    "is and what users look like. It is **never** linked to FAERS at the person level, "
    "and these are not incidence denominators."
)
lib.caveat()

if not lib.has("nhanes_population_context"):
    st.warning("No NHANES table found. Run `make nhanes` (downloads XPT) or `make demo`.")
    st.stop()

nh = lib.gold("nhanes_population_context")

st.subheader("Weighted medication-use prevalence")
prev = nh.sort_values("weighted_prevalence", ascending=False).copy()
prev["pct"] = prev["weighted_prevalence"] * 100
bar = alt.Chart(prev).mark_bar().encode(
    x=alt.X("pct:Q", title="Weighted prevalence (%)"),
    y=alt.Y("medication_name_normalized:N", sort="-x", title=None),
    color=alt.Color("drug_class:N", title="Class"),
    tooltip=["medication_name_normalized", "pct", "unweighted_sample_count"],
).properties(height=360)
st.altair_chart(bar, use_container_width=True)

st.caption("Unweighted sample counts are shown below — small counts mean unstable estimates.")
show = nh.copy()
show["weighted_prevalence"] = (show["weighted_prevalence"] * 100).map(lambda v: f"{v:.2f}%")
show["stability"] = show.apply(
    lambda r: "🔴 very small n" if r["very_small_n_flag"] else ("🟡 small n" if r["small_n_flag"] else "🟢 ok"), axis=1)
st.dataframe(
    show[["medication_name_normalized", "drug_class", "weighted_prevalence", "unweighted_sample_count",
          "median_age", "female_percent", "bmi_ge_30_percent", "diabetes_percent", "hba1c_median",
          "weight_variable_used", "stability"]].rename(columns={
        "medication_name_normalized": "Medication", "drug_class": "Class",
        "weighted_prevalence": "Wtd prevalence", "unweighted_sample_count": "Unwtd n",
        "median_age": "Median age", "female_percent": "% female", "bmi_ge_30_percent": "% BMI≥30",
        "diabetes_percent": "% diabetes", "hba1c_median": "HbA1c", "weight_variable_used": "Weight var",
        "stability": "Stability"}),
    hide_index=True, width="stretch",
)

# FAERS vs NHANES demographic comparison.
st.subheader("FAERS vs NHANES — interpretation note")
st.write(
    "When FAERS report demographics differ from NHANES medication-user demographics, "
    "the difference reflects **reporting patterns**, not true risk differences. Person-level "
    "linkage is intentionally not performed."
)
