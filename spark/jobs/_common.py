"""Shared helpers for the PySpark FAERS backfill jobs.

These jobs run unchanged in three places:
  * locally in ``local[*]`` mode (dev + tests, and even the real backfill on one box),
  * AWS EMR Serverless (the recommended serverless Spark engine),
  * any Spark cluster.

Paths are plain strings: a local filesystem path or an ``s3://`` URI. EMR Serverless and
local Spark both read/write those directly, so the same ``--data-root`` flows through.
"""
from __future__ import annotations

import re

from pyspark.sql import SparkSession


def get_spark(app_name: str) -> SparkSession:
    return (
        SparkSession.builder
        .appName(app_name)
        # Sensible defaults for a few-GB workload; harmless if the cluster overrides.
        .config("spark.sql.shuffle.partitions", "64")
        .config("spark.sql.parquet.compression.codec", "snappy")
        # FAERS carries malformed/ancient dates (e.g. year 0001); write them as-is
        # rather than failing on the Julian/Gregorian rebase guard (Spark >= 3.0).
        .config("spark.sql.parquet.datetimeRebaseModeInWrite", "CORRECTED")
        .config("spark.sql.parquet.int96RebaseModeInWrite", "CORRECTED")
        .getOrCreate()
    )


def silver_dir(data_root: str, table: str) -> str:
    return f"{data_root.rstrip('/')}/silver/faers/{table}"


def gold_dir(data_root: str) -> str:
    return f"{data_root.rstrip('/')}/gold"


def bronze_ascii_path(data_root: str, ref_year: int, ref_q: int, table: str) -> str:
    return (f"{data_root.rstrip('/')}/bronze/faers/"
            f"year={ref_year}/quarter=Q{ref_q}/ascii/{table}.txt")


_QUARTER_RE = re.compile(r"year=(\d{4})/quarter=Q([1-4])")


def parse_quarter_token(token: str) -> tuple[int, int]:
    """'2023q4' / '2023Q4' -> (2023, 4)."""
    y, q = token.lower().split("q")
    return int(y), int(q)


def expand_quarter_tokens(tokens: list[str]) -> list[tuple[int, int]]:
    """Expand quarter tokens incl. inclusive ranges ('2012q4..2023q4')."""
    out: list[tuple[int, int]] = []
    for tok in tokens:
        if ".." in tok:
            lo, hi = (parse_quarter_token(t) for t in tok.split("..", 1))
            y, q = lo
            while (y, q) <= hi:
                out.append((y, q))
                q += 1
                if q > 4:
                    q, y = 1, y + 1
        else:
            out.append(parse_quarter_token(tok))
    return out


def quarter_label(year: int, q: int) -> str:
    return f"{year}Q{q}"


def discover_quarters(data_root: str) -> list[tuple[int, int]]:
    """List (year, quarter) pairs that have a staged DEMO.txt under bronze."""
    import fsspec

    glob = f"{data_root.rstrip('/')}/bronze/faers/year=*/quarter=Q*/ascii/DEMO.txt"
    fs, _ = fsspec.core.url_to_fs(glob)
    found = []
    for path in fs.glob(glob):
        m = _QUARTER_RE.search(path)
        if m:
            found.append((int(m.group(1)), int(m.group(2))))
    return sorted(set(found))
