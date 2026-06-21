"""Generate a deterministic offline demo dataset (requirements §13.2, §15.2).

Produces the full set of gold tables with realistic-but-synthetic numbers so a
reviewer can run the dashboard with zero cloud credentials and zero network calls
(`make demo` then `make dashboard`). The same modeling functions used by the real
pipeline are used here, so the demo exercises the actual ROR/PRR/priority code.

Writes into ``sample_data/gold/`` (bundled in the repo) AND ``data/gold/``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from .. import config
from ..modeling import signal_scores as ss
from ..paths import SAMPLE_DATA_DIR

RNG = np.random.default_rng(42)  # deterministic

# Clinically plausible reaction terms for the metabolic / GLP-1 domain.
EVENTS = [
    "NAUSEA", "VOMITING", "DIARRHOEA", "CONSTIPATION", "ABDOMINAL PAIN",
    "PANCREATITIS", "PANCREATITIS ACUTE", "CHOLELITHIASIS", "GASTROOESOPHAGEAL REFLUX DISEASE",
    "DEHYDRATION", "DECREASED APPETITE", "WEIGHT DECREASED", "FATIGUE", "HEADACHE",
    "DIZZINESS", "INJECTION SITE REACTION", "HYPOGLYCAEMIA", "GASTROPARESIS",
    "ILEUS", "INTESTINAL OBSTRUCTION", "ACUTE KIDNEY INJURY", "MALAISE",
    "DYSGEUSIA", "ERUCTATION", "ABDOMINAL DISTENSION", "BLOOD GLUCOSE INCREASED",
    "SUICIDAL IDEATION", "DEPRESSION", "VISION BLURRED", "TACHYCARDIA",
]

# Events that should read as "emerging" / disproportionate for GLP-1 drugs.
ELEVATED = {"GASTROPARESIS", "ILEUS", "INTESTINAL OBSTRUCTION", "PANCREATITIS",
            "PANCREATITIS ACUTE", "CHOLELITHIASIS", "SUICIDAL IDEATION"}

WIN_START, WIN_END = date(2021, 1, 1), date(2025, 12, 31)
ALL_TOTAL = 16_000_000  # plausible order of magnitude for FAERS report universe
QUARTERS = [f"{y}Q{q}" for y in range(2021, 2026) for q in range(1, 5)]


def _synthesize():
    drugs = config.load_drug_domain()
    thresholds = config.load_thresholds()
    weights = config.load_priority_weights()

    count_rows, score_rows, emerging_rows, evidence_rows, support_rows = [], [], [], [], []

    for drug in drugs:
        # Bigger marketed drugs get more reports.
        base_drug_total = int(RNG.integers(2_000, 60_000))
        for term in EVENTS:
            elevated = term in ELEVATED and drug.drug_class in {
                "glp1_receptor_agonists", "dual_gip_glp1_agonists"}
            # `a` = reports with this drug+event
            share = RNG.uniform(0.02, 0.18) * (3.0 if elevated else 1.0)
            a = int(max(0, RNG.poisson(base_drug_total * share / 10)))
            if a == 0 and RNG.random() < 0.3:
                continue  # some pairs simply unreported
            event_total = int(a + RNG.integers(500, 40_000) * (0.4 if elevated else 1.0))
            cont = ss.Contingency.from_totals(a, base_drug_total, event_total, ALL_TOTAL)
            disp = ss.disproportionality(cont)
            seriousness_rate = float(np.clip(RNG.uniform(0.1, 0.5) + (0.25 if elevated else 0), 0, 1))
            serious_count = int(a * seriousness_rate)
            flag = ss.is_signal(
                cont, disp,
                minimum_reports=thresholds.minimum_reports,
                ror_lower_ci_threshold=thresholds.ror_lower_ci_threshold,
                prr_threshold=thresholds.prr_threshold,
                chi_square_threshold=thresholds.chi_square_threshold,
            )

            count_rows.append({
                "drug_name_normalized": drug.canonical_name, "drug_class": drug.drug_class,
                "adverse_event": term, "scoring_window_start": WIN_START,
                "scoring_window_end": WIN_END, "report_count": a,
                "serious_count": serious_count, "distinct_case_count": a,
            })
            score_rows.append({
                "drug_name_normalized": drug.canonical_name, "drug_class": drug.drug_class,
                "adverse_event": term, "scoring_window_start": WIN_START, "scoring_window_end": WIN_END,
                "a_drug_event": cont.a, "b_drug_other_events": cont.b,
                "c_other_drugs_event": cont.c, "d_other_drugs_other_events": cont.d,
                "prr": disp.prr, "ror": disp.ror, "ror_ci_lower": disp.ror_ci_lower,
                "ror_ci_upper": disp.ror_ci_upper, "chi_square": disp.chi_square,
                "expected_count": disp.expected_a, "bayesian_shrunken_score": disp.shrunken_log_score,
                "seriousness_rate": seriousness_rate, "min_count_flag": cont.a < thresholds.minimum_reports,
                "disproportionality_flag": flag, "continuity_correction": disp.continuity_correction,
            })

    scores_df = pd.DataFrame(score_rows)

    # Emerging signals + quarterly trend for the strongest pairs.
    top = scores_df.sort_values("ror", ascending=False).head(60)
    for _, row in top.iterrows():
        elevated = row["adverse_event"] in ELEVATED
        baseline_mean = RNG.uniform(5, 40)
        baseline = list(np.clip(RNG.normal(baseline_mean, baseline_mean * 0.2, 4), 0, None).astype(int))
        growth = RNG.uniform(1.5, 4.0) if elevated else RNG.uniform(0.7, 1.4)
        current = int(max(baseline) * growth)
        trend = ss.trend_metrics(current, baseline)
        lit_count = int(RNG.integers(0, 25)) if elevated else int(RNG.integers(0, 6))
        lit_score = float(np.clip(lit_count / 20 + (0.2 if elevated else 0), 0, 1))
        nhanes_ctx = row["drug_class"] in {"glp1_receptor_agonists", "biguanides",
                                           "dual_gip_glp1_agonists", "sglt2_inhibitors"}
        priority = ss.priority_score(
            disproportionality_score=ss.normalize_disproportionality(row["bayesian_shrunken_score"]),
            trend_anomaly_score=ss.normalize_trend(trend.z_score),
            seriousness_score=row["seriousness_rate"],
            literature_support_score=lit_score,
            population_context_score=0.6 if nhanes_ctx else 0.0,
            weights=weights,
        )
        emerging_rows.append({
            "drug_name_normalized": row["drug_name_normalized"], "drug_class": row["drug_class"],
            "adverse_event": row["adverse_event"], "current_quarter": QUARTERS[-1],
            "current_count": current, "trailing_baseline_count": trend.trailing_baseline_mean,
            "percent_change": trend.percent_change, "anomaly_score": trend.z_score,
            "poisson_anomaly_score": trend.poisson_anomaly_score, "seriousness_rate": row["seriousness_rate"],
            "literature_support_count": lit_count, "nhanes_context_available": nhanes_ctx,
            "priority_score": priority,
            "priority_level": ss.priority_level(priority, thresholds.high_priority_score,
                                                thresholds.moderate_priority_score),
        })

        # Synthetic PubMed evidence for elevated pairs.
        for i in range(min(lit_count, 8)):
            year = int(RNG.integers(2019, 2026))
            rel = float(np.clip(RNG.uniform(0.3, 1.0), 0, 1))
            evidence_rows.append({
                "drug_name_normalized": row["drug_name_normalized"], "adverse_event": row["adverse_event"],
                "pmid": str(int(RNG.integers(30_000_000, 39_999_999))),
                "title": f"{row['drug_name_normalized'].title()} and {row['adverse_event'].title()}: a pharmacovigilance analysis",
                "journal": RNG.choice(["Diabetes Care", "Drug Safety", "JAMA", "Lancet Diabetes Endocrinol"]),
                "publication_year": year, "relevance_score": round(rel, 3),
                "evidence_snippet": "Synthetic demo abstract snippet describing reported association (demo data).",
                "mentions_drug": True, "mentions_event": True, "adverse_context": True,
                "url": "https://pubmed.ncbi.nlm.nih.gov/",
            })
        support_rows.append({
            "drug_name_normalized": row["drug_name_normalized"], "adverse_event": row["adverse_event"],
            "literature_support_count": lit_count, "literature_support_score": round(lit_score, 3),
            "support_level": "Strong" if lit_score >= 0.55 else "Moderate" if lit_score >= 0.25 else "Weak" if lit_score > 0 else "None",
        })

    # Quarterly trend long table for trend-line charts.
    trend_rows = []
    for _, row in top.iterrows():
        elevated = row["adverse_event"] in ELEVATED
        level = RNG.uniform(5, 30)
        for i, q in enumerate(QUARTERS):
            drift = (1 + 0.12 * i) if elevated else 1.0
            trend_rows.append({
                "drug_name_normalized": row["drug_name_normalized"],
                "adverse_event": row["adverse_event"], "quarter": q,
                "report_count": int(max(0, RNG.normal(level * drift, level * 0.15))),
            })
    trend_df = pd.DataFrame(trend_rows)

    # NHANES population context (synthetic but plausible for GLP-1 / metabolic).
    nhanes_rows = []
    for drug in drugs:
        n = int(RNG.integers(5, 180))
        nhanes_rows.append({
            "survey_cycle": "2021-2023", "drug_class": drug.drug_class,
            "medication_name_normalized": drug.canonical_name,
            "weighted_prevalence": round(float(RNG.uniform(0.002, 0.06)), 4),
            "estimated_users": int(RNG.uniform(0.5e6, 12e6)),
            "unweighted_sample_count": n,
            "median_age": round(float(RNG.uniform(45, 65)), 1),
            "female_percent": round(float(RNG.uniform(45, 65)), 1),
            "bmi_ge_30_percent": round(float(RNG.uniform(55, 85)), 1),
            "diabetes_percent": round(float(RNG.uniform(40, 90)), 1),
            "hba1c_median": round(float(RNG.uniform(6.0, 8.5)), 1),
            "weight_variable_used": "WTMEC2YR",
            "small_n_flag": n < 30, "very_small_n_flag": n < 10,
        })

    # Pipeline health + data quality (demo run).
    health = pd.DataFrame([{
        "run_id": str(uuid.uuid4()), "run_timestamp": datetime.now(timezone.utc),
        "source": "demo_generator", "source_period": "2021-01-01..2025-12-31",
        "status": "success", "rows_raw": ALL_TOTAL,
        "rows_silver": int(scores_df["a_drug_event"].sum()), "rows_gold": len(scores_df),
        "failed_checks": 0, "warning_checks": 1, "duration_seconds": 0.5,
        "estimated_cost_usd": 0.0, "git_commit": "demo",
        "notes": "Deterministic synthetic demo dataset — NOT real FAERS data.",
    }])
    checks_df = pd.DataFrame([
        {"table": "gold_signal_scores", "check": "row_count", "category": "completeness",
         "status": "pass", "detail": f"{len(scores_df)} rows"},
        {"table": "gold_signal_scores", "check": "pair_uniqueness", "category": "uniqueness",
         "status": "pass", "detail": "0 duplicate pair rows"},
        {"table": "gold_nhanes_population_context", "check": "small_n", "category": "validity",
         "status": "warn", "detail": "some medication-level estimates have unweighted n < 30"},
    ])

    return {
        "drug_event_counts": pd.DataFrame(count_rows),
        "signal_scores": scores_df,
        "emerging_signals": pd.DataFrame(emerging_rows),
        "quarterly_trend": trend_df,
        "pubmed_evidence": pd.DataFrame(evidence_rows),
        "pubmed_support_summary": pd.DataFrame(support_rows),
        "nhanes_population_context": pd.DataFrame(nhanes_rows),
        "pipeline_health": health,
        "data_quality_checks": checks_df,
    }


def main() -> None:
    tables = _synthesize()
    out_dirs = [SAMPLE_DATA_DIR / "gold", config_data_gold()]
    for d in out_dirs:
        d.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        for d in out_dirs:
            df.to_parquet(d / f"{name}.parquet", index=False)
    total = sum(len(df) for df in tables.values())
    print(f"Demo dataset written: {len(tables)} tables, {total} rows -> sample_data/gold and data/gold")


def config_data_gold():
    from ..paths import GOLD_DIR
    return GOLD_DIR


if __name__ == "__main__":
    main()
