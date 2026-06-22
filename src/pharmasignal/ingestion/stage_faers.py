"""Stage FAERS quarterly ZIPs into bronze as raw ASCII (Spark-readable).

The PySpark ingest job can't read ``$``-delimited tables from *inside* a ZIP, so this
step downloads each quarterly extract and writes the individual ASCII ``.txt`` tables
to ``bronze/faers/year=/quarter=/ascii/`` via the storage abstraction (local **or**
``s3://``). It is deliberately light (network + unzip only), so it costs ~$0 to run from
GitHub Actions or a laptop — no Spark cluster needed for staging.

Downloading the full history is tens of GB of transfer; ingress to S3 is free, and we
write only the extracted ASCII (a few GB), not the giant ZIPs, by default.

Run::

    python -m pharmasignal.ingestion.stage_faers 2004q1..2025q1
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

import requests

from ..serving import storage
from .faers_quarterly import FAERS_BASE, FILE_TEMPLATE, QuarterRef, expand_quarters

# Tables we extract to bronze. THER/RPSR added by the full-FAERS upgrade (WS1).
STAGE_TABLES = ("DEMO", "DRUG", "REAC", "OUTC", "INDI", "THER", "RPSR")


def _ascii_dir(ref: QuarterRef) -> str:
    return storage.bronze_uri("faers", f"year={ref.year}", f"quarter=Q{ref.quarter}", "ascii")


def _member_for_table(zf: zipfile.ZipFile, table: str, ref: QuarterRef) -> str | None:
    yy = str(ref.year)[2:]
    candidates = [
        f"ASCII/{table}{yy}Q{ref.quarter}.txt",
        f"ascii/{table}{yy}Q{ref.quarter}.txt",
        f"{table}{yy}Q{ref.quarter}.txt",
    ]
    for n in candidates:
        if n in zf.namelist():
            return n
    return next(
        (n for n in zf.namelist()
         if table.lower() in n.lower() and n.lower().endswith(".txt")),
        None,
    )


def _fetch_zip(ref: QuarterRef, *, timeout: int = 300) -> bytes:
    url = f"{FAERS_BASE}/{FILE_TEMPLATE.format(year=ref.year, q=ref.quarter)}"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def stage_quarter(ref: QuarterRef, *, keep_zip: bool = False) -> dict:
    """Download one quarter and write its ASCII tables (+ DELETED list) to bronze."""
    raw = _fetch_zip(ref)
    ascii_dir = _ascii_dir(ref)
    written: dict[str, int] = {}

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for table in STAGE_TABLES:
            member = _member_for_table(zf, table, ref)
            if member is None:
                continue
            data = zf.read(member)
            storage.write_bytes(data, f"{ascii_dir}/{table}.txt")
            written[table] = len(data)

        # DELETED cases: concatenate every deleted-* member into one file.
        deleted_members = [
            n for n in zf.namelist()
            if "delet" in n.lower() and n.lower().endswith(".txt")
        ]
        if deleted_members:
            blob = b"\n".join(zf.read(n) for n in deleted_members)
            storage.write_bytes(blob, f"{ascii_dir}/DELETED.txt")
            written["DELETED"] = len(blob)

    if keep_zip:
        storage.write_bytes(
            raw, f"{storage.bronze_uri('faers', f'year={ref.year}', f'quarter=Q{ref.quarter}')}"
                 f"/faers_ascii_{ref.label}.zip")

    storage.write_json(
        {
            "quarter": ref.label,
            "source_file": FILE_TEMPLATE.format(year=ref.year, q=ref.quarter),
            "zip_size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "tables_staged": written,
            "staged_at": datetime.now(timezone.utc).isoformat(),
        },
        f"{ascii_dir}/_stage.meta.json",
    )
    return written


def main(argv: list[str] | None = None) -> None:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: python -m pharmasignal.ingestion.stage_faers "
              "<YYYYqQ | YYYYqQ..YYYYqQ> [...]  [--keep-zip]")
        return
    keep_zip = "--keep-zip" in args
    tokens = [a for a in args if not a.startswith("--")]
    for ref in expand_quarters(tokens):
        print(f"Staging FAERS {ref.label} -> {_ascii_dir(ref)} ...", flush=True)
        written = stage_quarter(ref, keep_zip=keep_zip)
        print(f"  -> {json.dumps(written)}", flush=True)


if __name__ == "__main__":
    main()
