"""Option B: RxNorm ingredient re-aggregation + label folding (no network).

Covers the load-bearing behaviours:
  * ``build_drug_dimension`` collapses brand variants to one ``analysis_key`` (RxNorm
    resolve + ATC mocked — no network).
  * ``build_gold_bulk`` re-keys the matrix by ``analysis_key`` when the dimension mart
    exists, so two brand names co-occurring with the same event merge into ONE scored
    pair with summed counts.
  * ``build_label_flags.apply_to_signal_scores`` folds ``novel_flag`` / ``label_status``
    onto the matrix and refreshes the stats mart's ``novel_total``.
"""
from __future__ import annotations

import pandas as pd


def _write_silver(df: pd.DataFrame, root, table, quarter="2023Q1"):
    out = root / "silver" / "faers" / table / f"year={quarter[:4]}" / f"quarter={quarter[4:]}"
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "data.parquet", index=False)


def _make_brand_silver(root):
    """Two brand spellings of one ingredient, each co-occurring with EVENTA."""
    reports, drugs, reactions = [], [], []
    pid = 0

    def add(caseid, drug, event):
        nonlocal pid
        pid += 1
        reports.append({"primaryid": str(pid), "caseid": str(caseid), "case_version": 1,
                        "fda_date": pd.Timestamp("2023-02-01"), "faers_quarter": "2023Q1"})
        drugs.append({"primaryid": str(pid), "drug_name_normalized": drug,
                      "drug_name_raw": drug, "drug_class": None, "role_code": "PS"})
        reactions.append({"primaryid": str(pid), "reaction_term_normalized": event,
                          "reaction_term": event})

    caseid = 1
    # Background to populate marginals.
    for _ in range(80):
        add(caseid, "OTHERDRUG", "EB"); caseid += 1
    # 15 OZEMPIC+EVENTA and 12 WEGOVY+EVENTA — both ingredient = SEMAGLUTIDE.
    for _ in range(15):
        add(caseid, "OZEMPIC", "EVENTA"); caseid += 1
    for _ in range(12):
        add(caseid, "WEGOVY", "EVENTA"); caseid += 1

    for t, rows in (("reports", reports), ("drugs", drugs), ("reactions", reactions)):
        _write_silver(pd.DataFrame(rows), root, t)
    return {"oz": 15, "wg": 12}


def test_build_drug_dimension_collapses_brands(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASIGNAL_DATA_ROOT", str(tmp_path))
    _make_brand_silver(tmp_path)

    from pharmasignal.pipeline import build_drug_dimension
    from pharmasignal.transforms import rxnorm
    from pharmasignal.transforms.normalize import NormalizedDrug  # noqa: F401

    ing = {"OZEMPIC": ("SEMAGLUTIDE", "111"), "WEGOVY": ("SEMAGLUTIDE", "111"),
           "OTHERDRUG": ("OTHERDRUG", "222")}

    def fake_resolve(name, **_):
        name_u = name.strip().upper()
        if name_u in ing:
            iname, cui = ing[name_u]
            return rxnorm.RxNormMatch(cui, iname, cui, "rxnorm_exact", "high")
        return rxnorm.RxNormMatch(None, None, None, "rxnorm_unmatched", "none")

    monkeypatch.setattr(rxnorm, "resolve", fake_resolve)
    monkeypatch.setattr(rxnorm, "atc_class", lambda cui, **_: ("A", "Alimentary tract and metabolism"))

    df = build_drug_dimension.build(with_atc=True)
    by_name = df.set_index("drug_name_normalized")
    assert by_name.loc["OZEMPIC", "analysis_key"] == "SEMAGLUTIDE"
    assert by_name.loc["WEGOVY", "analysis_key"] == "SEMAGLUTIDE"
    assert by_name.loc["OZEMPIC", "drug_class_atc"] == "Alimentary tract and metabolism"
    # Two brands collapse to one ingredient key (+ OTHERDRUG) -> fewer keys than names.
    assert df["analysis_key"].nunique() < len(df)


def test_gold_bulk_reaggregates_at_ingredient(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASIGNAL_DATA_ROOT", str(tmp_path))
    counts = _make_brand_silver(tmp_path)

    from pharmasignal.pipeline import build_gold_bulk
    from pharmasignal.serving.lakehouse import read_gold, write_gold

    # Dimension mart: both brands -> SEMAGLUTIDE.
    dim = pd.DataFrame([
        {"drug_name_normalized": "OZEMPIC", "analysis_key": "SEMAGLUTIDE",
         "ingredient": "SEMAGLUTIDE", "ingredient_rxcui": "111",
         "rxnorm_method": "rxnorm_exact", "rxnorm_confidence": "high",
         "drug_class_atc": "Alimentary tract and metabolism", "report_count": 15},
        {"drug_name_normalized": "WEGOVY", "analysis_key": "SEMAGLUTIDE",
         "ingredient": "SEMAGLUTIDE", "ingredient_rxcui": "111",
         "rxnorm_method": "rxnorm_exact", "rxnorm_confidence": "high",
         "drug_class_atc": "Alimentary tract and metabolism", "report_count": 12},
    ])
    write_gold(dim, "drug_dimension")

    build_gold_bulk.build(trend_top_k=50)
    scores = read_gold("signal_scores", allow_sample=False)

    # The two brands collapsed: a single SEMAGLUTIDE+EVENTA row with summed cases.
    sem = scores[(scores["drug_name_normalized"] == "SEMAGLUTIDE")
                 & (scores["adverse_event"] == "EVENTA")]
    assert len(sem) == 1
    assert int(sem.iloc[0]["a_drug_event"]) == counts["oz"] + counts["wg"]
    # The raw brand names must NOT survive as separate keys.
    assert scores[scores["drug_name_normalized"].isin(["OZEMPIC", "WEGOVY"])].empty


def test_apply_label_flags_folds_novel(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASIGNAL_DATA_ROOT", str(tmp_path))
    from pharmasignal.pipeline import build_label_flags
    from pharmasignal.serving.lakehouse import read_gold, write_gold

    scores = pd.DataFrame([
        {"drug_name_normalized": "SEMAGLUTIDE", "adverse_event": "EVENTA",
         "disproportionality_flag": True, "ror": 5.0, "a_drug_event": 27,
         "b_drug_other_events": 3, "c_other_drugs_event": 1},
        {"drug_name_normalized": "SEMAGLUTIDE", "adverse_event": "NAUSEA",
         "disproportionality_flag": True, "ror": 4.0, "a_drug_event": 50,
         "b_drug_other_events": 5, "c_other_drugs_event": 2},
    ])
    write_gold(scores, "signal_scores")

    flags = pd.DataFrame([
        {"drug_name_normalized": "SEMAGLUTIDE", "adverse_event": "EVENTA",
         "labeled_event": False, "label_status": "novel", "novel_flag": True},
        {"drug_name_normalized": "SEMAGLUTIDE", "adverse_event": "NAUSEA",
         "labeled_event": True, "label_status": "labeled", "novel_flag": False},
    ])
    build_label_flags.apply_to_signal_scores(flags)

    merged = read_gold("signal_scores", allow_sample=False)
    assert "novel_flag" in merged.columns and "label_status" in merged.columns
    novel_row = merged[merged["adverse_event"] == "EVENTA"].iloc[0]
    assert bool(novel_row["novel_flag"]) is True
    assert novel_row["label_status"] == "novel"
    # Stats mart refreshed with novel_total.
    stats = read_gold("signal_scores_stats", allow_sample=False).iloc[0]
    assert int(stats["novel_total"]) == 1
