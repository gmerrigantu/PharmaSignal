# LEGACY PROTOTYPE — the original single-file openFDA explorer.
# Superseded by the full PharmaSignal platform (see README.md): the modeling logic now
# lives in src/pharmasignal/modeling/signal_scores.py and the multi-page app in
# dashboard/. Kept for reference / quick live-API exploration:  streamlit run request.py
import math
import os
from typing import Iterable

import pandas as pd
import requests
import streamlit as st


BASE_URL = "https://api.fda.gov/drug/event.json"
DEFAULT_API_KEY = os.getenv("OPENFDA_API_KEY", "")
DRUG_FIELD = "patient.drug.medicinalproduct.exact"
EVENT_FIELD = "patient.reaction.reactionmeddrapt.exact"
MAX_COUNT_LIMIT = 1000


def split_terms(raw_terms: str) -> list[str]:
    terms: list[str] = []
    for chunk in raw_terms.replace("\n", ",").split(","):
        term = chunk.strip()
        if term:
            terms.append(term.upper())
    return list(dict.fromkeys(terms))


def quote_term(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def exact_clause(field: str, term: str) -> str:
    return f"{field}:{quote_term(term)}"


def and_query(clauses: Iterable[str]) -> str:
    return " AND ".join(f"({clause})" for clause in clauses)


@st.cache_data(ttl=60 * 30, show_spinner=False)
def openfda_get(params: dict) -> dict:
    clean_params = {key: value for key, value in params.items() if value not in ("", None)}
    response = requests.get(BASE_URL, params=clean_params, timeout=30)
    if response.status_code == 404:
        return {"meta": {"results": {"total": 0}}, "results": []}
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=60 * 30, show_spinner=False)
def total_reports(api_key: str) -> int:
    data = openfda_get({"limit": 1, "api_key": api_key})
    return int(data.get("meta", {}).get("results", {}).get("total", 0))


@st.cache_data(ttl=60 * 30, show_spinner=False)
def report_count(search: str, api_key: str) -> int:
    data = openfda_get({"search": search, "limit": 1, "api_key": api_key})
    return int(data.get("meta", {}).get("results", {}).get("total", 0))


@st.cache_data(ttl=60 * 30, show_spinner=False)
def count_terms(search: str, count_field: str, limit: int, api_key: str) -> pd.DataFrame:
    data = openfda_get(
        {"search": search, "count": count_field, "limit": limit, "api_key": api_key}
    )
    rows = data.get("results", [])
    if not rows:
        return pd.DataFrame(columns=["term", "count"])
    return pd.DataFrame(rows).rename(columns={"term": "term", "count": "count"})


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return float("nan")
    return numerator / denominator


def pair_statistics(drug: str, event: str, api_key: str) -> dict:
    drug_query = exact_clause(DRUG_FIELD, drug)
    event_query = exact_clause(EVENT_FIELD, event)
    pair_query = and_query([drug_query, event_query])

    a = report_count(pair_query, api_key)
    drug_total = report_count(drug_query, api_key)
    event_total = report_count(event_query, api_key)
    all_total = total_reports(api_key)

    b = max(drug_total - a, 0)
    c = max(event_total - a, 0)
    d = max(all_total - a - b - c, 0)

    corrected = any(cell == 0 for cell in (a, b, c, d))
    ca, cb, cc, cd = (a, b, c, d)
    if corrected:
        ca, cb, cc, cd = (a + 0.5, b + 0.5, c + 0.5, d + 0.5)

    ror = safe_ratio(ca * cd, cb * cc)
    prr = safe_ratio(ca / (ca + cb), cc / (cc + cd))
    pair_share = safe_ratio(a, drug_total)

    return {
        "drug": drug,
        "adverse_event": event,
        "both_reports": a,
        "drug_reports": drug_total,
        "event_reports": event_total,
        "other_reports": d,
        "ROR": ror,
        "PRR": prr,
        "event_share_for_drug": pair_share,
        "continuity_correction": corrected,
    }


def format_stat(value: float) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    if value >= 100:
        return f"{value:,.0f}"
    if value >= 10:
        return f"{value:,.1f}"
    return f"{value:,.2f}"


def render_table(df: pd.DataFrame, height: int = 420) -> None:
    st.dataframe(df, width="stretch", hide_index=True, height=height)


def remove_tracked_terms(df: pd.DataFrame, terms: list[str]) -> pd.DataFrame:
    if df.empty:
        return df
    tracked_terms = {term.upper() for term in terms}
    return df[~df["term"].str.upper().isin(tracked_terms)].reset_index(drop=True)


st.set_page_config(page_title="FDA Adverse Event Tracker", page_icon="FDA", layout="wide")

st.title("FDA Adverse Event Tracker")
st.caption(
    "Explore openFDA FAERS reports by drug and MedDRA preferred term. "
    "Counts are report counts, not confirmed incidence rates."
)

with st.sidebar:
    st.header("Track")
    drug_input = st.text_area(
        "Drug names",
        value="SEMAGLUTIDE",
        help=(
            "Enter one or more medicinal product names separated by commas or new lines. "
            "Leave blank to fetch drugs associated with the adverse events."
        ),
    )
    event_input = st.text_area(
        "Adverse events",
        value="NAUSEA, VOMITING",
        help=(
            "Enter one or more reaction terms separated by commas or new lines. "
            "Leave blank to fetch events and co-reported drugs associated with the drugs."
        ),
    )
    result_limit = st.slider(
        "Associated result limit",
        min_value=25,
        max_value=MAX_COUNT_LIMIT,
        value=100,
        step=25,
        help="openFDA count endpoints return ranked terms; this requests up to 1,000 terms.",
    )
    api_key = st.text_input(
        "openFDA API key",
        value=DEFAULT_API_KEY,
        type="password",
        help="Uses OPENFDA_API_KEY when set. openFDA also allows limited unauthenticated requests.",
    )
    st.button("Update dashboard", type="primary", width="stretch")

drugs = split_terms(drug_input)
events = split_terms(event_input)

if not drugs and not events:
    st.info("Add at least one drug or adverse event in the sidebar.")
    st.stop()

try:
    with st.spinner("Loading openFDA data..."):
        all_report_count = total_reports(api_key)
        pair_rows = [
            pair_statistics(drug, event, api_key) for drug in drugs for event in events
        ]
except requests.HTTPError as exc:
    st.error(f"openFDA request failed: {exc}")
    st.stop()
except requests.RequestException as exc:
    st.error(f"Could not reach openFDA: {exc}")
    st.stop()

pair_df = pd.DataFrame(pair_rows)

metric_cols = st.columns(4)
metric_cols[0].metric("Tracked drugs", len(drugs))
metric_cols[1].metric("Tracked events", len(events))
metric_cols[2].metric("Drug-event pairs", len(pair_rows))
metric_cols[3].metric("FAERS reports indexed", f"{all_report_count:,}")

if events and not drugs:
    st.info("Drug names is blank, so the Associated Drugs tab lists drugs for each entered adverse event.")
elif drugs and not events:
    st.info(
        "Adverse events is blank, so Associated Events lists events for each drug and "
        "Associated Drugs lists co-reported drugs."
    )

tab_pairs, tab_drugs, tab_events, tab_method = st.tabs(
    ["Pair Statistics", "Associated Events", "Associated Drugs", "Method"]
)

with tab_pairs:
    st.subheader("Drug-event disproportionality")
    if pair_df.empty:
        st.info("Add both a drug and an adverse event to calculate pair statistics.")
    else:
        display_df = pair_df.copy()
        display_df["event_share_for_drug"] = display_df["event_share_for_drug"].map(
            lambda value: f"{value:.2%}" if pd.notna(value) else "n/a"
        )
        display_df["ROR"] = display_df["ROR"].map(format_stat)
        display_df["PRR"] = display_df["PRR"].map(format_stat)
        display_df = display_df.rename(
            columns={
                "drug": "Drug",
                "adverse_event": "Adverse event",
                "both_reports": "Drug + event reports",
                "drug_reports": "Drug reports",
                "event_reports": "Event reports",
                "other_reports": "Other reports",
                "event_share_for_drug": "Share of drug reports",
                "continuity_correction": "0.5 correction used",
            }
        )
        render_table(display_df)

        chart_df = pair_df.sort_values("ROR", ascending=False).head(25)
        if not chart_df.empty:
            st.bar_chart(
                chart_df,
                x="adverse_event",
                y="ROR",
                color="drug",
                width="stretch",
            )

with tab_drugs:
    st.subheader("Events associated with tracked drugs")
    if not drugs:
        st.info("Add drug names in the sidebar to see associated adverse events.")
    for drug in drugs:
        st.markdown(f"**{drug}**")
        event_df = count_terms(
            exact_clause(DRUG_FIELD, drug), EVENT_FIELD, result_limit, api_key
        )
        if event_df.empty:
            st.warning(f"No openFDA reports found for {drug}.")
            continue
        event_df = event_df.rename(columns={"term": "Adverse event", "count": "Reports"})
        render_table(event_df, height=300)
        st.bar_chart(event_df.head(20), x="Adverse event", y="Reports", width="stretch")

with tab_events:
    st.subheader("Drugs associated with tracked events")
    if not events and not drugs:
        st.info("Add adverse event terms in the sidebar to see associated drugs.")
    elif drugs and not events:
        st.caption("No adverse events were entered. Showing co-reported drugs for each tracked drug.")
    for event in events:
        st.markdown(f"**{event}**")
        drug_df = count_terms(
            exact_clause(EVENT_FIELD, event), DRUG_FIELD, result_limit, api_key
        )
        if drug_df.empty:
            st.warning(f"No openFDA reports found for {event}.")
            continue
        drug_df = drug_df.rename(columns={"term": "Drug", "count": "Reports"})
        render_table(drug_df, height=300)
        st.bar_chart(drug_df.head(20), x="Drug", y="Reports", width="stretch")
    if drugs and not events:
        for drug in drugs:
            st.markdown(f"**{drug}**")
            co_drug_df = count_terms(
                exact_clause(DRUG_FIELD, drug), DRUG_FIELD, result_limit, api_key
            )
            co_drug_df = remove_tracked_terms(co_drug_df, [drug])
            if co_drug_df.empty:
                st.warning(f"No co-reported drugs found for {drug}.")
                continue
            co_drug_df = co_drug_df.rename(columns={"term": "Drug", "count": "Reports"})
            render_table(co_drug_df, height=300)
            st.bar_chart(co_drug_df.head(20), x="Drug", y="Reports", width="stretch")

with tab_method:
    st.subheader("ROR and PRR")
    st.write(
        "The dashboard builds a 2x2 report table for each drug-event pair: "
        "`a` is reports containing both the drug and event, `b` is the drug without "
        "the event, `c` is the event without the drug, and `d` is all other reports."
    )
    st.code("ROR = (a / b) / (c / d)\nPRR = (a / (a + b)) / (c / (c + d))")
    st.write(
        "If any cell is zero, 0.5 is added to all four cells before calculating ROR "
        "and PRR. FAERS data supports signal detection; it does not prove causality "
        "or provide population incidence."
    )
