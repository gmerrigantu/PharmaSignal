"""PySpark FAERS ingest: bronze ASCII -> typed, normalized, partitioned silver.

This is the parallel, whole-history counterpart to the per-quarter pandas ingester in
``ingestion/faers_quarterly.py``. It reads the staged ``$``-delimited ASCII tables for
each quarter (see ``ingestion/stage_faers.py``), selects a canonical column subset that
is stable across the FAERS era (2012Q4+), normalizes drug names once over the *distinct*
strings (cheap), and writes ``silver/faers/{table}`` partitioned by year/quarter — the
exact layout ``build_gold_bulk`` / ``build_gold_spark`` read.

Schema drift across quarters is handled by reading each quarter separately, projecting
to the canonical columns, then ``unionByName(allowMissingColumns=True)``.

Run (local):
    PYTHONPATH=src spark-submit spark/jobs/ingest_faers_spark.py \
        --data-root ./data --quarters 2023q1..2023q4
"""
from __future__ import annotations

import argparse
import sys

from pyspark.sql import DataFrame, functions as F

# Make the pharmasignal package importable both locally (src/) and on a packaged cluster.
try:
    from pharmasignal.ingestion import faers_quarterly as fq
except ModuleNotFoundError:  # pragma: no cover - cluster/local path shim
    sys.path.insert(0, "src")
    from pharmasignal.ingestion import faers_quarterly as fq

try:
    from . import _common as C
except ImportError:  # spark-submit runs the file as a script, not a package
    import _common as C  # type: ignore


# Canonical raw->silver column maps per table (FAERS-era lowercase headers).
DRUG_COLUMNS = {"primaryid": "primaryid", "caseid": "caseid", "drug_seq": "drug_seq",
                "role_cod": "role_code", "drugname": "drug_name_raw"}
REAC_COLUMNS = {"primaryid": "primaryid", "caseid": "caseid", "pt": "reaction_term"}
OUTC_COLUMNS = {"primaryid": "primaryid", "caseid": "caseid", "outc_cod": "outcome_code"}
INDI_COLUMNS = {"primaryid": "primaryid", "caseid": "caseid",
                "indi_drug_seq": "drug_seq", "indi_pt": "indication"}

TABLE_COLUMN_MAPS = {
    "reports": ("DEMO", fq.DEMO_COLUMNS),
    "drugs": ("DRUG", DRUG_COLUMNS),
    "reactions": ("REAC", REAC_COLUMNS),
    "outcomes": ("OUTC", OUTC_COLUMNS),
    "indications": ("INDI", INDI_COLUMNS),
    "therapies": ("THER", fq.THER_COLUMNS),
    "report_sources": ("RPSR", fq.RPSR_COLUMNS),
}

def _normalization_table(spark, drugs: DataFrame, data_root: str) -> DataFrame:
    """Normalize the *distinct* raw drug strings on the driver, return a join table.

    FAERS has only a few hundred thousand distinct spellings, so resolving them once on
    the driver (rather than a per-row executor UDF) is both cheaper and avoids shipping
    Python closures to executors — the analysis key for ingredient-level rollups. The
    small result is materialized via pandas->Parquet and re-read with Spark (works on
    both local and S3, and sidesteps row-pickling quirks across Python versions).
    """
    import pandas as pd

    from pharmasignal.serving import storage
    from pharmasignal.transforms.normalize import normalize_drug

    names = [r["drug_name_raw"] for r in drugs.select("drug_name_raw").distinct().collect()]
    recs = [normalize_drug(x or "") for x in names]
    pdf = pd.DataFrame({
        "drug_name_raw": names,
        "drug_name_normalized": [r.normalized for r in recs],
        "drug_class": [r.drug_class for r in recs],
        "normalization_method": [r.method for r in recs],
        "normalization_confidence": [r.confidence for r in recs],
    })
    tmp = f"{data_root.rstrip('/')}/_tmp/drug_norm.parquet"
    storage.write_parquet(pdf, tmp)
    return spark.read.parquet(tmp)


def _read_table(spark, path: str, colmap: dict, year: int, q: int) -> DataFrame | None:
    """Read one quarter's ASCII table, project to canonical columns, tag partition."""
    try:
        df = (spark.read
              .option("sep", "$").option("header", True)
              .option("encoding", "latin1").option("mode", "PERMISSIVE")
              .csv(path))
    except Exception:
        return None
    if not df.columns:
        return None
    lower = {c.lower(): c for c in df.columns}
    # Several raw columns can map to the same canonical name (e.g. occr_country and
    # reporter_country -> reporter_country). Keep the first present per target so the
    # projection never produces a duplicate column (Spark rejects those).
    present, seen = [], set()
    for src, dst in colmap.items():
        if src in lower and dst not in seen:
            present.append((src, dst))
            seen.add(dst)
    if not present:
        return None
    df = df.select([F.col(lower[src]).alias(dst) for src, dst in present])
    return (df
            .withColumn("year", F.lit(year))
            .withColumn("quarter", F.lit(f"Q{q}"))
            .withColumn("faers_quarter", F.lit(C.quarter_label(year, q))))


def _to_date(df: DataFrame, col: str) -> DataFrame:
    if col in df.columns:
        return df.withColumn(col, F.to_date(F.col(col).cast("string"), "yyyyMMdd"))
    return df


def ingest(spark, data_root: str, quarters: list[tuple[int, int]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for silver_name, (ascii_table, colmap) in TABLE_COLUMN_MAPS.items():
        frames = []
        for (year, q) in quarters:
            path = C.bronze_ascii_path(data_root, year, q, ascii_table)
            part = _read_table(spark, path, colmap, year, q)
            if part is not None:
                frames.append(part)
        if not frames:
            continue
        df = frames[0]
        for f in frames[1:]:
            df = df.unionByName(f, allowMissingColumns=True)

        if silver_name == "reports":
            for dcol in ("event_date", "receive_date", "fda_date"):
                df = _to_date(df, dcol)
        elif silver_name == "reactions":
            df = df.withColumn("reaction_term_normalized",
                               F.upper(F.trim(F.col("reaction_term"))))
        elif silver_name == "drugs":
            norm = _normalization_table(spark, df, data_root)
            df = df.join(F.broadcast(norm), "drug_name_raw", "left")

        out = C.silver_dir(data_root, silver_name)
        (df.write.mode("overwrite")
           .partitionBy("year", "quarter")
           .parquet(out))
        counts[silver_name] = df.count()
        print(f"[ingest] {silver_name}: {counts[silver_name]:,} rows -> {out}", flush=True)
    return counts


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-root", required=True, help="local path or s3:// URI")
    p.add_argument("--quarters", nargs="*", default=[],
                   help="e.g. 2012q4..2023q4 (default: discover staged quarters)")
    args = p.parse_args(argv)

    quarters = (C.expand_quarter_tokens(args.quarters) if args.quarters
                else C.discover_quarters(args.data_root))
    if not quarters:
        raise SystemExit("no staged quarters found; run stage_faers first")

    spark = C.get_spark("pharmasignal-ingest")
    print(f"[ingest] {len(quarters)} quarters: "
          f"{C.quarter_label(*quarters[0])}..{C.quarter_label(*quarters[-1])}", flush=True)
    counts = ingest(spark, args.data_root, quarters)
    print(f"[ingest] done: {counts}", flush=True)
    spark.stop()


if __name__ == "__main__":
    main()
