"""Canonical filesystem locations for the local lakehouse.

The local lakehouse mirrors the cloud medallion layout (bronze/silver/gold). In a
cloud deployment these map to S3 prefixes (see docs/architecture.md); locally they
are directories under ``data/``.
"""
from __future__ import annotations

import os
from pathlib import Path

# Repo root = three levels up from this file: src/pharmasignal/paths.py -> repo
REPO_ROOT = Path(__file__).resolve().parents[2]

# CONFIG_DIR is overridable so packaged deployments (e.g. an EMR Serverless venv
# archive) can ship the YAML config alongside the code rather than next to the repo.
CONFIG_DIR = Path(os.getenv("PHARMASIGNAL_CONFIG_DIR", REPO_ROOT / "config"))
SAMPLE_DATA_DIR = REPO_ROOT / "sample_data"

# Allow overriding the data root (e.g. point at a mounted S3 prefix or a tmp dir).
DATA_ROOT = Path(os.getenv("PHARMASIGNAL_DATA_ROOT", REPO_ROOT / "data"))

BRONZE_DIR = DATA_ROOT / "bronze"
SILVER_DIR = DATA_ROOT / "silver"
GOLD_DIR = DATA_ROOT / "gold"

# Local-only paths — always the repo's data/ dir regardless of PHARMASIGNAL_DATA_ROOT.
# Use these in modules that do local I/O only (e.g. faers_quarterly, stage_faers)
# so that PHARMASIGNAL_DATA_ROOT=s3://... doesn't redirect their writes to a
# nonsensical local path.
LOCAL_DATA_ROOT = REPO_ROOT / "data"
LOCAL_BRONZE_DIR = LOCAL_DATA_ROOT / "bronze"
LOCAL_SILVER_DIR = LOCAL_DATA_ROOT / "silver"


def ensure_dirs() -> None:
    """Create the lakehouse directory tree if it does not yet exist."""
    for d in (BRONZE_DIR, SILVER_DIR, GOLD_DIR):
        d.mkdir(parents=True, exist_ok=True)


def gold_table_path(name: str) -> Path:
    """Return the Parquet path for a gold table (e.g. ``signal_scores``)."""
    return GOLD_DIR / f"{name}.parquet"


def silver_table_path(name: str) -> Path:
    return SILVER_DIR / f"{name}.parquet"
