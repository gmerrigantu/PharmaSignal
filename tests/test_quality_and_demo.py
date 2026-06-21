"""Data-contract / quality checks and demo-dataset shape tests (§14.1, §14.2)."""
import pandas as pd

from pharmasignal.quality import checks


def _good_scores() -> pd.DataFrame:
    return pd.DataFrame([
        {"drug_name_normalized": "semaglutide", "adverse_event": "NAUSEA",
         "scoring_window_start": "2021-01-01", "a_drug_event": 10, "ror": 5.0,
         "prr": 3.0, "min_count_flag": False},
        {"drug_name_normalized": "semaglutide", "adverse_event": "VOMITING",
         "scoring_window_start": "2021-01-01", "a_drug_event": 4, "ror": 2.0,
         "prr": 1.5, "min_count_flag": False},
    ])


def test_checks_pass_on_clean_table():
    results = checks.check_signal_scores(_good_scores())
    summary = checks.summarize(results)
    assert summary["failed_checks"] == 0


def test_checks_detect_duplicates():
    df = pd.concat([_good_scores(), _good_scores().iloc[[0]]], ignore_index=True)
    results = checks.check_signal_scores(df)
    uniqueness = next(r for r in results if r.check == "pair_uniqueness")
    assert uniqueness.status == "fail"


def test_checks_detect_empty():
    results = checks.check_signal_scores(pd.DataFrame(columns=_good_scores().columns))
    assert any(r.status == "fail" for r in results)


def test_gold_signal_scores_contract_if_present():
    """Data-contract test — runs only when a gold signal_scores table exists
    (live pipeline or demo). Skips cleanly when neither is present."""
    import pytest

    from pharmasignal.serving.lakehouse import read_gold

    try:
        scores = read_gold("signal_scores")
    except FileNotFoundError:
        pytest.skip("no gold signal_scores table present (run the pipeline first)")
    required = {"drug_name_normalized", "adverse_event", "a_drug_event",
                "ror", "prr", "ror_ci_lower", "ror_ci_upper",
                "disproportionality_flag"}
    assert required.issubset(scores.columns)
    assert len(scores) > 0
    # ROR CI must bracket the point estimate for every row.
    assert (scores["ror_ci_lower"] <= scores["ror"]).all()
    assert (scores["ror"] <= scores["ror_ci_upper"]).all()
