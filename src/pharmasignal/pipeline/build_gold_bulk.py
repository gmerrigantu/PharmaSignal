"""Whole-database signal scoring from silver FAERS via set-based SQL (WS2, Phase 0/1).

This is the architectural pivot described in ``docs/FULL_FAERS_UPGRADE_PLAN.md`` §1:
instead of one openFDA ``count`` call per drug-event pair (impossible at full scale),
we compute the *entire* drug x event signal matrix as a handful of DuckDB ``GROUP BY``
queries over the partitioned ``silver/faers`` tables, then score every pair at once
with the vectorized formulas in :mod:`modeling.signal_scores` and the EBGM/MGPS
estimator in :mod:`modeling.ebgm`.

No network. No per-pair loop. Run after ``faers_quarterly`` has populated silver::

    make ingest-faers QUARTERS="2023q1 2023q2 2023q3 2023q4"
    make gold-bulk

Key steps:
  1. Case-version dedup — keep the latest ``primaryid`` per ``caseid`` and drop caseids
     in the FDA ``deleted_cases`` list (silver step the API path never had).
  2. Marginals — per-drug, per-event, grand-total distinct case counts (3 GROUP BYs).
  3. Co-occurrence — distinct cases per (drug, event) pair (one join + GROUP BY).
  4. Score — assemble the 2x2, run vectorized ROR/PRR/chi-square + EBGM/EB05.
  5. Trend — per-quarter pair counts for the strongest pairs -> emerging_signals.
  6. Publish gold: ``signal_scores`` (the full, unfiltered co-occurring matrix —
     served on demand via DuckDB pushdown) + ``emerging_signals`` + health/quality.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

import pandas as pd

from .. import config
from ..paths import ensure_dirs
from ..quality import checks
from ..serving import storage
from ..serving.lakehouse import write_gold
from . import scoring

# FAERS DRUG.role_code values. Default analysis uses suspect drugs (primary +
# secondary + interacting); concomitant ("C") can be included via env override.
SUSPECT_ROLES = ("PS", "SS", "I")


def _log(msg: str) -> None:
    print(f"[build_gold_bulk] {msg}", flush=True)


def _silver_glob(table: str) -> str:
    """read_parquet glob for a partitioned silver table (local path or s3:// URI)."""
    return f"{storage.data_root()}/silver/faers/{table}/*/*/*.parquet"


def _silver_has(table: str) -> bool:
    """True if any Parquet partition exists for a silver table (local or s3)."""
    import fsspec

    glob = _silver_glob(table)
    fs, _ = fsspec.core.url_to_fs(glob)
    try:
        return bool(fs.glob(glob if storage.is_s3() else glob))
    except Exception:
        return False


def _connect_duckdb():
    """File-backed DuckDB connection with laptop-safe resource limits.

    We use a persistent database file so DuckDB's buffer pool can spill pages
    to disk when RAM fills up — essential for the full-FAERS co-occurrence join
    (~17 M cases) on an 8 GB laptop.  Override via env vars:

        PHARMASIGNAL_DUCKDB_MEMORY_LIMIT  (default: "3gb"  — buffer pool size)
        PHARMASIGNAL_DUCKDB_THREADS       (default: 2)
        PHARMASIGNAL_DUCKDB_WORK_DB       (default: data/duckdb_tmp/working.duckdb)

    The working.duckdb file is deleted on success by the caller.
    """
    import duckdb
    from ..paths import LOCAL_DATA_ROOT

    tmp_dir = LOCAL_DATA_ROOT / "duckdb_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    work_db = os.getenv(
        "PHARMASIGNAL_DUCKDB_WORK_DB",
        str(tmp_dir / "working.duckdb"),
    )

    # Remove stale working db from a previous failed run so we start fresh.
    if os.path.exists(work_db):
        os.remove(work_db)

    con = duckdb.connect(work_db)

    mem_limit = os.getenv("PHARMASIGNAL_DUCKDB_MEMORY_LIMIT", "3gb")
    n_threads = int(os.getenv("PHARMASIGNAL_DUCKDB_THREADS") or 2)

    con.execute(f"SET memory_limit='{mem_limit}';")
    con.execute(f"SET threads={n_threads};")
    con.execute("SET preserve_insertion_order=false;")
    _log(f"DuckDB: memory_limit={mem_limit}, threads={n_threads}, db={work_db}")

    if storage.is_s3():
        con.execute("INSTALL httpfs; LOAD httpfs;")
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        con.execute(f"SET s3_region='{region}';")
        # Credentials come from the standard env chain (same as s3fs elsewhere).
        if os.getenv("AWS_ACCESS_KEY_ID"):
            con.execute(f"SET s3_access_key_id='{os.environ['AWS_ACCESS_KEY_ID']}';")
            con.execute(f"SET s3_secret_access_key='{os.environ['AWS_SECRET_ACCESS_KEY']}';")
        if os.getenv("AWS_SESSION_TOKEN"):
            con.execute(f"SET s3_session_token='{os.environ['AWS_SESSION_TOKEN']}';")
    return con


def _roles_sql() -> str:
    roles = os.getenv("PHARMASIGNAL_DRUG_ROLES")
    if roles:
        wanted = tuple(r.strip().upper() for r in roles.split(",") if r.strip())
    else:
        wanted = SUSPECT_ROLES
    quoted = ", ".join(f"'{r}'" for r in wanted)
    return f"UPPER(role_code) IN ({quoted})"


def _build_case_tables(con) -> None:
    """Materialize deduped case-level tables into the file-backed DuckDB database.

    Writing physical tables to the on-disk DB file (rather than lazy TEMP VIEWs)
    lets the subsequent pair-count join stream from disk instead of re-scanning all
    parquet in RAM — essential for the full-FAERS co-occurrence join on 8 GB laptops.

    After this runs the connection has tables: ``latest_case``, ``case_drug``,
    ``case_reaction`` (and optionally ``serious_case``).
    """
    reports = _silver_glob("reports")
    drugs = _silver_glob("drugs")
    reactions = _silver_glob("reactions")
    deleted = _silver_glob("deleted_cases")

    deleted_clause = (
        f"WHERE CAST(caseid AS VARCHAR) NOT IN "
        f"(SELECT CAST(caseid AS VARCHAR) FROM read_parquet('{deleted}'))"
        if _silver_has("deleted_cases") else ""
    )

    _log("materializing latest_case ...")
    con.execute(f"""
        CREATE TABLE latest_case AS
        WITH ranked AS (
            SELECT
                CAST(caseid AS VARCHAR)    AS caseid,
                CAST(primaryid AS VARCHAR) AS primaryid,
                ROW_NUMBER() OVER (
                    PARTITION BY CAST(caseid AS VARCHAR)
                    ORDER BY fda_date DESC NULLS LAST,
                             TRY_CAST(case_version AS INTEGER) DESC NULLS LAST,
                             CAST(primaryid AS VARCHAR) DESC
                ) AS rn
            FROM read_parquet('{reports}')
            {deleted_clause}
        )
        SELECT caseid, primaryid FROM ranked WHERE rn = 1
    """)

    _log("materializing case_drug ...")
    con.execute(f"""
        CREATE TABLE case_drug AS
        SELECT DISTINCT lc.primaryid,
               COALESCE(NULLIF(d.drug_name_normalized, ''), d.drug_name_raw) AS drug,
               ANY_VALUE(d.drug_class) AS drug_class
        FROM read_parquet('{drugs}') d
        JOIN latest_case lc ON CAST(d.primaryid AS VARCHAR) = lc.primaryid
        WHERE {_roles_sql()}
          AND COALESCE(NULLIF(d.drug_name_normalized, ''), d.drug_name_raw) IS NOT NULL
        GROUP BY lc.primaryid, drug
    """)

    _log("materializing case_reaction ...")
    con.execute(f"""
        CREATE TABLE case_reaction AS
        SELECT DISTINCT lc.primaryid,
               COALESCE(NULLIF(r.reaction_term_normalized, ''), r.reaction_term) AS event
        FROM read_parquet('{reactions}') r
        JOIN latest_case lc ON CAST(r.primaryid AS VARCHAR) = lc.primaryid
        WHERE COALESCE(NULLIF(r.reaction_term_normalized, ''), r.reaction_term) IS NOT NULL
    """)


def _score_matrix(con, *, minimum_reports: int) -> tuple[pd.DataFrame, int]:
    """Return the fully-scored co-occurring drug-event matrix and grand total N.

    The pair-count join (case_drug × case_reaction on primaryid) is too large to
    fit in the 2-4 GB available on an 8 GB laptop.  We process it in N chunks
    keyed by ``hash(primaryid) % N`` so each chunk handles 1/N of cases.
    Because case_drug and case_reaction are already deduped on (primaryid, drug) /
    (primaryid, event), COUNT(primaryid) == COUNT(DISTINCT primaryid) everywhere.
    """
    n_cases = con.execute("SELECT COUNT(*) FROM latest_case").fetchone()[0]
    if not n_cases:
        return pd.DataFrame(), 0

    n_chunks = int(os.getenv("PHARMASIGNAL_PAIR_CHUNKS", "10"))

    # Seriousness flag — materialize once so chunks can join cheaply.
    has_serious = _silver_has("outcomes")
    if has_serious:
        con.execute(
            "CREATE TABLE IF NOT EXISTS serious_case AS "
            "SELECT DISTINCT CAST(primaryid AS VARCHAR) AS primaryid "
            "FROM read_parquet('%s')" % _silver_glob("outcomes"))

    # ---- marginals (cheap scalar GROUP BYs on already-materialized tables) ---- #
    _log("computing marginals ...")
    con.execute("""
        CREATE TABLE drug_marginal AS
        SELECT drug, ANY_VALUE(drug_class) AS drug_class, COUNT(primaryid) AS drug_total
        FROM case_drug GROUP BY drug
    """)
    con.execute("""
        CREATE TABLE event_marginal AS
        SELECT event, COUNT(primaryid) AS event_total
        FROM case_reaction GROUP BY event
    """)

    # ---- chunked pair counts -------------------------------------------------- #
    serious_join = (
        "LEFT JOIN serious_case s ON cd.primaryid = s.primaryid" if has_serious else ""
    )
    serious_col = (
        ", COUNT(CASE WHEN s.primaryid IS NOT NULL THEN cd.primaryid END) AS serious_a"
        if has_serious else ""
    )

    con.execute(
        f"CREATE TABLE pair_staging (drug VARCHAR, event VARCHAR, a BIGINT{', serious_a BIGINT' if has_serious else ''})"
    )
    for k in range(n_chunks):
        _log(f"pair chunk {k+1}/{n_chunks} ...")
        con.execute(f"""
            INSERT INTO pair_staging
            SELECT cd.drug, cr.event, COUNT(cd.primaryid) AS a{serious_col}
            FROM case_drug cd
            JOIN case_reaction cr ON cd.primaryid = cr.primaryid
            {serious_join}
            WHERE hash(cd.primaryid) % {n_chunks} = {k}
            GROUP BY cd.drug, cr.event
        """)

    _log("aggregating pair chunks ...")
    con.execute(f"""
        CREATE TABLE pair_counts AS
        SELECT drug, event, SUM(a) AS a{', SUM(serious_a) AS serious_a' if has_serious else ''}
        FROM pair_staging GROUP BY drug, event
    """)

    extra_col = ", pc.serious_a" if has_serious else ""
    pairs = con.execute(f"""
        SELECT pc.drug AS drug_name_normalized,
               dm.drug_class,
               pc.event AS adverse_event,
               pc.a,
               dm.drug_total,
               em.event_total{extra_col}
        FROM pair_counts pc
        JOIN drug_marginal dm ON pc.drug = dm.drug
        JOIN event_marginal em ON pc.event = em.event
    """).fetchdf()

    if pairs.empty:
        return pairs, int(n_cases)

    out = scoring.score_pairs(pairs, int(n_cases), config.load_thresholds())
    return out, int(n_cases)


def _quarterly_trend(con) -> pd.DataFrame:
    """Per-quarter case counts for every (drug, event) pair.

    Reads faers_quarter from reports silver (already materialized in case_drug/
    case_reaction), joining back on primaryid.  Uses COUNT not COUNT DISTINCT
    because the case tables are already deduped.
    """
    # Materialize a quarter lookup from reports silver (primaryid → faers_quarter).
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS case_quarter AS
        SELECT DISTINCT lc.primaryid, r.faers_quarter
        FROM latest_case lc
        JOIN read_parquet('{_silver_glob("reports")}') r
          ON CAST(r.primaryid AS VARCHAR) = lc.primaryid
    """)
    return con.execute("""
        SELECT cd.drug AS drug_name_normalized,
               cr.event AS adverse_event,
               q.faers_quarter,
               COUNT(cd.primaryid) AS report_count
        FROM case_drug cd
        JOIN case_reaction cr ON cd.primaryid = cr.primaryid
        JOIN case_quarter q ON cd.primaryid = q.primaryid
        GROUP BY 1, 2, 3
    """).fetchdf()


def build(*, trend_top_k: int | None = None) -> dict:
    if trend_top_k is None:
        trend_top_k = int(os.getenv("PHARMASIGNAL_TREND_TOP_K", "200"))
    ensure_dirs()
    thresholds = config.load_thresholds()
    weights = config.load_priority_weights()
    started = time.time()

    _log(f"reading silver from {storage.data_root()}/silver/faers")
    for required in ("reports", "drugs", "reactions"):
        if not _silver_has(required):
            raise SystemExit(
                f"silver/faers/{required} not found under {storage.data_root()}. "
                "Run `make ingest-faers QUARTERS=...` first."
            )
    con = _connect_duckdb()
    _build_case_tables(con)

    scores_all, n_cases = _score_matrix(con, minimum_reports=thresholds.minimum_reports)
    _log(f"N (deduped cases) = {n_cases:,}; co-occurring pairs = {len(scores_all):,}")

    if scores_all.empty:
        raise SystemExit(
            "No co-occurring pairs found. Did you run `make ingest-faers` first?"
        )

    # signal_scores IS the full, unfiltered co-occurring matrix — no display cap. The
    # API serves any slice of it on demand via DuckDB-over-S3 pushdown.
    scores_df = scores_all
    _log(f"signal_scores rows = {len(scores_df):,} (full matrix, unfiltered)")

    # ------------------------------------------------------------------ #
    # Emerging signals — quarterly trend for the strongest pairs by EB05.
    # This is a curated priority queue (ranked top-K), not a cap on the data above.
    # ------------------------------------------------------------------ #
    trend = _quarterly_trend(con)
    emerging_df = scoring.emerging_signals(trend, scores_df, thresholds, weights,
                                           top_k=trend_top_k)

    # Publish gold.
    _log("writing gold tables")
    write_gold(scores_df, "signal_scores")
    write_gold(scoring.scatter_sample(scores_df), "signal_scores_sample")
    write_gold(scoring.summary_stats(scores_df), "signal_scores_stats")
    write_gold(scoring.drug_facets(scores_df), "signal_drugs")
    write_gold(scoring.event_facets(scores_df), "signal_events")
    write_gold(emerging_df, "emerging_signals")

    check_results = checks.check_signal_scores(scores_df)
    summary = checks.summarize(check_results)
    health = pd.DataFrame([{
        "run_id": str(uuid.uuid4()),
        "run_timestamp": datetime.now(timezone.utc),
        "source": "faers_quarterly_silver",
        "source_period": _ingested_quarters(con),
        "status": "success" if summary["failed_checks"] == 0 else "failed",
        "rows_raw": n_cases,
        "rows_silver": n_cases,
        "rows_gold": len(scores_df),
        "failed_checks": summary["failed_checks"],
        "warning_checks": summary["warning_checks"],
        "duration_seconds": round(time.time() - started, 1),
        "estimated_cost_usd": 0.0,  # local DuckDB compute; see docs/cost_estimate.md
        "git_commit": None,
        "notes": "Bulk SQL build over silver FAERS (whole-database set-based scoring).",
    }])
    write_gold(health, "pipeline_health")
    write_gold(pd.DataFrame([c.__dict__ for c in check_results]), "data_quality_checks")

    con.close()

    return {
        "cases": n_cases,
        "pairs_all": len(scores_all),
        "pairs_served": len(scores_df),
        "emerging": len(emerging_df),
        "flagged": int(scores_df["disproportionality_flag"].sum()),
    }


def _ingested_quarters(con) -> str:
    try:
        df = con.execute(
            "SELECT DISTINCT faers_quarter FROM read_parquet('%s') ORDER BY 1"
            % _silver_glob("reports")
        ).fetchdf()
        qs = [q for q in df["faers_quarter"].tolist() if q]
        return f"{qs[0]}..{qs[-1]}" if qs else "unknown"
    except Exception:
        return "unknown"


def main() -> None:
    summary = build()
    print(f"Bulk gold build complete: {summary}")


if __name__ == "__main__":
    main()
