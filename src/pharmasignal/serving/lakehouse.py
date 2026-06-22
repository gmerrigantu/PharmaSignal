"""Lakehouse read/write over the storage backend (local Parquet or S3).

Locally we read Parquet with DuckDB/pandas; in AWS the same gold Parquet lives in S3
and is queried with Athena (or pandas+s3fs here). The dashboard only ever reads gold
tables (requirements §13). Backend is selected by ``PHARMASIGNAL_DATA_ROOT`` — see
storage.py.
"""
from __future__ import annotations

import os

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
    Parquet is loaded via pandas+fsspec, so no DuckDB httpfs setup is required).

    NOTE: this materializes each whole table into the connection first, so it does NOT
    scale to the full unfiltered matrix. For large marts use :func:`pushdown_query`,
    which lets DuckDB read Parquet directly (filter/limit/count pushdown, no full load).
    """
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


def gold_source(name: str) -> str | None:
    """Return a DuckDB-readable Parquet path for a gold table, or ``None`` if absent.

    Prefers the active lakehouse (local path or ``s3://`` URI); falls back to the bundled
    sample dataset. The returned string is meant to be embedded as ``read_parquet('...')``
    in a SQL query so DuckDB scans the file directly (with predicate/limit pushdown)
    rather than loading the whole table into memory first.
    """
    uri = storage.gold_uri(name)
    if storage.exists(uri):
        return uri
    sample = SAMPLE_DATA_DIR / "gold" / f"{name}.parquet"
    if sample.exists():
        return str(sample)
    return None


def _sql_str(val: str) -> str:
    """Escape a value for a single-quoted SQL literal."""
    return val.replace("'", "''")


def _configure_s3(con) -> None:
    """Enable DuckDB's httpfs S3 reader for the gold Parquet.

    The ``httpfs`` extension is pre-installed at build time into
    ``PHARMASIGNAL_DUCKDB_EXT_DIR`` so ``INSTALL`` is a local no-op (no runtime download).
    Credentials: AWS Lambda injects the execution-role keys as env vars
    (``AWS_ACCESS_KEY_ID``/``AWS_SECRET_ACCESS_KEY``/``AWS_SESSION_TOKEN``), so we build an
    explicit-config S3 secret from them — this needs only httpfs, not the ``aws`` extension
    (whose ``credential_chain`` provider would require a separate extension + network). If
    no env keys are present, fall back to ``credential_chain``.
    """
    ext_dir = os.getenv("PHARMASIGNAL_DUCKDB_EXT_DIR")
    if ext_dir:
        con.execute(f"SET extension_directory='{ext_dir}';")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    con.execute(f"SET s3_region='{region}';")

    key = os.getenv("AWS_ACCESS_KEY_ID")
    secret = os.getenv("AWS_SECRET_ACCESS_KEY")
    token = os.getenv("AWS_SESSION_TOKEN")
    if key and secret:
        parts = [f"KEY_ID '{_sql_str(key)}'", f"SECRET '{_sql_str(secret)}'",
                 f"REGION '{_sql_str(region)}'"]
        if token:
            parts.append(f"SESSION_TOKEN '{_sql_str(token)}'")
        con.execute(f"CREATE OR REPLACE SECRET pharmasignal_s3 (TYPE s3, {', '.join(parts)});")
    else:
        con.execute("INSTALL aws; LOAD aws;")
        con.execute("CREATE OR REPLACE SECRET pharmasignal_s3 "
                    "(TYPE s3, PROVIDER credential_chain);")


def pushdown_query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run a DuckDB query that reads gold Parquet directly via ``read_parquet('...')``.

    DuckDB pushes WHERE/LIMIT/COUNT down into the Parquet scan, so this serves any slice
    of the full unfiltered matrix without loading it into memory — and scales from the
    2024 mart (~10^6 rows) to the full 2012+ history (~10^7+). Configures S3 access when
    the lakehouse root is ``s3://``. Filter *values* must be passed as ``params`` (``?``
    placeholders); only trusted source paths/column names may be interpolated into ``sql``.
    """
    import duckdb

    con = duckdb.connect()
    try:
        if storage.is_s3():
            _configure_s3(con)
        return con.execute(sql, params or []).fetchdf()
    finally:
        con.close()
