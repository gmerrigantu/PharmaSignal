"""End-to-end test of the bulk SQL gold build over synthetic silver FAERS (WS2).

Covers the load-bearing Phase-0 behaviours: case-version dedup, DELETED-case
exclusion, the 2x2 marginal arithmetic, and gold table emission — all with no network.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _write(df: pd.DataFrame, root, table, quarter):
    year = quarter[:4]
    q = quarter[4:]  # e.g. "Q1"
    out = root / "silver" / "faers" / table / f"year={year}" / f"quarter={q}"
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "data.parquet", index=False)


def _make_silver(root):
    rng = np.random.default_rng(7)
    reports, drugs, reactions = [], [], []
    pid = 0

    def add_case(caseid, quarter, fda_date, drug, event, *, case_version=1):
        nonlocal pid
        pid += 1
        reports.append({
            "primaryid": str(pid), "caseid": str(caseid), "case_version": case_version,
            "fda_date": pd.Timestamp(fda_date), "faers_quarter": quarter,
        })
        drugs.append({"primaryid": str(pid), "drug_name_normalized": drug,
                      "drug_name_raw": drug, "drug_class": "test", "role_code": "PS"})
        reactions.append({"primaryid": str(pid), "reaction_term_normalized": event,
                          "reaction_term": event})

    caseid = 1
    # 120 background cases spread over both quarters to populate marginals.
    bg_drugs = ["DA", "DB", "DC", "DD"]
    bg_events = ["EA", "EB", "EC", "ED"]
    for _ in range(120):
        quarter = "2023Q1" if caseid % 2 else "2023Q2"
        fda = "2023-02-01" if quarter == "2023Q1" else "2023-05-01"
        add_case(caseid, quarter, fda,
                 rng.choice(bg_drugs), rng.choice(bg_events))
        caseid += 1

    # 30 strong DRUGX+EVENTA cases (the signal we will assert on).
    strong_cases = []
    for _ in range(30):
        quarter = "2023Q1" if caseid % 2 else "2023Q2"
        fda = "2023-02-15" if quarter == "2023Q1" else "2023-05-15"
        add_case(caseid, quarter, fda, "DRUGX", "EVENTA")
        strong_cases.append(caseid)
        caseid += 1

    # Case-version dedup: caseid 9001 has an OLD version (Q1: DRUGX+EVENTA) and a
    # NEWER version (Q2: DRUGX+EVENTB). Only EVENTB should survive.
    add_case(9001, "2023Q1", "2023-01-10", "DRUGX", "EVENTA", case_version=1)
    add_case(9001, "2023Q2", "2023-06-10", "DRUGX", "EVENTB", case_version=2)

    # Deleted case 9002 must be excluded entirely.
    add_case(9002, "2023Q1", "2023-01-20", "DRUGX", "EVENTA")

    # Partition reports/drugs/reactions by their own quarter for realism.
    rep_df = pd.DataFrame(reports)
    drug_df = pd.DataFrame(drugs)
    reac_df = pd.DataFrame(reactions)
    pid_to_q = dict(zip(rep_df["primaryid"], rep_df["faers_quarter"]))
    for q in ["2023Q1", "2023Q2"]:
        _write(rep_df[rep_df["faers_quarter"] == q], root, "reports", q)
        q_pids = rep_df.loc[rep_df["faers_quarter"] == q, "primaryid"]
        _write(drug_df[drug_df["primaryid"].isin(q_pids)], root, "drugs", q)
        _write(reac_df[reac_df["primaryid"].isin(q_pids)], root, "reactions", q)
        # Every case has a serious outcome -> seriousness_rate should be 1.0 everywhere.
        _write(pd.DataFrame({"primaryid": q_pids, "outcome_code": "HO"}),
               root, "outcomes", q)

    # DELETED list for case 9002.
    _write(pd.DataFrame({"caseid": ["9002"], "faers_quarter": ["2023Q2"]}),
           root, "deleted_cases", "2023Q2")

    # Distinct, non-deleted caseids = 120 background + 30 strong + 1 (9001) = 151.
    return {"n_cases": 151, "strong_a": 30}


def test_build_gold_bulk_dedup_and_scoring(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASIGNAL_DATA_ROOT", str(tmp_path))
    expected = _make_silver(tmp_path)

    # Import after env is set so module-level state picks up the tmp root if needed.
    from pharmasignal.pipeline import build_gold_bulk
    from pharmasignal.serving.lakehouse import read_gold

    summary = build_gold_bulk.build(trend_top_k=50)

    assert summary["cases"] == expected["n_cases"]

    # signal_scores is now the full, unfiltered co-occurring matrix (no separate _all).
    scores = read_gold("signal_scores", allow_sample=False)
    drugx_eventa = scores[
        (scores["drug_name_normalized"] == "DRUGX")
        & (scores["adverse_event"] == "EVENTA")
    ]
    assert len(drugx_eventa) == 1
    # The 30 strong cases; the old version of 9001 and the deleted 9002 must NOT count.
    assert int(drugx_eventa.iloc[0]["a_drug_event"]) == expected["strong_a"]

    # Dedup kept the NEWER version of 9001 -> DRUGX+EVENTB exists.
    assert not scores[
        (scores["drug_name_normalized"] == "DRUGX")
        & (scores["adverse_event"] == "EVENTB")
    ].empty

    # EBGM columns present and the strong pair is a robust signal.
    for col in ("ebgm", "eb05", "eb95", "ror", "prr", "chi_square", "seriousness_rate"):
        assert col in scores.columns
    assert float(drugx_eventa.iloc[0]["ebgm"]) > 1.0
    # All synthetic cases are serious -> rate 1.0 for the strong pair.
    assert float(drugx_eventa.iloc[0]["seriousness_rate"]) == pytest.approx(1.0)


def test_build_gold_bulk_errors_without_silver(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASIGNAL_DATA_ROOT", str(tmp_path))
    from pharmasignal.pipeline import build_gold_bulk

    with pytest.raises(SystemExit):
        build_gold_bulk.build()
