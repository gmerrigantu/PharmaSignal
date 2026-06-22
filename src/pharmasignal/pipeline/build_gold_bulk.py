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
    """DuckDB connection wired for S3 when the lakehouse root is s3://."""
    import duckdb

    con = duckdb.connect()
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
    """Register deduped case-level drug and reaction relations in ``con``.

    After this runs the connection has temp views: ``latest_case`` (one row per
    surviving caseid/primaryid), ``case_drug`` and ``case_reaction``
    (distinct primaryid x key), plus ``n_cases`` scalar.
    """
    reports = _silver_glob("reports")
    drugs = _silver_glob("drugs")
    reactions = _silver_glob("reactions")
    deleted = _silver_glob("deleted_cases")

    # Deleted-cases table may not exist for every quarter; tolerate its absence.
    deleted_clause = (
        f"WHERE CAST(caseid AS VARCHAR) NOT IN "
        f"(SELECT CAST(caseid AS VARCHAR) FROM read_parquet('{deleted}'))"
        if _silver_has("deleted_cases") else ""
    )

    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW latest_case AS
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

    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW case_drug AS
        SELECT DISTINCT lc.primaryid,
               COALESCE(NULLIF(d.drug_name_normalized, ''), d.drug_name_raw) AS drug,
               ANY_VALUE(d.drug_class) AS drug_class
        FROM read_parquet('{drugs}') d
        JOIN latest_case lc ON CAST(d.primaryid AS VARCHAR) = lc.primaryid
        WHERE {_roles_sql()}
          AND COALESCE(NULLIF(d.drug_name_normalized, ''), d.drug_name_raw) IS NOT NULL
        GROUP BY lc.primaryid, drug
    """)

    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW case_reaction AS
        SELECT DISTINCT lc.primaryid,
               COALESCE(NULLIF(r.reaction_term_normalized, ''), r.reaction_term) AS event
        FROM read_parquet('{reactions}') r
        JOIN latest_case lc ON CAST(r.primaryid AS VARCHAR) = lc.primaryid
        WHERE COALESCE(NULLIF(r.reaction_term_normalized, ''), r.reaction_term) IS NOT NULL
    """)


def _score_matrix(con, *, minimum_reports: int) -> tuple[pd.DataFrame, int]:
    """Return the fully-scored co-occurring drug-event matrix and grand total N."""
    n_cases = con.execute("SELECT COUNT(*) FROM latest_case").fetchone()[0]
    if not n_cases:
        return pd.DataFrame(), 0

    # Seriousness from OUTC (a case is serious if it has any reported outcome). Joined
    # into the pair count as serious_a; scales as a set-based join like everything else.
    has_serious = _silver_has("outcomes")
    if has_serious:
        con.execute(
            "CREATE OR REPLACE TEMP VIEW serious_case AS "
            "SELECT DISTINCT CAST(primaryid AS VARCHAR) AS primaryid "
            "FROM read_parquet('%s')" % _silver_glob("outcomes"))
        pair_select = (
            "SELECT cd.drug, cr.event, COUNT(DISTINCT cd.primaryid) AS a, "
            "COUNT(DISTINCT CASE WHEN s.primaryid IS NOT NULL THEN cd.primaryid END) AS serious_a "
            "FROM case_drug cd JOIN case_reaction cr ON cd.primaryid = cr.primaryid "
            "LEFT JOIN serious_case s ON cd.primaryid = s.primaryid "
            "GROUP BY cd.drug, cr.event")
        extra_col = ", p.serious_a"
    else:
        pair_select = (
            "SELECT cd.drug, cr.event, COUNT(DISTINCT cd.primaryid) AS a "
            "FROM case_drug cd JOIN case_reaction cr ON cd.primaryid = cr.primaryid "
            "GROUP BY cd.drug, cr.event")
        extra_col = ""

    # Marginals (cheap) + co-occurrence (the one heavy join). DuckDB streams these.
    pairs = con.execute(f"""
        WITH drug_tot AS (
            SELECT drug, ANY_VALUE(drug_class) AS drug_class,
                   COUNT(DISTINCT primaryid) AS drug_total
            FROM case_drug GROUP BY drug
        ),
        event_tot AS (
            SELECT event, COUNT(DISTINCT primaryid) AS event_total
            FROM case_reaction GROUP BY event
        ),
        pair AS ({pair_select})
        SELECT p.drug AS drug_name_normalized,
               dt.drug_class,
               p.event AS adverse_event,
               p.a,
               dt.drug_total,
               et.event_total{extra_col}
        FROM pair p
        JOIN drug_tot dt ON p.drug = dt.drug
        JOIN event_tot et ON p.event = et.event
    """).fetchdf()

    if pairs.empty:
        return pairs, int(n_cases)

    out = scoring.score_pairs(pairs, int(n_cases), config.load_thresholds())
    return out, int(n_cases)


def _quarterly_trend(con) -> pd.DataFrame:
    """Per-quarter distinct-case counts for every (drug, event) pair (one GROUP BY)."""
    return con.execute("""
        SELECT cd.drug AS drug_name_normalized,
               cr.event AS adverse_event,
               q.faers_quarter,
               COUNT(DISTINCT cd.primaryid) AS report_count
        FROM case_drug cd
        JOIN case_reaction cr ON cd.primaryid = cr.primaryid
        JOIN (
            SELECT DISTINCT CAST(primaryid AS VARCHAR) AS primaryid, faers_quarter
            FROM read_parquet('%s')
        ) q ON cd.primaryid = q.primaryid
        GROUP BY 1, 2, 3
    """ % _silver_glob("reports")).fetchdf()


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
