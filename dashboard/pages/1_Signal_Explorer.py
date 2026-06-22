"""Signal Explorer — core search/analysis page for drug-event pairs (§12.2)."""
from __future__ import annotations

import altair as alt
import pathlib as _pl, sys as _sys
_d = _pl.Path(__file__).resolve().parent
_sys.path.insert(0, str(_d if (_d / "lib.py").exists() else _d.parent))
import lib
import streamlit as st

lib.page_setup("Signal Explorer", "🔎")
st.title("🔎 Signal Explorer")
lib.source_badge()
lib.caveat()

scores = lib.gold("signal_scores")
th = lib.thresholds()

# Join the labeled-vs-novel flag (openFDA Drug Label API) if available.
if lib.has("drug_label_flags"):
    flags = lib.gold("drug_label_flags")[
        ["drug_name_normalized", "adverse_event", "label_status", "label_section"]]
    scores = scores.merge(flags, on=["drug_name_normalized", "adverse_event"], how="left")
    scores["label_status"] = scores["label_status"].fillna("unknown")
else:
    scores = scores.assign(label_status="unknown", label_section=None)

with st.sidebar:
    st.header("Filters")
    classes = ["(all)"] + sorted(scores["drug_class"].dropna().unique().tolist())
    sel_class = st.selectbox("Drug class", classes)
    drugs = sorted(scores["drug_name_normalized"].unique().tolist())
    sel_drugs = st.multiselect("Drugs", drugs)
    event_query = st.text_input("Adverse event contains", "")
    min_reports = st.slider("Minimum report count (a)", 0, 200, int(th.minimum_reports), 1)
    only_flagged = st.checkbox("Only disproportionality-flagged signals", value=False)
    label_filter = st.radio(
        "Label status",
        ["All", "Novel (not in label)", "Labeled", "Unknown"],
        help="Whether the adverse event already appears in the drug's official openFDA "
             "labeling. 'Novel' + disproportionate is the most interesting case — but a "
             "text-match heuristic, not a regulatory claim.",
    )
    # EBGM/EB05 are present only in the whole-database (bulk) build; offer them to
    # rank by when available, since EB05 is the regulatory-grade signalling metric.
    has_ebgm = "ebgm" in scores.columns and "eb05" in scores.columns
    sort_options = ["ror", "prr", "a_drug_event", "seriousness_rate", "chi_square",
                    "bayesian_shrunken_score"]
    if has_ebgm:
        sort_options = ["eb05", "ebgm"] + sort_options
    sort_by = st.selectbox(
        "Sort by",
        sort_options,
        format_func=lambda c: {
            "eb05": "EB05 (EBGM lower bound)", "ebgm": "EBGM",
            "ror": "ROR", "prr": "PRR", "a_drug_event": "Report count",
            "seriousness_rate": "Seriousness rate", "chi_square": "Chi-square",
            "bayesian_shrunken_score": "Shrinkage score",
        }[c],
    )

df = scores.copy()
if sel_class != "(all)":
    df = df[df["drug_class"] == sel_class]
if sel_drugs:
    df = df[df["drug_name_normalized"].isin(sel_drugs)]
if event_query:
    df = df[df["adverse_event"].str.contains(event_query.upper(), na=False)]
df = df[df["a_drug_event"] >= min_reports]
if only_flagged:
    df = df[df["disproportionality_flag"]]
_label_map = {"Novel (not in label)": "novel", "Labeled": "labeled", "Unknown": "unknown"}
if label_filter in _label_map:
    df = df[df["label_status"] == _label_map[label_filter]]

novel_flagged = int(((df["label_status"] == "novel") & df["disproportionality_flag"]).sum())
st.caption(
    f"{len(df):,} drug-event pairs match the current filters · "
    f"**{novel_flagged}** are novel *and* disproportionality-flagged."
)

show = df.sort_values(sort_by, ascending=False).copy()
for col in ["ror", "ror_ci_lower", "ror_ci_upper", "prr", "chi_square",
            "bayesian_shrunken_score", "ebgm", "eb05"]:
    if col in show.columns:
        show[col] = show[col].map(lib.fmt)
if "seriousness_rate" in show.columns:
    show["seriousness_rate"] = (df.sort_values(sort_by, ascending=False)["seriousness_rate"] * 100).map(lambda v: f"{v:.0f}%")
_badge = {"novel": "🆕 novel", "labeled": "📋 labeled", "unknown": "❓ unknown"}
show["label_status"] = show["label_status"].map(lambda s: _badge.get(s, s))
cols = {
    "drug_name_normalized": "Drug", "adverse_event": "Adverse event", "drug_class": "Class",
    "a_drug_event": "Reports (a)",
    "ebgm": "EBGM", "eb05": "EB05",
    "ror": "ROR", "ror_ci_lower": "ROR CI low",
    "ror_ci_upper": "ROR CI high", "prr": "PRR", "chi_square": "χ²",
    "seriousness_rate": "Serious", "label_status": "Label", "bayesian_shrunken_score": "Shrink",
    "disproportionality_flag": "Flag",
}
# Only show columns that exist in this build (API-mode lacks EBGM/seriousness).
cols = {k: v for k, v in cols.items() if k in show.columns}
st.dataframe(show[list(cols)].rename(columns=cols), hide_index=True, width="stretch", height=460)
st.caption(
    "**Label** = whether the event already appears in the drug's official openFDA labeling "
    "(🆕 novel = not found, 📋 labeled = found, ❓ unknown = no label retrieved). "
    "Novel + disproportionate signals warrant the most attention — but this is a "
    "text-matching heuristic, not a regulatory finding."
)

# Confidence-interval plot for the top pairs (shows statistical uncertainty, §12.3).
st.subheader("ROR with 95% confidence intervals")
ci_df = df.sort_values("ror", ascending=False).head(20).copy()
ci_df["label"] = ci_df["drug_name_normalized"] + " — " + ci_df["adverse_event"]
base = alt.Chart(ci_df).encode(y=alt.Y("label:N", sort="-x", title=None))
chart = (
    base.mark_rule().encode(x=alt.X("ror_ci_lower:Q", title="ROR (log scale)", scale=alt.Scale(type="log")),
                            x2="ror_ci_upper:Q")
    + base.mark_point(filled=True, size=70, color="#2563eb").encode(x="ror:Q")
)
ref = alt.Chart(ci_df).mark_rule(strokeDash=[4, 4], color="#888").encode(x=alt.datum(1.0))
st.altair_chart((chart + ref).properties(height=460), use_container_width=True)
st.caption("Dashed line = ROR of 1 (no disproportionality). Wide intervals indicate low-count, unstable signals.")
