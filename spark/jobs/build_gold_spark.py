"""PySpark whole-history gold build: silver FAERS -> scored gold marts.

The heavy distributed work (global case-version dedup, DELETED exclusion, drug/event
marginals, and the co-occurrence count for every pair) runs in Spark. The result —
one compact row per co-occurring (drug, event) — is collected to the driver and scored
with the *same* :mod:`pharmasignal.pipeline.scoring` code the DuckDB path uses, so the
two engines produce identical statistics. The gold marts are small, so they are written
as single Parquet files in the existing ``gold/<name>.parquet`` layout the API reads.

Run (local):
    PYTHONPATH=src spark-submit spark/jobs/build_gold_spark.py --data-root ./data
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import datetime, timezone

from pyspark.sql import DataFrame, functions as F
from pyspark.sql.window import Window

try:
    from pharmasignal import config
except ModuleNotFoundError:  # pragma: no cover - cluster/local path shim
    sys.path.insert(0, "src")
    from pharmasignal import config

from pharmasignal.pipeline import scoring
from pharmasignal.quality import checks
from pharmasignal.serving import storage

try:
    from . import _common as C
except ImportError:
    import _common as C  # type: ignore

# FAERS DRUG.role_code values treated as suspect (primary/secondary/interacting).
SUSPECT_ROLES = ["PS", "SS", "I"]
# Cap the number of pairs we compute quarterly trend for (bounds shuffle cost + keeps
# the emerging_signals mart small enough for the single-payload dashboard summary).
TREND_PAIR_CAP = 2000


def _deduped_latest(spark, data_root: str) -> DataFrame:
    """Latest primaryid per caseid, with DELETED caseids removed."""
    reports = spark.read.parquet(C.silver_dir(data_root, "reports"))
    order = [F.col("fda_date").desc_nulls_last()]
    if "case_version" in reports.columns:
        order.append(F.col("case_version").cast("int").desc_nulls_last())
    order.append(F.col("primaryid").desc())
    w = Window.partitionBy("caseid").orderBy(*order)
    latest = (reports.withColumn("_rn", F.row_number().over(w))
              .where(F.col("_rn") == 1)
              .select("caseid", "primaryid"))

    deleted = _deleted_caseids(spark, data_root)
    if deleted is not None:
        latest = latest.join(deleted, "caseid", "left_anti")
    return latest


def _deleted_caseids(spark, data_root: str) -> DataFrame | None:
    glob = f"{data_root.rstrip('/')}/bronze/faers/year=*/quarter=Q*/ascii/DELETED.txt"
    try:
        txt = spark.read.text(glob)
    except Exception:
        return None
    if txt.limit(1).count() == 0:
        return None
    return (txt.select(F.trim(F.split("value", r"\$").getItem(0)).alias("caseid"))
            .where((F.col("caseid") != "") & (F.lower("caseid") != "caseid"))
            .distinct())


def _case_drug(spark, data_root: str, latest: DataFrame) -> DataFrame:
    drugs = spark.read.parquet(C.silver_dir(data_root, "drugs"))
    drug_expr = F.coalesce(
        F.when(F.trim(F.coalesce(F.col("drug_name_normalized"), F.lit(""))) != "",
               F.col("drug_name_normalized")),
        F.col("drug_name_raw"))
    out = (drugs
           .where(F.upper("role_code").isin(SUSPECT_ROLES))
           .withColumn("drug", drug_expr)
           .where(F.col("drug").isNotNull())
           .join(latest.select("primaryid"), "primaryid")
           .groupBy("primaryid", "drug")
           .agg(F.first("drug_class", ignorenulls=True).alias("drug_class")))
    return out


def _case_reaction(spark, data_root: str, latest: DataFrame) -> DataFrame:
    reac = spark.read.parquet(C.silver_dir(data_root, "reactions"))
    event_expr = F.coalesce(
        F.when(F.trim(F.coalesce(F.col("reaction_term_normalized"), F.lit(""))) != "",
               F.col("reaction_term_normalized")),
        F.col("reaction_term"))
    return (reac
            .withColumn("event", event_expr)
            .where(F.col("event").isNotNull())
            .join(latest.select("primaryid"), "primaryid")
            .select("primaryid", "event").distinct())


def _serious_pids(spark, data_root: str):
    """Distinct primaryids with a reported serious outcome (FAERS OUTC table).

    Presence in OUTC means at least one serious outcome (death, hospitalization, etc.)
    was reported for the case. Returns None if outcomes weren't ingested.
    """
    import fsspec

    glob = f"{C.silver_dir(data_root, 'outcomes')}/*/*/*.parquet"
    fs, _ = fsspec.core.url_to_fs(glob)
    try:
        if not fs.glob(glob):
            return None
    except Exception:
        return None
    outc = spark.read.parquet(C.silver_dir(data_root, "outcomes"))
    return outc.select("primaryid").distinct().withColumn("is_serious", F.lit(True))


def _pairs(case_drug: DataFrame, case_reaction: DataFrame, serious_pids=None):
    drug_tot = case_drug.groupBy("drug").agg(
        F.first("drug_class", ignorenulls=True).alias("drug_class"),
        F.countDistinct("primaryid").alias("drug_total"))
    event_tot = case_reaction.groupBy("event").agg(
        F.countDistinct("primaryid").alias("event_total"))

    joined = case_drug.select("primaryid", "drug").join(case_reaction, "primaryid")
    if serious_pids is not None:
        joined = joined.join(F.broadcast(serious_pids), "primaryid", "left")
        pair = joined.groupBy("drug", "event").agg(
            F.countDistinct("primaryid").alias("a"),
            F.countDistinct(F.when(F.col("is_serious"), F.col("primaryid"))).alias("serious_a"))
    else:
        pair = joined.groupBy("drug", "event").agg(F.countDistinct("primaryid").alias("a"))

    cols = [F.col("drug").alias("drug_name_normalized"), "drug_class",
            F.col("event").alias("adverse_event"), "a", "drug_total", "event_total"]
    if serious_pids is not None:
        cols.append("serious_a")
    return pair.join(drug_tot, "drug").join(event_tot, "event").select(*cols)


def _quarterly_trend(spark, data_root: str, case_drug, case_reaction, latest, served_keys):
    """Per-quarter distinct-case counts, restricted to the served top pairs."""
    reports = spark.read.parquet(C.silver_dir(data_root, "reports"))
    pid_q = (latest.join(reports.select("primaryid", "faers_quarter"), "primaryid")
             .select("primaryid", "faers_quarter").distinct())
    joined = (case_drug.select("primaryid", "drug")
              .join(case_reaction, "primaryid")
              .join(F.broadcast(served_keys), (F.col("drug") == served_keys.k_drug) &
                    (F.col("event") == served_keys.k_event))
              .join(pid_q, "primaryid"))
    return (joined.groupBy(F.col("drug").alias("drug_name_normalized"),
                           F.col("event").alias("adverse_event"), "faers_quarter")
            .agg(F.countDistinct("primaryid").alias("report_count")))


def build(spark, data_root: str) -> dict:
    started = time.time()
    thresholds = config.load_thresholds()
    weights = config.load_priority_weights()

    latest = _deduped_latest(spark, data_root).cache()
    n_cases = latest.count()
    print(f"[gold] deduped cases N = {n_cases:,}", flush=True)

    case_drug = _case_drug(spark, data_root, latest).cache()
    case_reaction = _case_reaction(spark, data_root, latest).cache()
    serious_pids = _serious_pids(spark, data_root)

    pairs_pd = _pairs(case_drug, case_reaction, serious_pids).toPandas()
    print(f"[gold] co-occurring pairs = {len(pairs_pd):,}", flush=True)

    scores_all = scoring.score_pairs(pairs_pd, n_cases, thresholds)
    scores_df = scoring.served_mart(scores_all)
    print(f"[gold] served signal_scores rows = {len(scores_df):,}", flush=True)

    # Trend only for the strongest served pairs (bounds the extra shuffle).
    emerging_df = _build_emerging(spark, data_root, case_drug, case_reaction, latest,
                                  scores_df, thresholds, weights)

    storage.write_parquet(scores_df, storage.gold_uri("signal_scores"))
    storage.write_parquet(scores_all, storage.gold_uri("signal_scores_all"))
    storage.write_parquet(emerging_df, storage.gold_uri("emerging_signals"))

    check_results = checks.check_signal_scores(scores_df)
    summary = checks.summarize(check_results)
    import pandas as pd

    health = pd.DataFrame([{
        "run_id": str(uuid.uuid4()),
        "run_timestamp": datetime.now(timezone.utc),
        "source": "faers_spark_backfill",
        "source_period": "whole-history",
        "status": "success" if summary["failed_checks"] == 0 else "failed",
        "rows_raw": n_cases,
        "rows_silver": n_cases,
        "rows_gold": len(scores_df),
        "failed_checks": summary["failed_checks"],
        "warning_checks": summary["warning_checks"],
        "duration_seconds": round(time.time() - started, 1),
        "estimated_cost_usd": None,  # filled by the EMR Serverless orchestrator
        "git_commit": None,
        "notes": "PySpark whole-history backfill (EMR Serverless / local Spark).",
    }])
    storage.write_parquet(health, storage.gold_uri("pipeline_health"))
    storage.write_parquet(pd.DataFrame([c.__dict__ for c in check_results]),
                          storage.gold_uri("data_quality_checks"))

    return {
        "cases": n_cases,
        "pairs_all": len(scores_all),
        "pairs_served": len(scores_df),
        "emerging": len(emerging_df),
        "flagged": int(scores_df["disproportionality_flag"].sum()) if len(scores_df) else 0,
        "elapsed_seconds": round(time.time() - started, 1),
    }


def _build_emerging(spark, data_root, case_drug, case_reaction, latest,
                    scores_df, thresholds, weights):
    import pandas as pd

    if scores_df.empty:
        return pd.DataFrame()
    rank_col = "eb05" if "eb05" in scores_df else "ror"
    top = scores_df.sort_values(rank_col, ascending=False).head(TREND_PAIR_CAP)
    keys_pd = top[["drug_name_normalized", "adverse_event"]].rename(
        columns={"drug_name_normalized": "k_drug", "adverse_event": "k_event"})
    # Round-trip via Parquet (works local + S3, avoids pandas->Spark row pickling).
    keys_uri = f"{data_root.rstrip('/')}/_tmp/served_keys.parquet"
    storage.write_parquet(keys_pd, keys_uri)
    served_keys = spark.read.parquet(keys_uri)
    trend = _quarterly_trend(spark, data_root, case_drug, case_reaction,
                             latest, served_keys).toPandas()
    return scoring.emerging_signals(trend, scores_df, thresholds, weights,
                                    top_k=TREND_PAIR_CAP)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-root", required=True, help="local path or s3:// URI")
    args = p.parse_args(argv)

    # The gold marts are written via storage.* (pandas), which honors this env var.
    import os
    os.environ.setdefault("PHARMASIGNAL_DATA_ROOT", args.data_root)

    spark = C.get_spark("pharmasignal-gold")
    summary = build(spark, args.data_root)
    print(f"[gold] complete: {summary}", flush=True)
    spark.stop()


if __name__ == "__main__":
    main()
