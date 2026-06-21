"""Methodology & Caveats — statistical methods and responsible-use limits (§12.2)."""
from __future__ import annotations

import pathlib as _pl, sys as _sys
_d = _pl.Path(__file__).resolve().parent
_sys.path.insert(0, str(_d if (_d / "lib.py").exists() else _d.parent))
import lib
import streamlit as st

lib.page_setup("Methodology", "📐")
st.title("📐 Methodology & Caveats")
lib.caveat()

th = lib.thresholds()

st.header("Disproportionality")
st.markdown(
    "For each drug-event pair we build a 2×2 reporting contingency table over the "
    "selected window:"
)
st.markdown(
    "| | Event of interest | Other events |\n"
    "|---|---|---|\n"
    "| **Drug of interest** | a | b |\n"
    "| **Other drugs** | c | d |"
)
st.latex(r"\mathrm{ROR} = \frac{a/b}{c/d} \qquad \mathrm{PRR} = \frac{a/(a+b)}{c/(c+d)}")
st.latex(r"\mathrm{SE}(\log \mathrm{ROR}) = \sqrt{\tfrac1a+\tfrac1b+\tfrac1c+\tfrac1d}, \quad "
         r"95\%\,\mathrm{CI} = e^{\log \mathrm{ROR} \pm 1.96\,\mathrm{SE}}")
st.markdown(
    "If any cell is zero we add a Haldane–Anscombe 0.5 continuity correction to all "
    "four cells before computing ROR/PRR."
)

st.header("Shrinkage (simplified empirical Bayes)")
st.latex(r"E[a] = \frac{(a+b)(a+c)}{N}, \quad \mathrm{OE} = a / E[a]")
st.latex(r"\text{score} = w \cdot \log(\mathrm{OE}), \quad w = \frac{a}{a + 0.5}")
st.markdown(
    "This down-weights unstable low-count pairs. It is a **prioritization** metric, "
    "deliberately not labeled EBGM/MGPS to avoid implying regulatory equivalence."
)

st.header("Trend / anomaly")
st.markdown(
    "Per pair we compute quarterly counts and compare the current quarter to a "
    f"trailing baseline of **{th.trend_baseline_quarters} quarters**: percent change, "
    "z-score, EWMA, and a Poisson tail anomaly score."
)

st.header("Composite priority score")
st.latex(
    r"\text{priority} = 0.30\,D + 0.25\,T + 0.20\,S + 0.15\,L + 0.10\,P"
)
st.markdown(
    "where D = disproportionality, T = trend anomaly, S = seriousness, L = literature "
    "support, P = population context — each normalized to [0, 1]. Components are always "
    "shown; the composite never hides them."
)
st.markdown(
    f"**Signal flag rule (configurable):** a ≥ {th.minimum_reports}, "
    f"ROR 95% lower CI > {th.ror_lower_ci_threshold}, PRR ≥ {th.prr_threshold}, "
    f"χ² ≥ {th.chi_square_threshold}. **Priority levels:** High ≥ {th.high_priority_score}, "
    f"Moderate ≥ {th.moderate_priority_score}."
)

st.header("Limitations")
st.markdown(
    "- FAERS is a spontaneous reporting system: underreporting, duplicates, reporting "
    "bias, stimulated reporting, and **no denominator**.\n"
    "- Disproportionality is **hypothesis-generating**, not causal or incidence.\n"
    "- NHANES is cross-sectional population context, requires survey weights, and is "
    "**not linked** to FAERS at the person level.\n"
    "- PubMed co-occurrence is retrieval support, not clinical consensus."
)

st.header("Sources")
st.markdown(
    "- openFDA Drug Adverse Event API — https://open.fda.gov/apis/drug/event/\n"
    "- FDA FAERS Quarterly Data Extract Files — https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html\n"
    "- NHANES Data Search — https://wwwn.cdc.gov/nchs/nhanes/search/datapage.aspx\n"
    "- NCBI E-utilities — https://www.ncbi.nlm.nih.gov/books/NBK25501/"
)
