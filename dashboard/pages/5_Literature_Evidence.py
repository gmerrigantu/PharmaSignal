"""Literature Evidence — PubMed support for a selected signal (§11, §12.2)."""
from __future__ import annotations

import altair as alt
import pathlib as _pl, sys as _sys
_d = _pl.Path(__file__).resolve().parent
_sys.path.insert(0, str(_d if (_d / "lib.py").exists() else _d.parent))
import lib
import streamlit as st

lib.page_setup("Literature Evidence", "📚")
st.title("📚 Literature Evidence")
lib.source_badge()
st.info(
    "PubMed output is **literature-retrieval support**, not proof of causality or "
    "clinical consensus. Relevance scoring is keyword/title based and transparent."
)
lib.caveat()

if not lib.has("pubmed_evidence"):
    st.warning("No PubMed evidence table found. Run `make pubmed` (needs network) or `make demo`.")
    st.stop()

evidence = lib.gold("pubmed_evidence")
summary = lib.gold("pubmed_support_summary") if lib.has("pubmed_support_summary") else None

pairs = (evidence[["drug_name_normalized", "adverse_event"]].drop_duplicates()
         .assign(label=lambda d: d["drug_name_normalized"] + " — " + d["adverse_event"]))
label = st.selectbox("Drug-event signal", pairs["label"].tolist())
drug, event = pairs[pairs["label"] == label][["drug_name_normalized", "adverse_event"]].iloc[0]

sel = evidence[(evidence["drug_name_normalized"] == drug) & (evidence["adverse_event"] == event)].copy()

if summary is not None:
    s = summary[(summary["drug_name_normalized"] == drug) & (summary["adverse_event"] == event)]
    if not s.empty:
        row = s.iloc[0]
        c1, c2, c3 = st.columns(3)
        c1.metric("Articles retrieved", int(row["literature_support_count"]))
        c2.metric("Support score", f"{row['literature_support_score']:.2f}")
        c3.metric("Support level", row["support_level"])

st.subheader("Publication timeline")
if "publication_year" in sel and sel["publication_year"].notna().any():
    by_year = sel.dropna(subset=["publication_year"]).groupby("publication_year").size().reset_index(name="articles")
    chart = alt.Chart(by_year).mark_bar().encode(
        x=alt.X("publication_year:O", title="Year"),
        y=alt.Y("articles:Q", title="Articles"),
    ).properties(height=240)
    st.altair_chart(chart, use_container_width=True)

st.subheader("Top articles by relevance")
for _, a in sel.sort_values("relevance_score", ascending=False).head(15).iterrows():
    with st.container(border=True):
        st.markdown(f"**[{a['title']}]({a['url']})**")
        st.caption(
            f"{a['journal']} · {int(a['publication_year']) if a['publication_year'] == a['publication_year'] else '—'} · "
            f"PMID {a['pmid']} · relevance {a['relevance_score']:.2f}"
        )
        st.write(a["evidence_snippet"])
