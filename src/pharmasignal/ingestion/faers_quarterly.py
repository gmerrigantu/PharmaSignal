"""FAERS quarterly extract-file ingestion (production path, requirements §7.1).

The MVP defaults to openFDA API mode (openfda.py). This module implements the
production-scale path: download the official quarterly ZIPs, store them immutably
in bronze, extract the ASCII (``$``-delimited) tables, standardize columns across
quarters, and write partitioned silver Parquet.

It is intentionally runnable but NOT executed by the default `make pipeline` target
because each quarter is hundreds of MB. Invoke explicitly:

    python -m pharmasignal.ingestion.faers_quarterly 2023q4

Implements ING-FAERS-001..007.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import requests

from ..paths import BRONZE_DIR, SILVER_DIR
from ..transforms.normalize import normalize_drug, normalize_reaction

# Public FDA quarterly extract files. The ASCII (not XML) packages are used.
FAERS_BASE = "https://fis.fda.gov/content/Exports"
# Filename pattern, e.g. faers_ascii_2023Q4.zip
FILE_TEMPLATE = "faers_ascii_{year}Q{q}.zip"

# Canonical column maps absorb schema drift across quarters (ING-FAERS-004).
DEMO_COLUMNS = {
    "primaryid": "primaryid",
    "caseid": "caseid",
    "event_dt": "event_date",
    "rept_dt": "receive_date",
    "sex": "patient_sex",
    "age": "patient_age",
    "age_cod": "patient_age_unit",
    "reporter_country": "reporter_country",
    "occr_country": "reporter_country",
}


@dataclass(frozen=True)
class QuarterRef:
    year: int
    quarter: int  # 1..4

    @classmethod
    def parse(cls, s: str) -> "QuarterRef":
        # accepts "2023q4" / "2023Q4"
        year, q = s.lower().split("q")
        return cls(year=int(year), quarter=int(q))

    @property
    def label(self) -> str:
        return f"{self.year}Q{self.quarter}"


def download_quarter(ref: QuarterRef, *, timeout: int = 120) -> "io.BytesIO":
    """ING-FAERS-001: download the quarterly ZIP and persist it immutably to bronze.

    Returns the in-memory bytes for immediate extraction.
    """
    url = f"{FAERS_BASE}/{FILE_TEMPLATE.format(year=ref.year, q=ref.quarter)}"
    dest_dir = BRONZE_DIR / "faers" / f"year={ref.year}" / f"quarter=Q{ref.quarter}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"faers_ascii_{ref.label}.zip"

    if dest.exists():
        return io.BytesIO(dest.read_bytes())

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    # checksum + metadata sidecar (ING-FAERS-002)
    import hashlib

    meta = {
        "source_url": url,
        "source_file": dest.name,
        "file_size_bytes": len(resp.content),
        "sha256": hashlib.sha256(resp.content).hexdigest(),
        "ingest_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    (dest_dir / f"{dest.stem}.meta.json").write_text(__import__("json").dumps(meta, indent=2))
    return io.BytesIO(resp.content)


def _read_ascii_table(zf: zipfile.ZipFile, table: str, ref: QuarterRef) -> pd.DataFrame:
    """Read a single `$`-delimited ASCII table (e.g. DEMO, DRUG, REAC)."""
    # FDA names files like ASCII/DEMO23Q4.txt
    yy = str(ref.year)[2:]
    candidates = [
        f"ASCII/{table}{yy}Q{ref.quarter}.txt",
        f"ascii/{table}{yy}Q{ref.quarter}.txt",
        f"{table}{yy}Q{ref.quarter}.txt",
    ]
    name = next((n for n in candidates if n in zf.namelist()), None)
    if name is None:
        # fall back to any file starting with the table name
        name = next((n for n in zf.namelist() if table.lower() in n.lower() and n.lower().endswith(".txt")), None)
    if name is None:
        return pd.DataFrame()
    with zf.open(name) as fh:
        return pd.read_csv(fh, sep="$", dtype=str, encoding="latin-1", on_bad_lines="skip")


def build_silver_from_quarter(ref: QuarterRef) -> dict[str, int]:
    """ING-FAERS-003/005/006: parse DEMO/DRUG/REAC/OUTC/INDI and write silver Parquet.

    Returns a row-count summary. Tables are partitioned by year/quarter.
    """
    raw = download_quarter(ref)
    counts: dict[str, int] = {}
    with zipfile.ZipFile(raw) as zf:
        demo = _read_ascii_table(zf, "DEMO", ref)
        drug = _read_ascii_table(zf, "DRUG", ref)
        reac = _read_ascii_table(zf, "REAC", ref)
        outc = _read_ascii_table(zf, "OUTC", ref)
        indi = _read_ascii_table(zf, "INDI", ref)

    part = SILVER_DIR / "faers"
    # --- reports ---
    if not demo.empty:
        demo = demo.rename(columns={k: v for k, v in DEMO_COLUMNS.items() if k in demo.columns})
        demo["faers_quarter"] = ref.label
        demo["event_date"] = pd.to_datetime(demo.get("event_date"), format="%Y%m%d", errors="coerce")
        demo["receive_date"] = pd.to_datetime(demo.get("receive_date"), format="%Y%m%d", errors="coerce")
        demo["ingest_timestamp"] = datetime.now(timezone.utc)
        _write_partition(demo, part / "reports", ref)
        counts["reports"] = len(demo)

    # --- drugs (with normalization; raw always preserved) ---
    if not drug.empty:
        drug = drug.rename(columns={"drugname": "drug_name_raw", "drug_seq": "drug_seq", "role_cod": "role_code"})
        if "drug_name_raw" in drug.columns:
            norm = drug["drug_name_raw"].fillna("").map(normalize_drug)
            drug["drug_name_normalized"] = [n.normalized for n in norm]
            drug["drug_class"] = [n.drug_class for n in norm]
            drug["normalization_method"] = [n.method for n in norm]
            drug["normalization_confidence"] = [n.confidence for n in norm]
        drug["faers_quarter"] = ref.label
        _write_partition(drug, part / "drugs", ref)
        counts["drugs"] = len(drug)

    # --- reactions ---
    if not reac.empty:
        reac = reac.rename(columns={"pt": "reaction_term", "outc_cod": "reaction_outcome"})
        if "reaction_term" in reac.columns:
            reac["reaction_term_normalized"] = reac["reaction_term"].fillna("").map(normalize_reaction)
        reac["faers_quarter"] = ref.label
        _write_partition(reac, part / "reactions", ref)
        counts["reactions"] = len(reac)

    for name, frame in (("outcomes", outc), ("indications", indi)):
        if not frame.empty:
            frame = frame.copy()
            frame["faers_quarter"] = ref.label
            _write_partition(frame, part / name, ref)
            counts[name] = len(frame)

    return counts


def _write_partition(df: pd.DataFrame, base, ref: QuarterRef) -> None:
    out = base / f"year={ref.year}" / f"quarter=Q{ref.quarter}"
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "data.parquet", index=False)


def main(argv: list[str] | None = None) -> None:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: python -m pharmasignal.ingestion.faers_quarterly <YYYYqQ> [<YYYYqQ> ...]")
        return
    for token in args:
        ref = QuarterRef.parse(token)
        print(f"Ingesting FAERS {ref.label} ...")
        summary = build_silver_from_quarter(ref)
        print(f"  -> {summary}")


if __name__ == "__main__":
    main()
