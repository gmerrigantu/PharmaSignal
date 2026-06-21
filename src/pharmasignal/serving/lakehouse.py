"""Lakehouse read/write over the storage backend (local Parquet or S3).

Locally we read Parquet with DuckDB/pandas; in AWS the same gold Parquet lives in S3
and is queried with Athena (or pandas+s3fs here). The dashboard only ever reads gold
tables (requirements §13). Backend is selected by ``PHARMASIGNAL_DATA_ROOT`` — see
storage.py.
"""
from __future__ import annotations

import pandas as pd

from ..paths import SAMPLE_DATA_DIR
from . import storage


def write_gold(df: pd.DataFrame, name: str) -> str:
    return storage.write_parquet(df, storage.gold_uri(name))


def read_gold(name: str, *, allow_sample: bool = True) -> pd.DataFrame:
    """Read a gold table, falling back to the bundled demo dataset if present."""
    uri = storage.gold_uri(name)
    if storage.exists(uri):
        return storage.read_parquet(uri)
    sample = SAMPLE_DATA_DIR / "gold" / f"{name}.parquet"
    if allow_sample and sample.exists():
        return pd.read_parquet(sample)
    raise FileNotFoundError(
        f"Gold table '{name}' not found at {uri} or bundled sample. "
        f"Run `make pipeline` (openFDA) or `make demo` (offline) first."
    )


def gold_exists(name: str) -> bool:
    if storage.exists(storage.gold_uri(name)):
        return True
    return (SAMPLE_DATA_DIR / "gold" / f"{name}.parquet").exists()


def active_source() -> str:
    """Whether the dashboard is reading live gold data (and from where) or the demo."""
    if storage.list_parquet(f"{storage.data_root()}/gold"):
        return "s3" if storage.is_s3() else "pipeline"
    if any((SAMPLE_DATA_DIR / "gold").glob("*.parquet")):
        return "demo"
    return "none"


def query(sql: str) -> pd.DataFrame:
    """Run a DuckDB SQL query with each gold Parquet registered as a view named after
    its file stem, e.g. ``SELECT * FROM signal_scores``. Works for local and S3 (gold
    Parquet is loaded via pandas+fsspec, so no DuckDB httpfs setup is required)."""
    import duckdb

    con = duckdb.connect()
    seen: set[str] = set()
    sources = [f"{storage.data_root()}/gold", str(SAMPLE_DATA_DIR / "gold")]
    for src in sources:
        for uri in storage.list_parquet(src):
            stem = uri.rsplit("/", 1)[-1][: -len(".parquet")]
            if stem in seen:
                continue
            con.register(stem, storage.read_parquet(uri))
            seen.add(stem)
    return con.execute(sql).fetchdf()
