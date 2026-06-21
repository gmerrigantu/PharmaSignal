"""Data-quality checks producing machine-readable results (requirements §14).

Checks return a list of :class:`CheckResult`. The pipeline aggregates pass/warn/fail
counts into ``gold_pipeline_health`` so the dashboard's Pipeline Health page can
demonstrate operational trust.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class CheckResult:
    table: str
    check: str
    category: str  # completeness | validity | uniqueness | distribution
    status: str    # pass | warn | fail
    detail: str


def check_signal_scores(df: pd.DataFrame) -> list[CheckResult]:
    results: list[CheckResult] = []

    def add(check, category, status, detail):
        results.append(CheckResult("gold_signal_scores", check, category, status, detail))

    # Completeness
    required = {"drug_name_normalized", "adverse_event", "a_drug_event", "ror", "prr"}
    missing = required - set(df.columns)
    add("required_columns", "completeness", "pass" if not missing else "fail",
        "all present" if not missing else f"missing {sorted(missing)}")

    if df.empty:
        add("row_count", "completeness", "fail", "no rows")
        return results
    add("row_count", "completeness", "pass", f"{len(df)} rows")

    # Validity — ROR/PRR should be positive and finite where defined.
    bad_ror = df["ror"].le(0).sum() if "ror" in df else 0
    add("ror_positive", "validity", "pass" if bad_ror == 0 else "warn",
        f"{bad_ror} non-positive ROR values")

    # Uniqueness — one row per (drug, event, window).
    keys = [c for c in ["drug_name_normalized", "adverse_event", "scoring_window_start"] if c in df]
    dupes = df.duplicated(subset=keys).sum() if keys else 0
    add("pair_uniqueness", "uniqueness", "pass" if dupes == 0 else "fail",
        f"{dupes} duplicate pair rows")

    # Distribution — share of pairs hitting the min-count floor.
    if "min_count_flag" in df:
        share = float(df["min_count_flag"].mean())
        status = "pass" if share < 0.95 else "warn"
        add("min_count_distribution", "distribution", status,
            f"{share:.0%} of pairs below minimum report count")

    return results


def summarize(results: list[CheckResult]) -> dict[str, int]:
    return {
        "failed_checks": sum(r.status == "fail" for r in results),
        "warning_checks": sum(r.status == "warn" for r in results),
        "passed_checks": sum(r.status == "pass" for r in results),
    }
