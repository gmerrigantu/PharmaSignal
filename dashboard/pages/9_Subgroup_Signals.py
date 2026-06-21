"""Subgroup Signals — disproportionality by age band and sex (future-enhancement #7)."""
from __future__ import annotations

import altair as alt
import pathlib as _pl, sys as _sys
_d = _pl.Path(__file__).resolve().parent
_sys.path.insert(0, str(_d if (_d / "lib.py").exists() else _d.parent))
import lib
import streamlit as st

lib.page_setup("Subgroup Signals", "👥")
st.title("👥 Subgroup Signals — age & sex")
lib.source_badge()
st.info(
    "Disproportionality (ROR) recomputed **within demographic strata**. A signal "
    "concentrated in one subgroup can be obscured in the overall population. Small "
    "strata are unstable — read alongside the report counts."
)
lib.caveat()

if not lib.has("subgroup_signals"):
    st.warning("No subgroup_signals table found. Run `make` target `build_subgroups`.")
    st.stop()

sub = lib.gold("subgroup_signals")
pairs = (sub[["drug_name_normalized", "adverse_event"]].drop_duplicates()
         .assign(label=lambda d: d["drug_name_normalized"] + " — " + d["adverse_event"]))
label = st.selectbox("Drug-event signal", sorted(pairs["label"].tolist()))
drug, event = pairs[pairs["label"] == label][["drug_name_normalized", "adverse_event"]].iloc[0]

sel = sub[(sub["drug_name_normalized"] == drug) & (sub["adverse_event"] == event)].copy()
overall = sel["overall_ror"].iloc[0] if not sel.empty else float("nan")
st.metric("Overall ROR (all patients)", lib.fmt(overall))

for stype, title in [("sex", "By sex"), ("age", "By age band")]:
    part = sel[sel["subgroup_type"] == stype]
    if part.empty:
        continue
    st.subheader(title)
    chart = (
        alt.Chart(part).mark_bar().encode(
            x=alt.X("subgroup:N", title=None, sort=None),
            y=alt.Y("ror:Q", title="ROR"),
            color=alt.Color("subgroup:N", legend=None),
            tooltip=["subgroup", "stratum_reports", "ror", "ror_ci_lower", "ror_ci_upper", "prr"],
        ).properties(height=240)
    )
    rule = alt.Chart(part).mark_rule(strokeDash=[4, 4], color="#888").encode(
        y=alt.datum(float(overall)))
    st.altair_chart(chart + rule, use_container_width=True)
    show = part[["subgroup", "stratum_reports", "stratum_population", "ror", "ror_ci_lower", "ror_ci_upper", "prr"]].copy()
    for c in ["ror", "ror_ci_lower", "ror_ci_upper", "prr"]:
        show[c] = show[c].map(lib.fmt)
    st.dataframe(show.rename(columns={
        "subgroup": "Subgroup", "stratum_reports": "Reports (a)", "stratum_population": "Stratum N",
        "ror": "ROR", "ror_ci_lower": "CI low", "ror_ci_upper": "CI high", "prr": "PRR"}),
        hide_index=True, width="stretch")
st.caption("Dashed line = overall ROR. Bars above it indicate the signal is concentrated in that subgroup.")
