"""End-to-end openFDA -> silver/gold pipeline (requirements §7.2, §9, Phase 1-3).

For the configured drug domain this:
  1. Pulls top reaction terms per drug from openFDA (each count IS the `a` cell).
  2. Builds the 2x2 contingency table per drug-event pair.
  3. Computes ROR/PRR/CI/chi-square/shrinkage + disproportionality flags.
  4. Computes quarterly trend/anomaly for the strongest pairs.
  5. Writes gold tables: drug_event_counts, signal_scores, emerging_signals,
     pipeline_health.

This is API-mode (MVP). Quarterly-file mode (faers_quarterly.py) is the production
path. Run: python -m pharmasignal.pipeline.build_gold
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import date, datetime, timezone

import pandas as pd

from .. import config
from ..ingestion import openfda
from ..modeling import signal_scores as ss
from ..paths import ensure_dirs
from ..quality import checks
from ..serving.lakehouse import write_gold


def _quarter_bounds(start: date, end: date) -> list[tuple[str, str, str]]:
    """List of (label, start_yyyymmdd, end_yyyymmdd) quarters in [start, end]."""
    out = []
    y, q = start.year, (start.month - 1) // 3 + 1
    while date(y, (q - 1) * 3 + 1, 1) <= end:
        q_start = date(y, (q - 1) * 3 + 1, 1)
        last_month = q * 3
        q_end_year, q_end_month = y, last_month
        # last day of quarter
        if q_end_month == 12:
            q_end = date(q_end_year, 12, 31)
        else:
            q_end = date(q_end_year, q_end_month + 1, 1) - pd.Timedelta(days=1)
            q_end = q_end.date() if hasattr(q_end, "date") else q_end
        out.append((f"{y}Q{q}", q_start.strftime("%Y%m%d"), q_end.strftime("%Y%m%d")))
        q += 1
        if q > 4:
            q, y = 1, y + 1
    return out


def _log(msg: str) -> None:
    """Progress line (flushed) so long runs show they're alive."""
    print(f"[build_gold] {msg}", flush=True)


def build(*, api_key: str = openfda.DEFAULT_API_KEY, use_cache: bool = True,
          trend_top_k: int | None = None, polite_delay: float | None = None) -> dict:
    # Speed knobs (env-overridable): with an API key openFDA's rate ceiling is high,
    # so a tiny delay is plenty. PHARMASIGNAL_POLITE_DELAY / _TREND_TOP_K override.
    if polite_delay is None:
        polite_delay = float(os.getenv("PHARMASIGNAL_POLITE_DELAY", "0.05"))
    if trend_top_k is None:
        trend_top_k = int(os.getenv("PHARMASIGNAL_TREND_TOP_K", "50"))
    ensure_dirs()
    faers_cfg = config.load_faers_config()
    thresholds = config.load_thresholds()
    weights = config.load_priority_weights()
    drugs = config.load_drug_domain()

    window = faers_cfg["window"]
    win_start = datetime.fromisoformat(window["start_date"]).date()
    win_end = datetime.fromisoformat(window["end_date"]).date()
    window_clause = openfda.date_range_clause(window["start_date"], window["end_date"])
    top_events = faers_cfg.get("top_events_per_drug", 100)

    _log(f"start: {len(drugs)} drugs, top_events={top_events}, trend_top_k={trend_top_k}, "
         f"polite_delay={polite_delay}s, root={os.getenv('PHARMASIGNAL_DATA_ROOT', 'local')}")
    all_total = openfda.count(window_clause, api_key=api_key, use_cache=use_cache)
    _log(f"universe total reports in window = {all_total:,}")

    count_rows: list[dict] = []
    score_rows: list[dict] = []

    # Cache event_total per event term to avoid repeated calls.
    event_total_cache: dict[str, int] = {}

    def event_total(term: str) -> int | None:
        if term not in event_total_cache:
            clause = openfda.and_query([openfda.event_clause(term), window_clause])
            try:
                event_total_cache[term] = openfda.count(clause, api_key=api_key, use_cache=use_cache)
            except openfda.OpenFDABadQuery:
                event_total_cache[term] = None  # unsupported term (e.g. apostrophes)
        return event_total_cache[term]

    for i, drug in enumerate(drugs, 1):
        # OR all known aliases/brands so the canonical drug captures all raw spellings.
        aliases = sorted({drug.canonical_name.upper(), *drug.aliases, *(b.upper() for b in drug.brands)})
        drug_or = " OR ".join(openfda.drug_clause(a) for a in aliases)
        drug_search = openfda.and_query([drug_or, window_clause])

        drug_total = openfda.count(drug_search, api_key=api_key, use_cache=use_cache)
        _log(f"drug {i}/{len(drugs)}: {drug.canonical_name} (reports={drug_total:,})")
        if drug_total == 0:
            continue
        events = openfda.count_field(drug_search, openfda.EVENT_FIELD, top_events,
                                     api_key=api_key, use_cache=use_cache)
        time.sleep(polite_delay)

        # Seriousness for ALL of this drug's events in ONE call (count of serious
        # reports grouped by reaction term), instead of one call per pair.
        serious_rows = openfda.count_field(
            openfda.and_query([drug_or, "serious:1", window_clause]),
            openfda.EVENT_FIELD, top_events, api_key=api_key, use_cache=use_cache)
        serious_by_term = {r["term"]: int(r["count"]) for r in serious_rows}
        time.sleep(polite_delay)

        for ev in events:
            term, a = ev["term"], int(ev["count"])
            evt_total = event_total(term)
            if evt_total is None:
                continue  # openFDA rejected this reaction term — skip it
            cont = ss.Contingency.from_totals(a, drug_total, evt_total, all_total)
            disp = ss.disproportionality(cont)

            serious_count = serious_by_term.get(term, 0)
            seriousness_rate = serious_count / a if a else 0.0

            flag = ss.is_signal(
                cont, disp,
                minimum_reports=thresholds.minimum_reports,
                ror_lower_ci_threshold=thresholds.ror_lower_ci_threshold,
                prr_threshold=thresholds.prr_threshold,
                chi_square_threshold=thresholds.chi_square_threshold,
            )

            count_rows.append({
                "drug_name_normalized": drug.canonical_name,
                "drug_class": drug.drug_class,
                "adverse_event": term,
                "scoring_window_start": win_start,
                "scoring_window_end": win_end,
                "report_count": a,
                "serious_count": serious_count,
                "distinct_case_count": a,  # API counts at report level
            })
            score_rows.append({
                "drug_name_normalized": drug.canonical_name,
                "drug_class": drug.drug_class,
                "adverse_event": term,
                "scoring_window_start": win_start,
                "scoring_window_end": win_end,
                "a_drug_event": cont.a,
                "b_drug_other_events": cont.b,
                "c_other_drugs_event": cont.c,
                "d_other_drugs_other_events": cont.d,
                "prr": disp.prr,
                "ror": disp.ror,
                "ror_ci_lower": disp.ror_ci_lower,
                "ror_ci_upper": disp.ror_ci_upper,
                "chi_square": disp.chi_square,
                "expected_count": disp.expected_a,
                "bayesian_shrunken_score": disp.shrunken_log_score,
                "seriousness_rate": seriousness_rate,
                "min_count_flag": cont.a < thresholds.minimum_reports,
                "disproportionality_flag": flag,
                "continuity_correction": disp.continuity_correction,
            })
            time.sleep(polite_delay)

    scores_df = pd.DataFrame(score_rows)
    counts_df = pd.DataFrame(count_rows)
    _log(f"scored {len(scores_df)} drug-event pairs; computing trend for top {trend_top_k}")

    # ------------------------------------------------------------------ #
    # Emerging signals: quarterly trend for the strongest pairs.
    # ------------------------------------------------------------------ #
    emerging_rows: list[dict] = []
    if not scores_df.empty:
        quarters = _quarter_bounds(win_start, win_end)
        top = scores_df.sort_values("ror", ascending=False).head(trend_top_k)
        for ti, (_, row) in enumerate(top.iterrows(), 1):
            if ti % 10 == 0:
                _log(f"  trend {ti}/{min(trend_top_k, len(top))}")
            drug_name, term = row["drug_name_normalized"], row["adverse_event"]
            drug = next(d for d in drugs if d.canonical_name == drug_name)
            aliases = sorted({drug.canonical_name.upper(), *drug.aliases, *(b.upper() for b in drug.brands)})
            drug_or = " OR ".join(openfda.drug_clause(a) for a in aliases)
            q_counts = []
            try:
                for _, qs, qe in quarters:
                    clause = openfda.and_query([drug_or, openfda.event_clause(term),
                                                openfda.date_range_clause(qs, qe)])
                    q_counts.append(openfda.count(clause, api_key=api_key, use_cache=use_cache))
                    time.sleep(polite_delay)
            except openfda.OpenFDABadQuery:
                continue  # skip pairs whose term openFDA can't query for trend
            if len(q_counts) < 2:
                continue
            current = q_counts[-1]
            baseline = q_counts[max(0, len(q_counts) - 1 - thresholds.trend_baseline_quarters):-1]
            trend = ss.trend_metrics(current, baseline)

            disp_norm = ss.normalize_disproportionality(row["bayesian_shrunken_score"])
            trend_norm = ss.normalize_trend(trend.z_score)
            priority = ss.priority_score(
                disproportionality_score=disp_norm,
                trend_anomaly_score=trend_norm,
                seriousness_score=row["seriousness_rate"],
                literature_support_score=0.0,    # filled by pubmed.build_evidence
                population_context_score=0.0,    # filled by nhanes join
                weights=weights,
            )
            emerging_rows.append({
                "drug_name_normalized": drug_name,
                "drug_class": row["drug_class"],
                "adverse_event": term,
                "current_quarter": quarters[-1][0],
                "current_count": current,
                "trailing_baseline_count": trend.trailing_baseline_mean,
                "percent_change": trend.percent_change,
                "anomaly_score": trend.z_score,
                "poisson_anomaly_score": trend.poisson_anomaly_score,
                "seriousness_rate": row["seriousness_rate"],
                "literature_support_count": 0,
                "nhanes_context_available": False,
                "priority_score": priority,
                "priority_level": ss.priority_level(
                    priority, thresholds.high_priority_score, thresholds.moderate_priority_score),
            })

    emerging_df = pd.DataFrame(emerging_rows)

    # Persist gold tables.
    _log(f"writing gold tables -> {os.getenv('PHARMASIGNAL_DATA_ROOT', 'local data/gold')}")
    write_gold(counts_df, "drug_event_counts")
    write_gold(scores_df, "signal_scores")
    write_gold(emerging_df, "emerging_signals")

    # Pipeline health.
    check_results = checks.check_signal_scores(scores_df)
    summary = checks.summarize(check_results)
    health = pd.DataFrame([{
        "run_id": str(uuid.uuid4()),
        "run_timestamp": datetime.now(timezone.utc),
        "source": "openfda_api",
        "source_period": f"{window['start_date']}..{window['end_date']}",
        "status": "success" if summary["failed_checks"] == 0 else "failed",
        "rows_raw": all_total,
        "rows_silver": int(counts_df["report_count"].sum()) if not counts_df.empty else 0,
        "rows_gold": len(scores_df),
        "failed_checks": summary["failed_checks"],
        "warning_checks": summary["warning_checks"],
        "duration_seconds": None,
        "estimated_cost_usd": 0.0,  # openFDA is free; see docs/cost_estimate.md
        "git_commit": None,
        "notes": "API-mode build for configured GLP-1 / metabolic drug domain.",
    }])
    write_gold(health, "pipeline_health")

    checks_df = pd.DataFrame([c.__dict__ for c in check_results])
    write_gold(checks_df, "data_quality_checks")

    return {
        "drugs": len(drugs),
        "pairs": len(scores_df),
        "emerging": len(emerging_df),
        "flagged": int(scores_df["disproportionality_flag"].sum()) if not scores_df.empty else 0,
    }


def main() -> None:
    started = time.time()
    summary = build()
    summary["elapsed_seconds"] = round(time.time() - started, 1)
    print(f"Pipeline complete: {summary}")


if __name__ == "__main__":
    main()
