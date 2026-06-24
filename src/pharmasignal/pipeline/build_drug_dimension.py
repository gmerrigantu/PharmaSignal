"""Build ``gold_drug_dimension`` — the raw-name → RxNorm ingredient map (Option B).

The whole-database silver layer has ~130k *distinct* normalized drug strings, mostly
brand/dose/spelling variants of a few thousand actual ingredients. This module resolves
each distinct name to its RxNorm **ingredient** (TTY=IN) once, caching every lookup to
``bronze/rxnorm/`` (so a run is resumable and free after the first pass), and writes a
small ``drug_dimension`` mart:

    drug_name_normalized  — the current silver analysis key (what gold is keyed on today)
    analysis_key          — COALESCE(ingredient, drug_name_normalized): the NEW key
    ingredient            — RxNorm ingredient name (uppercased), or None
    ingredient_rxcui      — RxNorm ingredient RxCUI (for exact openFDA label joins)
    rxnorm_method         — rxnorm_exact | rxnorm_approximate | rxnorm_unmatched
    rxnorm_confidence     — high | medium | none
    drug_class_atc        — ATC level-1 main group name (coarse class), or None
    report_count          — distinct-case frequency (drives resolution ordering)

``build_gold_bulk`` / ``build_gold_spark`` join this mart and group by ``analysis_key``
so the disproportionality matrix re-aggregates at ingredient level (brand fragmentation
no longer dilutes signals). Names that don't resolve keep their cleaned string as the
key — no rows are dropped.

Run: ``python -m pharmasignal.pipeline.build_drug_dimension`` (needs network on a cold
cache). ``--limit N`` resolves only the top-N most-frequent names (the rest pass through
unresolved); ``--no-atc`` skips the ATC class fill.
"""
from __future__ import annotations

import argparse
import os
import time

import pandas as pd

from ..serving import storage
from ..serving.lakehouse import write_gold
from ..transforms import rxnorm


def _log(msg: str) -> None:
    print(f"[build_drug_dimension] {msg}", flush=True)


def _silver_glob(table: str) -> str:
    return f"{storage.data_root()}/silver/faers/{table}/*/*/*.parquet"


def distinct_drug_names() -> pd.DataFrame:
    """Distinct silver drug analysis keys with a distinct-case frequency, busiest first.

    Mirrors the key ``build_gold_bulk`` uses today (normalized, falling back to raw) so the
    dimension covers exactly the names that appear in the matrix.
    """
    import duckdb

    glob = _silver_glob("drugs")
    con = duckdb.connect()
    try:
        if storage.is_s3():
            con.execute("INSTALL httpfs; LOAD httpfs; "
                        "SET s3_region='us-east-1'; "
                        "CREATE SECRET (TYPE s3, PROVIDER credential_chain);")
        return con.execute(
            f"""
            SELECT COALESCE(NULLIF(drug_name_normalized, ''), drug_name_raw) AS drug_name_normalized,
                   COUNT(DISTINCT primaryid) AS report_count
            FROM read_parquet('{glob}')
            WHERE COALESCE(NULLIF(drug_name_normalized, ''), drug_name_raw) IS NOT NULL
            GROUP BY 1
            ORDER BY report_count DESC
            """
        ).fetchdf()
    finally:
        con.close()


def build(*, limit: int | None = None, with_atc: bool = True,
          rate_per_sec: float = 18.0) -> pd.DataFrame:
    """Resolve distinct drug names to RxNorm ingredients and return the dimension frame.

    ``rate_per_sec`` throttles cache-miss HTTP calls to stay under RxNav's guidance
    (~20 req/s). Cached lookups are not throttled, so re-runs are fast.
    """
    names = distinct_drug_names()
    _log(f"{len(names):,} distinct silver drug names")
    if limit is not None:
        names = names.head(int(limit))
        _log(f"resolving top {len(names):,} by report frequency (rest pass through unresolved)")

    min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
    rows: list[dict] = []
    resolved = 0
    for i, r in enumerate(names.itertuples(index=False), 1):
        name = r.drug_name_normalized
        t0 = time.monotonic()
        match = rxnorm.resolve(name)
        ingredient = match.ingredient.upper() if match.ingredient else None
        rxcui = match.ingredient_rxcui or match.rxcui
        atc_name = None
        if with_atc and rxcui:
            _, atc_name = rxnorm.atc_class(rxcui)
        if ingredient:
            resolved += 1
        rows.append({
            "drug_name_normalized": name,
            "analysis_key": ingredient or name,
            "ingredient": ingredient,
            "ingredient_rxcui": rxcui,
            "rxnorm_method": match.method,
            "rxnorm_confidence": match.confidence,
            "drug_class_atc": atc_name,
            "report_count": int(r.report_count),
        })
        # Throttle only when we actually hit the network (a miss is slower than the floor).
        elapsed = time.monotonic() - t0
        if elapsed > 0.002 and elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        if i % 500 == 0:
            _log(f"  {i:,}/{len(names):,} ({resolved:,} resolved to an ingredient)")

    df = pd.DataFrame(rows)
    _log(f"resolved {resolved:,}/{len(df):,} names to an ingredient "
         f"({df['analysis_key'].nunique():,} distinct analysis keys)")
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None,
                    help="resolve only the top-N most-frequent names")
    ap.add_argument("--no-atc", action="store_true", help="skip ATC class fill")
    args = ap.parse_args()

    df = build(limit=args.limit, with_atc=not args.no_atc)
    path = write_gold(df, "drug_dimension")
    method_counts = df["rxnorm_method"].value_counts().to_dict()
    _log(f"wrote {len(df):,} rows -> {path}")
    _log(f"  method breakdown: {method_counts}")
    collapse = len(df) - df["analysis_key"].nunique()
    _log(f"  ingredient collapse: {len(df):,} names -> {df['analysis_key'].nunique():,} keys "
         f"({collapse:,} names merged)")


if __name__ == "__main__":
    main()
