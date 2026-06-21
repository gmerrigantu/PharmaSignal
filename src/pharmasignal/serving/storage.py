"""Storage abstraction over local filesystem and S3 (the cloud lakehouse backend).

The lakehouse root is controlled by ``PHARMASIGNAL_DATA_ROOT``:
  - local (default):  ``/path/to/repo/data``
  - cloud:            ``s3://pharmasignal-data-<suffix>``

Both are handled uniformly through ``fsspec`` (s3fs for S3, which reads AWS creds from
the standard environment chain). This is what lets the *same* pipeline + dashboard code
run locally or against S3 with no changes — only the env var differs (requirements §5.1).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import fsspec
import pandas as pd


def data_root() -> str:
    """Lakehouse root URI/path. ``s3://...`` activates the cloud backend."""
    root = os.getenv("PHARMASIGNAL_DATA_ROOT")
    if root:
        return root.rstrip("/")
    from ..paths import DATA_ROOT  # local default

    return str(DATA_ROOT)


def is_s3() -> bool:
    return data_root().startswith("s3://")


def _join(*parts: str) -> str:
    """Join path parts with '/'. The first part keeps its leading slash so an absolute
    local root (``/Users/.../data``) or an ``s3://bucket`` root is preserved — only
    interior/trailing slashes are normalized. (A previous version stripped the leading
    slash, turning absolute local roots into relative paths and creating stray dirs.)"""
    clean = [p for p in parts if p not in ("", None)]
    if not clean:
        return ""
    first = clean[0].rstrip("/")
    return "/".join([first, *(p.strip("/") for p in clean[1:])])


def gold_uri(name: str) -> str:
    return f"{data_root()}/gold/{name}.parquet"


def silver_uri(name: str) -> str:
    return f"{data_root()}/silver/{name}.parquet"


def bronze_uri(*parts: str) -> str:
    return _join(data_root(), "bronze", *parts)


def exists(uri: str) -> bool:
    fs, path = fsspec.core.url_to_fs(uri)
    return fs.exists(path)


def list_parquet(dir_uri: str) -> list[str]:
    """Return fully-qualified URIs of *.parquet directly under ``dir_uri``."""
    fs, path = fsspec.core.url_to_fs(dir_uri)
    if not fs.exists(path):
        return []
    proto = "s3://" if is_s3() else ""
    return [f"{proto}{p}" if proto else p for p in fs.glob(f"{path}/*.parquet")]


def write_parquet(df: pd.DataFrame, uri: str) -> str:
    if not uri.startswith("s3://"):
        os.makedirs(os.path.dirname(uri), exist_ok=True)
    df.to_parquet(uri, index=False)  # pandas + fsspec handle s3:// transparently
    return uri


def read_parquet(uri: str) -> pd.DataFrame:
    return pd.read_parquet(uri)


def write_json(obj: dict, uri: str) -> None:
    if not uri.startswith("s3://"):
        os.makedirs(os.path.dirname(uri), exist_ok=True)
    with fsspec.open(uri, "w") as fh:
        json.dump(obj, fh)


def read_json(uri: str) -> dict:
    with fsspec.open(uri, "r") as fh:
        return json.load(fh)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
