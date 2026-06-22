"""End-to-end PySpark backfill test: synthetic bronze ASCII -> silver -> gold.

Exercises the same jobs that run on EMR Serverless (ingest_faers_spark +
build_gold_spark), in local[*] mode, with no network. Skipped if PySpark/Java aren't
installed so the rest of the suite still runs everywhere.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyspark")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spark" / "jobs"))


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession

    s = (SparkSession.builder.appName("test").master("local[2]")
         .config("spark.sql.shuffle.partitions", "4")
         .config("spark.ui.enabled", "false")
         .getOrCreate())
    yield s
    s.stop()


def _write_ascii(root: Path, year: int, q: int, table: str, header: str, rows: list[str]):
    d = root / "bronze" / "faers" / f"year={year}" / f"quarter=Q{q}" / "ascii"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{table}.txt").write_text("\n".join([header, *rows]), encoding="latin-1")


def _make_bronze(root: Path):
    demo_h = "primaryid$caseid$caseversion$fda_dt$event_dt$sex"
    drug_h = "primaryid$caseid$drug_seq$role_cod$drugname"
    reac_h = "primaryid$caseid$pt"

    demo_q1, drug_q1, reac_q1 = [], [], []
    demo_q2, drug_q2, reac_q2 = [], [], []
    pid = 0

    def add(demo, drug, reac, caseid, fda, drugname, pt, *, version=1):
        nonlocal pid
        pid += 1
        demo.append(f"{pid}${caseid}${version}${fda}$20230101$F")
        drug.append(f"{pid}${caseid}$1$PS${drugname}")
        reac.append(f"{pid}${caseid}${pt}")

    cid = 1
    # 60 background cases (alternating quarters) to populate marginals.
    bg = [("DA", "EA"), ("DB", "EB"), ("DC", "EC"), ("DD", "ED")]
    for i in range(60):
        dr, ev = bg[i % 4]
        if i % 2:
            add(demo_q1, drug_q1, reac_q1, cid, "20230201", dr, ev)
        else:
            add(demo_q2, drug_q2, reac_q2, cid, "20230501", dr, ev)
        cid += 1

    # 25 strong DRUGX+EVENTA cases (the signal we assert on).
    strong = 0
    for i in range(25):
        if i % 2:
            add(demo_q1, drug_q1, reac_q1, cid, "20230215", "DRUGX", "EVENTA")
        else:
            add(demo_q2, drug_q2, reac_q2, cid, "20230515", "DRUGX", "EVENTA")
        strong += 1
        cid += 1

    # Dedup: case 9001 has OLD version (Q1: DRUGX/EVENTA) + NEWER (Q2: DRUGX/EVENTB).
    add(demo_q1, drug_q1, reac_q1, 9001, "20230110", "DRUGX", "EVENTA", version=1)
    add(demo_q2, drug_q2, reac_q2, 9001, "20230610", "DRUGX", "EVENTB", version=2)
    # Deleted case 9002 must be excluded.
    add(demo_q1, drug_q1, reac_q1, 9002, "20230120", "DRUGX", "EVENTA")

    _write_ascii(root, 2023, 1, "DEMO", demo_h, demo_q1)
    _write_ascii(root, 2023, 1, "DRUG", drug_h, drug_q1)
    _write_ascii(root, 2023, 1, "REAC", reac_h, reac_q1)
    _write_ascii(root, 2023, 2, "DEMO", demo_h, demo_q2)
    _write_ascii(root, 2023, 2, "DRUG", drug_h, drug_q2)
    _write_ascii(root, 2023, 2, "REAC", reac_h, reac_q2)
    # DELETED list (one caseid per line) staged in Q2.
    dd = root / "bronze" / "faers" / "year=2023" / "quarter=Q2" / "ascii"
    (dd / "DELETED.txt").write_text("9002\n", encoding="latin-1")

    # 60 bg + 25 strong + 1 (9001 deduped) = 86 distinct non-deleted cases.
    return {"n_cases": 86, "strong_a": 25}


def test_spark_ingest_and_gold(spark, tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASIGNAL_DATA_ROOT", str(tmp_path))
    expected = _make_bronze(tmp_path)

    import build_gold_spark
    import ingest_faers_spark

    ingest_faers_spark.ingest(spark, str(tmp_path), [(2023, 1), (2023, 2)])
    summary = build_gold_spark.build(spark, str(tmp_path))

    assert summary["cases"] == expected["n_cases"]

    import pandas as pd
    scores = pd.read_parquet(os.path.join(tmp_path, "gold", "signal_scores_all.parquet"))
    dx_ea = scores[(scores["drug_name_normalized"] == "DRUGX")
                   & (scores["adverse_event"] == "EVENTA")]
    assert len(dx_ea) == 1
    assert int(dx_ea.iloc[0]["a_drug_event"]) == expected["strong_a"]
    # Dedup kept the newer 9001 version -> DRUGX/EVENTB present.
    assert not scores[(scores["drug_name_normalized"] == "DRUGX")
                      & (scores["adverse_event"] == "EVENTB")].empty
    for col in ("ebgm", "eb05", "eb95", "ror", "prr"):
        assert col in scores.columns
    assert float(dx_ea.iloc[0]["ebgm"]) > 1.0
