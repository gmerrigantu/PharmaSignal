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

# The dashboard/API summary ships signal_scores as one JSON payload, which must stay
# under the Lambda/API-Gateway response limit (~6 MB). So the *served* mart is capped to
# the strongest top-N signals; the full matrix lives in signal_scores_all (Athena).
DASHBOARD_MART_LIMIT = int(os.getenv("PHARMASIGNAL_DASHBOARD_LIMIT", "5000"))

from ..config import SignalThresholds
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


def served_mart(scores_all: pd.DataFrame, *, eb05_floor: float = 2.0,
                limit: int | None = DASHBOARD_MART_LIMIT) -> pd.DataFrame:
    """The small, dashboard-served slice: meets the min-count floor OR a robust EB05.

    Capped to the top ``limit`` rows by EB05 so the single-payload ``/dashboard/summary``
    response stays under the serving limit. The complete matrix is written separately as
    ``signal_scores_all`` (for Athena / paginated drill-down). ``limit=None`` disables the
    cap (e.g. when the full served set is wanted on disk).
    """
    if scores_all.empty:
        return scores_all.copy()
    mask = (~scores_all["min_count_flag"]) | (scores_all["eb05"] >= eb05_floor)
    served = scores_all[mask]
    if limit is not None and len(served) > limit:
        served = served.sort_values("eb05", ascending=False).head(limit)
    return served.reset_index(drop=True)


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
