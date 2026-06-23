"""FAERS quarterly extract-file ingestion (production path, requirements §7.1).

The MVP defaults to openFDA API mode (openfda.py). This module implements the
production-scale path: download the official quarterly ZIPs, store them immutably
in bronze, extract the ASCII (``$``-delimited) tables, standardize columns across
quarters, and write partitioned silver Parquet.

It is intentionally runnable but NOT executed by the default `make pipeline` target
because each quarter is hundreds of MB. Invoke explicitly:

    python -m pharmasignal.ingestion.faers_quarterly 2023q4

Implements ING-FAERS-001..007.

Resume behaviour
----------------
Each quarter is marked complete by a ``_SUCCESS`` sentinel written into the silver
``reports`` partition after all tables for that quarter are flushed.  On re-run the
quarter is skipped automatically.  Pass ``--force`` to overwrite existing silver.
Quarters produced by the Spark backfill path (which writes ``part-*.parquet`` files
rather than a sentinel) are also recognised as complete.

Disk / memory safety
--------------------
Pass ``--prune-bronze`` to delete each ZIP immediately after the quarter is ingested —
this keeps the peak extra disk footprint to ~one quarter's ZIP size (~300–500 MB) rather
than the cumulative total.  A hard 2 GB free-space floor is checked before each download;
a softer 5 GB warning is printed below that.
"""
from __future__ import annotations

import io
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import requests

from .. import config
from ..paths import LOCAL_BRONZE_DIR as BRONZE_DIR, LOCAL_SILVER_DIR as SILVER_DIR
from ..transforms.normalize import normalize_drug, normalize_reaction

# Public FDA quarterly extract files. The ASCII (not XML) packages are used.
FAERS_BASE = "https://fis.fda.gov/content/Exports"
# Filename pattern, e.g. faers_ascii_2023Q4.zip
FILE_TEMPLATE = "faers_ascii_{year}Q{q}.zip"

# Canonical column maps absorb schema drift across quarters (ING-FAERS-004).
DEMO_COLUMNS = {
    "primaryid": "primaryid",
    "caseid": "caseid",
    "caseversion": "case_version",
    "fda_dt": "fda_date",          # FDA receipt date -> drives case-version dedup
    "event_dt": "event_date",
    "rept_dt": "receive_date",
    "sex": "patient_sex",
    "gndr_cod": "patient_sex",     # some FAERS years use gndr_cod instead of sex
    "age": "patient_age",
    "age_cod": "patient_age_unit",
    "reporter_country": "reporter_country",
    "occr_country": "reporter_country",
}

# THER (drug therapy dates -> time-to-onset) and RPSR (report source -> consumer vs
# healthcare-professional vs literature) were missing from the original ingester; the
# upgrade plan (WS1 §4) requires both.
THER_COLUMNS = {
    "primaryid": "primaryid",
    "caseid": "caseid",
    "dsg_drug_seq": "drug_seq",
    "start_dt": "therapy_start_date",
    "end_dt": "therapy_end_date",
    "dur": "therapy_duration",
    "dur_cod": "therapy_duration_unit",
}
RPSR_COLUMNS = {
    "primaryid": "primaryid",
    "caseid": "caseid",
    "rpsr_cod": "report_source_code",
}


# ---------------------------------------------------------------------------
# Resume / disk-safety helpers
# ---------------------------------------------------------------------------

def _silver_reports_dir(ref: "QuarterRef"):
    return SILVER_DIR / "faers" / "reports" / f"year={ref.year}" / f"quarter=Q{ref.quarter}"


def _success_marker(ref: "QuarterRef"):
    return _silver_reports_dir(ref) / "_SUCCESS"


def _is_quarter_ingested(ref: "QuarterRef") -> bool:
    """True if this quarter's silver is already complete.

    Accepts both the ``_SUCCESS`` sentinel written by this module and the
    Spark-generated ``part-*.parquet`` layout (no sentinel, but parquets exist).
    """
    if _success_marker(ref).exists():
        return True
    d = _silver_reports_dir(ref)
    return d.is_dir() and any(d.glob("*.parquet"))


def _mark_ingested(ref: "QuarterRef") -> None:
    """Write a _SUCCESS sentinel so subsequent runs skip this quarter."""
    _silver_reports_dir(ref).mkdir(parents=True, exist_ok=True)
    _success_marker(ref).touch()


def _check_disk_space(path=None, hard_floor_gb: float = 2.0, warn_floor_gb: float = 5.0) -> None:
    """Abort if free space is below the hard floor; warn below the soft floor."""
    check_path = path or str(SILVER_DIR.parent.parent)
    usage = shutil.disk_usage(check_path)
    free_gb = usage.free / (1024 ** 3)
    if free_gb < hard_floor_gb:
        raise SystemExit(
            f"Only {free_gb:.1f} GB free — aborting to protect your disk.  "
            f"Free up at least {hard_floor_gb:.0f} GB then retry.  "
            "Tip: run with --prune-bronze to delete each ZIP after ingest."
        )
    if free_gb < warn_floor_gb:
        print(
            f"  [warn] {free_gb:.1f} GB free — consider --prune-bronze to reclaim space.",
            flush=True,
        )


def _prune_bronze_zip(ref: "QuarterRef") -> None:
    """Delete the downloaded ZIP for *ref* to reclaim disk space."""
    d = BRONZE_DIR / "faers" / f"year={ref.year}" / f"quarter=Q{ref.quarter}"
    removed = []
    for f in d.glob("*.zip"):
        f.unlink()
        removed.append(f.name)
    if removed:
        print(f"  [pruned] {', '.join(removed)}", flush=True)


# ---------------------------------------------------------------------------

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

    _check_disk_space()

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
        df = pd.read_csv(fh, sep="$", dtype=str, encoding="latin-1", on_bad_lines="skip")
    # Early FAERS quarterly files begin with a UTF-8 BOM (\xef\xbb\xbf) that pandas
    # reads as the latin-1 characters 'ï»¿' prepended to the first column name.
    # Strip both the decoded form and the raw Unicode BOM character to be safe.
    df.columns = [c.replace("ï»¿", "").replace("﻿", "").strip() for c in df.columns]
    return df


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
        ther = _read_ascii_table(zf, "THER", ref)
        rpsr = _read_ascii_table(zf, "RPSR", ref)
        deleted_ids = _read_deleted_cases(zf)

    part = SILVER_DIR / "faers"
    # --- reports ---
    if not demo.empty:
        demo = demo.rename(columns={k: v for k, v in DEMO_COLUMNS.items() if k in demo.columns})
        # Some older quarters have both `reporter_country` and `occr_country` (or both
        # `sex` and `gndr_cod`) in the same file.  After the rename both become the same
        # canonical name — drop the second occurrence to avoid the duplicate-column error.
        demo = demo.loc[:, ~demo.columns.duplicated()]
        demo["faers_quarter"] = ref.label
        demo["event_date"] = pd.to_datetime(demo.get("event_date"), format="%Y%m%d", errors="coerce")
        demo["receive_date"] = pd.to_datetime(demo.get("receive_date"), format="%Y%m%d", errors="coerce")
        demo["fda_date"] = pd.to_datetime(demo.get("fda_date"), format="%Y%m%d", errors="coerce")
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

    # --- therapy dates + report source (renamed for stable downstream joins) ---
    if not ther.empty:
        ther = ther.rename(columns={k: v for k, v in THER_COLUMNS.items() if k in ther.columns})
        ther["faers_quarter"] = ref.label
        _write_partition(ther, part / "therapies", ref)
        counts["therapies"] = len(ther)

    if not rpsr.empty:
        rpsr = rpsr.rename(columns={k: v for k, v in RPSR_COLUMNS.items() if k in rpsr.columns})
        rpsr["faers_quarter"] = ref.label
        _write_partition(rpsr, part / "report_sources", ref)
        counts["report_sources"] = len(rpsr)

    for name, frame in (("outcomes", outc), ("indications", indi)):
        if not frame.empty:
            frame = frame.copy()
            frame["faers_quarter"] = ref.label
            _write_partition(frame, part / name, ref)
            counts[name] = len(frame)

    # --- deleted cases (FDA ships a list of superseded caseids each quarter) ---
    if deleted_ids:
        deleted_df = pd.DataFrame({"caseid": deleted_ids})
        deleted_df["faers_quarter"] = ref.label
        _write_partition(deleted_df, part / "deleted_cases", ref)
        counts["deleted_cases"] = len(deleted_df)

    # Mark this quarter done so re-runs skip it.
    _mark_ingested(ref)
    return counts


def _read_deleted_cases(zf: zipfile.ZipFile) -> list[str]:
    """Return the list of caseids FDA marked deleted in this quarter's extract.

    FDA ships these in the ``deleted/`` folder of the ASCII ZIP, one caseid per line
    (filenames vary by quarter, e.g. ``ADR..DELETED..txt``), so we match by path.
    """
    names = [
        n for n in zf.namelist()
        if "delet" in n.lower() and n.lower().endswith(".txt")
    ]
    ids: list[str] = []
    for name in names:
        with zf.open(name) as fh:
            for raw_line in io.TextIOWrapper(fh, encoding="latin-1"):
                token = raw_line.strip().split("$")[0].strip()
                if token and token.lower() != "caseid":
                    ids.append(token)
    return ids


def _write_partition(df: pd.DataFrame, base, ref: QuarterRef) -> None:
    out = base / f"year={ref.year}" / f"quarter=Q{ref.quarter}"
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "data.parquet", index=False)


def expand_quarters(tokens: list[str]) -> list[QuarterRef]:
    """Expand CLI quarter tokens, supporting inclusive ranges with ``..``.

    Accepts individual quarters ("2023q4") and ranges ("2021q1..2023q4"), in any mix.
    """
    refs: list[QuarterRef] = []
    for token in tokens:
        if ".." in token:
            lo_s, hi_s = token.split("..", 1)
            lo, hi = QuarterRef.parse(lo_s), QuarterRef.parse(hi_s)
            y, q = lo.year, lo.quarter
            while (y, q) <= (hi.year, hi.quarter):
                refs.append(QuarterRef(year=y, quarter=q))
                q += 1
                if q > 4:
                    q, y = 1, y + 1
        else:
            refs.append(QuarterRef.parse(token))
    return refs


def main(argv: list[str] | None = None) -> None:
    import sys

    raw_args = argv if argv is not None else sys.argv[1:]

    # Parse flags (-- prefixed); everything else is quarter tokens.
    force = "--force" in raw_args
    prune_bronze = "--prune-bronze" in raw_args
    args = [a for a in raw_args if not a.startswith("--")]

    if not args:
        args = list(config.load_faers_config().get("quarters", []))
    if not args:
        print(
            "usage: python -m pharmasignal.ingestion.faers_quarterly "
            "<YYYYqQ | YYYYqQ..YYYYqQ> [...]\n"
            "flags: --force (re-ingest already-done quarters)  "
            "--prune-bronze (delete ZIP after each quarter)"
        )
        return

    refs = expand_quarters(args)
    n_total = len(refs)
    n_skip = sum(1 for r in refs if _is_quarter_ingested(r))
    n_todo = n_total - n_skip if not force else n_total

    print(
        f"[faers_quarterly] {n_total} quarters requested — "
        f"{n_skip} already ingested, {n_todo} to process"
        + (" (--force: re-ingesting all)" if force else ""),
        flush=True,
    )

    done, skipped = 0, 0
    for i, ref in enumerate(refs, 1):
        if not force and _is_quarter_ingested(ref):
            print(f"  [{i}/{n_total}] {ref.label} — already ingested, skipping", flush=True)
            skipped += 1
            continue
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"  [{i}/{n_total}] {ref.label} — ingesting ... ({ts})", flush=True)
        try:
            summary = build_silver_from_quarter(ref)
        except Exception as exc:
            import requests as _req
            if isinstance(exc, _req.HTTPError) and exc.response is not None and exc.response.status_code == 404:
                print(
                    f"  [{i}/{n_total}] {ref.label} — not yet published by FDA (HTTP 404); "
                    "skipping.  Re-run once FDA releases this quarter.",
                    flush=True,
                )
                skipped += 1
                continue
            raise
        print(f"    -> {summary}", flush=True)
        if prune_bronze:
            _prune_bronze_zip(ref)
        done += 1

    print(
        f"[faers_quarterly] done — {done} ingested, {skipped} skipped.  "
        f"Run `make gold-bulk` to (re)score the full matrix.",
        flush=True,
    )


if __name__ == "__main__":
    main()
