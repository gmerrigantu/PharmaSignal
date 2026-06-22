"""Shared signal-scoring assembly used by both the DuckDB and Spark gold builds.

Both ``build_gold_bulk`` (DuckDB, incremental / local) and the PySpark backfill job
(``spark/jobs/build_gold_spark.py``, whole-history) reduce silver FAERS to the same
two intermediate shapes:

  * a **pair-counts** table — one row per co-occurring (drug, event) with the observed
    count ``a`` plus the drug and event marginals — and the grand total ``n_cases``;
  * a **quarterly trend** table — distinct-case counts per (drug, event, quarter).

Keeping the actual statistics (2x2 arithmetic, vectorized ROR/PRR/chi-square, EBGM/MGPS,
trend, priority) in one place here means the two execution engines can never drift in
how a signal is scored — they only differ in how the counts are produced.
"""
from __future__ import annotations

import os

import pandas as pd

from ..config import SignalThresholds

# Rows in the precomputed scatter sample served in /dashboard/summary. The full matrix is
# unbounded and paged via /signals; this tiny top-by-ROR slice lets the dashboard's scatter
# render and keeps the summary's hot path free of a full-matrix sort (fast cold starts).
SCATTER_SAMPLE_ROWS = int(os.getenv("PHARMASIGNAL_SCATTER_SAMPLE", "3000"))
from ..modeling import ebgm as eb
from ..modeling import signal_scores as ss

# Required columns of the pair-counts input (engine-agnostic contract).
PAIR_COLUMNS = ("drug_name_normalized", "drug_class", "adverse_event",
                "a", "drug_total", "event_total")


def score_pairs(pairs: pd.DataFrame, n_cases: int,
                thresholds: SignalThresholds) -> pd.DataFrame:
    """Score the full co-occurring drug-event matrix from pair counts + grand total.

    ``pairs`` must contain :data:`PAIR_COLUMNS`. Returns the full scored matrix
    (every co-occurring pair), with EBGM fit once over the whole table.
    """
    missing = set(PAIR_COLUMNS) - set(pairs.columns)
    if missing:
        raise ValueError(f"pairs missing columns: {sorted(missing)}")
    if pairs.empty:
        return pairs.copy()

    a = pairs["a"].astype(float)
    b = (pairs["drug_total"] - a).clip(lower=0)
    c = (pairs["event_total"] - a).clip(lower=0)
    d = (n_cases - a - b - c).clip(lower=0)

    disp = ss.disproportionality_frame(a.values, b.values, c.values, d.values)
    ebgm = eb.ebgm_scores(a.values, disp["expected_count"])
    f = ebgm.fit
    print(f"[scoring] MGPS fit: a1={f.a1:.3g} b1={f.b1:.3g} a2={f.a2:.3g} "
          f"b2={f.b2:.3g} pi={f.pi:.3g} (over {f.n_pairs:,} pairs)", flush=True)

    out = pairs[["drug_name_normalized", "drug_class", "adverse_event"]].reset_index(drop=True).copy()
    out["a_drug_event"] = a.astype(int).values
    out["b_drug_other_events"] = b.astype(int).values
    out["c_other_drugs_event"] = c.astype(int).values
    out["d_other_drugs_other_events"] = d.astype(int).values
    out["prr"] = disp["prr"]
    out["ror"] = disp["ror"]
    out["ror_ci_lower"] = disp["ror_ci_lower"]
    out["ror_ci_upper"] = disp["ror_ci_upper"]
    out["chi_square"] = disp["chi_square"]
    out["expected_count"] = disp["expected_count"]
    out["bayesian_shrunken_score"] = disp["bayesian_shrunken_score"]
    out["ebgm"] = ebgm.ebgm
    out["eb05"] = ebgm.eb05
    out["eb95"] = ebgm.eb95
    # Seriousness from the FAERS OUTC table (serious_a = co-occurring cases with a
    # reported serious outcome). Computed by the caller's set-based join, so it scales
    # to the whole database. Absent (older builds / no OUTC) -> rate 0.
    if "serious_a" in pairs.columns:
        serious = pairs["serious_a"].astype(float).clip(lower=0).values
        out["serious_count"] = serious.astype(int)
        out["seriousness_rate"] = (serious / a.where(a > 0, 1).values).clip(0, 1)
    else:
        out["serious_count"] = 0
        out["seriousness_rate"] = 0.0
    out["min_count_flag"] = out["a_drug_event"] < thresholds.minimum_reports
    out["disproportionality_flag"] = (
        (out["a_drug_event"] >= thresholds.minimum_reports)
        & (out["ror_ci_lower"] > thresholds.ror_lower_ci_threshold)
        & (out["prr"] >= thresholds.prr_threshold)
        & (out["chi_square"] >= thresholds.chi_square_threshold)
    )
    out["continuity_correction"] = disp["continuity_correction"]
    return out


def scatter_sample(scores_all: pd.DataFrame, *, n: int = SCATTER_SAMPLE_ROWS) -> pd.DataFrame:
    """Top-``n`` pairs by ROR — the small slice the dashboard scatter renders.

    Precomputed at build time and written as ``signal_scores_sample`` so the serving API
    never sorts the full matrix on a request (which would blow the API-Gateway cold-start
    budget). The complete matrix remains fully reachable via paginated ``/signals``.
    """
    if scores_all.empty:
        return scores_all.copy()
    return scores_all.nlargest(n, "ror").reset_index(drop=True)


def summary_stats(scores_all: pd.DataFrame) -> pd.DataFrame:
    """One-row mart of dashboard totals over the full matrix (``signal_scores_stats``).

    Precomputed so ``/dashboard/summary`` reports true totals without scanning the full
    Parquet on each request (which is too slow over S3 under the API-Gateway cold-start
    budget). The complete matrix stays fully reachable via ``/signals``.
    """
    flagged = int(scores_all["disproportionality_flag"].sum()) if not scores_all.empty else 0
    return pd.DataFrame([{"signal_total": int(len(scores_all)), "flagged_total": flagged}])


def drug_facets(scores_all: pd.DataFrame) -> pd.DataFrame:
    """Distinct drugs with class + case count (``signal_drugs`` mart).

    Powers the full-scale drug picker / class filter without scanning the whole matrix on
    each request. ``report_count`` is the drug marginal (a + b), constant within a drug.
    """
    cols = ["drug_name_normalized", "drug_class", "report_count"]
    if scores_all.empty:
        return pd.DataFrame(columns=cols)
    df = scores_all[["drug_name_normalized", "drug_class", "a_drug_event",
                     "b_drug_other_events"]].copy()
    df["report_count"] = (df["a_drug_event"] + df["b_drug_other_events"]).astype(int)
    g = df.groupby("drug_name_normalized", as_index=False).agg(
        drug_class=("drug_class", "first"), report_count=("report_count", "max"))
    return g.sort_values("report_count", ascending=False).reset_index(drop=True)[cols]


def event_facets(scores_all: pd.DataFrame) -> pd.DataFrame:
    """Distinct adverse events with case count (``signal_events`` mart).

    ``report_count`` is the event marginal (a + c), constant within an event.
    """
    cols = ["adverse_event", "report_count"]
    if scores_all.empty:
        return pd.DataFrame(columns=cols)
    df = scores_all[["adverse_event", "a_drug_event", "c_other_drugs_event"]].copy()
    df["report_count"] = (df["a_drug_event"] + df["c_other_drugs_event"]).astype(int)
    g = df.groupby("adverse_event", as_index=False).agg(report_count=("report_count", "max"))
    return g.sort_values("report_count", ascending=False).reset_index(drop=True)[cols]


def emerging_signals(trend: pd.DataFrame, scores_df: pd.DataFrame,
                     thresholds: SignalThresholds, weights: dict[str, float],
                     *, top_k: int) -> pd.DataFrame:
    """Quarterly trend + composite priority for the strongest served pairs.

    ``trend`` has columns (drug_name_normalized, adverse_event, faers_quarter,
    report_count). Ranks ``scores_df`` by EB05 (or ROR) and computes trend metrics for
    the top ``top_k`` pairs.
    """
    if scores_df.empty or trend.empty:
        return pd.DataFrame()
    rank_col = "eb05" if "eb05" in scores_df else "ror"
    top = scores_df.sort_values(rank_col, ascending=False).head(top_k)

    quarters = sorted(q for q in trend["faers_quarter"].dropna().unique())
    if len(quarters) < 2:
        return pd.DataFrame()

    pivot = (
        trend.pivot_table(index=["drug_name_normalized", "adverse_event"],
                          columns="faers_quarter", values="report_count", fill_value=0)
        .reindex(columns=quarters, fill_value=0)
    )

    rows: list[dict] = []
    baseline_q = thresholds.trend_baseline_quarters
    for _, row in top.iterrows():
        key = (row["drug_name_normalized"], row["adverse_event"])
        if key not in pivot.index:
            continue
        series = [int(x) for x in pivot.loc[key].tolist()]
        current = series[-1]
        baseline = series[max(0, len(series) - 1 - baseline_q):-1]
        if not baseline:
            continue
        tr = ss.trend_metrics(current, baseline)
        seriousness = float(row.get("seriousness_rate", 0.0) or 0.0)
        priority = ss.priority_score(
            disproportionality_score=ss.normalize_disproportionality(row["bayesian_shrunken_score"]),
            trend_anomaly_score=ss.normalize_trend(tr.z_score),
            seriousness_score=seriousness,
            literature_support_score=0.0,
            population_context_score=0.0,
            weights=weights,
        )
        rows.append({
            "drug_name_normalized": key[0],
            "drug_class": row.get("drug_class"),
            "adverse_event": key[1],
            "current_quarter": quarters[-1],
            "current_count": current,
            "trailing_baseline_count": tr.trailing_baseline_mean,
            "percent_change": tr.percent_change,
            "anomaly_score": tr.z_score,
            "poisson_anomaly_score": tr.poisson_anomaly_score,
            "ebgm": row.get("ebgm"),
            "eb05": row.get("eb05"),
            "seriousness_rate": seriousness,
            "literature_support_count": 0,
            "nhanes_context_available": False,
            "priority_score": priority,
            "priority_level": ss.priority_level(
                priority, thresholds.high_priority_score, thresholds.moderate_priority_score),
        })
    return pd.DataFrame(rows)
