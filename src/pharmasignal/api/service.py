"""Data-assembly layer for the serving API.

Reads gold tables through the existing ``serving.lakehouse`` abstraction (so the same
code serves local Parquet, the bundled demo dataset, or S3 — selected by
``PHARMASIGNAL_DATA_ROOT``) and shapes them into JSON-safe payloads.

Two concerns handled here that the raw DataFrames don't give us for free:
  * **JSON safety** — pandas NaN/NaT/Inf and numpy scalar types are coerced to valid
    JSON (``null`` / native types) via ``df.to_json``.
  * **Warm-container caching** — on AWS Lambda the module stays resident between
    invocations, so we memoize the (tiny) gold tables for ``PHARMASIGNAL_CACHE_TTL``
    seconds to avoid re-reading S3 on every request. Gold only changes when the
    pipeline runs, so a 5-minute default is generous.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import pandas as pd

from ..serving import lakehouse

# Columns exposed per table for the dashboard payload. Kept in lock-step with the
# frontend's lib/types.ts so the response is the documented contract, not an
# accidental dump of internal modeling columns. Extra gold columns are dropped.
_DASHBOARD_COLUMNS: dict[str, list[str]] = {
    "signal_scores": [
        "drug_name_normalized", "adverse_event", "drug_class", "a_drug_event",
        "ror", "ror_ci_lower", "ror_ci_upper", "prr", "chi_square",
        "seriousness_rate", "bayesian_shrunken_score", "disproportionality_flag",
    ],
    "emerging_signals": [
        "drug_name_normalized", "adverse_event", "drug_class", "current_count",
        "trailing_baseline_count", "percent_change", "anomaly_score",
        "seriousness_rate", "priority_score", "priority_level",
        "literature_support_count", "nhanes_context_available", "current_quarter",
    ],
    "nhanes_population_context": [
        "medication_name_normalized", "drug_class", "weighted_prevalence",
        "unweighted_sample_count", "median_age", "female_percent",
        "bmi_ge_30_percent", "diabetes_percent", "hba1c_median",
        "small_n_flag", "very_small_n_flag", "survey_cycle", "weight_variable_used",
    ],
    "pubmed_evidence": [
        "drug_name_normalized", "adverse_event", "title", "journal",
        "publication_year", "pmid", "relevance_score", "url", "evidence_snippet",
    ],
    "pipeline_health": [
        "run_id", "source", "source_period", "run_timestamp", "status",
        "rows_raw", "rows_silver", "rows_gold", "failed_checks", "warning_checks",
        "estimated_cost_usd", "notes",
    ],
    "data_quality_checks": ["table", "check", "category", "status", "detail"],
}

# Advanced marts — present on the cloud lakehouse, may be absent from a minimal
# lakehouse. Surfaced in the dashboard payload and via dedicated endpoints; both
# degrade gracefully to ``[]``. Kept in lock-step with the frontend's lib/types.ts.
_OPTIONAL_COLUMNS: dict[str, list[str]] = {
    "interaction_signals": [
        "drug_a", "drug_b", "adverse_event", "co_reports", "pair_event_reports",
        "ror_combination", "ror_ci_lower", "ror_ci_upper", "prr_combination",
        "chi_square", "ror_drug_a", "ror_drug_b", "single_max_ror", "comparable",
        "interaction_ratio", "interaction_flag",
    ],
    "subgroup_signals": [
        "drug_name_normalized", "drug_class", "adverse_event", "subgroup_type",
        "subgroup", "stratum_reports", "stratum_population", "ror", "ror_ci_lower",
        "ror_ci_upper", "prr", "chi_square", "overall_ror",
    ],
    "drug_label_flags": [
        "drug_name_normalized", "adverse_event", "labeled_event", "label_section",
        "label_found", "label_status", "novel_flag",
    ],
}
_OPTIONAL_TABLES = tuple(_OPTIONAL_COLUMNS)

_CACHE_TTL = float(os.getenv("PHARMASIGNAL_CACHE_TTL", "300"))
_cache: dict[str, tuple[float, pd.DataFrame | None]] = {}

# Default / max page size for the paginated /signals endpoint. The full signal_scores
# matrix is unbounded (831k+ for 2024, 10^7+ for full history) — it is reached page by
# page via DuckDB-over-S3 pushdown, never shipped in one response.
_DEFAULT_PAGE = int(os.getenv("PHARMASIGNAL_SIGNALS_PAGE", "100"))
_MAX_PAGE = int(os.getenv("PHARMASIGNAL_SIGNALS_MAX_PAGE", "1000"))
# Bounded sample of the full matrix embedded in /dashboard/summary for the scatter plot
# (rendering 831k SVG points is infeasible). Top-by-ROR, not a data cap.
_SCATTER_SAMPLE = int(os.getenv("PHARMASIGNAL_SCATTER_SAMPLE", "3000"))

# Sort keys the API will honor on /signals (whitelist — never interpolate raw input).
_SORTABLE = {
    "ror", "a_drug_event", "prr", "chi_square", "seriousness_rate",
    "bayesian_shrunken_score", "ror_ci_lower", "ror_ci_upper",
}


def _load(name: str) -> pd.DataFrame | None:
    """Read a gold table (with TTL memoization). Returns None if it does not exist."""
    now = time.monotonic()
    hit = _cache.get(name)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return hit[1]
    df = lakehouse.read_gold(name) if lakehouse.gold_exists(name) else None
    _cache[name] = (now, df)
    return df


def clear_cache() -> None:
    _cache.clear()


def _records(df: pd.DataFrame | None, columns: list[str] | None = None) -> list[dict]:
    """DataFrame -> list of JSON-safe dicts (NaN/NaT/Inf -> null, numpy -> native)."""
    if df is None or df.empty:
        return []
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    # to_json handles NaN->null, numpy scalars, and ISO-formats any datetimes.
    return json.loads(df.to_json(orient="records", date_format="iso"))


def data_source() -> str:
    src = lakehouse.active_source()
    return "demo" if src == "none" else src


# --------------------------------------------------------------------------- #
# Endpoint payloads
# --------------------------------------------------------------------------- #
def dashboard_summary() -> dict:
    """The single payload the Next.js app fetches (GET /dashboard/summary).

    Matches frontend ``DashboardData``: the six tables the dashboard renders plus
    provenance fields so the UI can show where the data came from and when.
    """
    summary = signals_summary()
    # Every mart EXCEPT signal_scores is small enough to embed whole. signal_scores is the
    # full unfiltered matrix — we embed only aggregates + a bounded scatter sample, and the
    # table pages against /signals. This keeps the payload well under the ~6 MB limit.
    tables = {name: _records(_load(name), cols)
              for name, cols in _DASHBOARD_COLUMNS.items() if name != "signal_scores"}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": data_source(),
        "signal_total": summary["signal_total"],
        "flagged_total": summary["flagged_total"],
        "novel_total": summary["novel_total"],
        "signal_sample": summary["signal_sample"],
        **tables,
        # Advanced marts embedded for the frontend (empty list when not materialized).
        **{name: _records(_load(name), cols) for name, cols in _OPTIONAL_COLUMNS.items()},
    }


def _filter(df: pd.DataFrame | None, **eq: str | None) -> pd.DataFrame | None:
    if df is None:
        return None
    for col, val in eq.items():
        if val is not None and col in df.columns:
            df = df[df[col].astype(str).str.upper() == str(val).upper()]
    return df


def _quote(path: str) -> str:
    """Escape a source path for safe interpolation into a SQL string literal."""
    return path.replace("'", "''")


def _signals_where(drug: str | None, event: str | None, drug_class: str | None,
                   flagged_only: bool, min_reports: int, q: str | None) -> tuple[str, list]:
    """Build a parameterized WHERE clause (values bound as ``?``, never interpolated)."""
    clauses: list[str] = []
    params: list = []
    for col, val in (("drug_name_normalized", drug), ("adverse_event", event),
                     ("drug_class", drug_class)):
        if val:
            clauses.append(f"upper({col}) = upper(?)")
            params.append(val)
    if q:
        clauses.append("(upper(drug_name_normalized) LIKE upper(?) "
                       "OR upper(adverse_event) LIKE upper(?))")
        params.extend([f"%{q}%", f"%{q}%"])
    if flagged_only:
        clauses.append("disproportionality_flag")
    if min_reports:
        clauses.append("a_drug_event >= ?")
        params.append(int(min_reports))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def signals(*, drug: str | None = None, event: str | None = None,
            drug_class: str | None = None, flagged_only: bool = False,
            min_reports: int = 0, q: str | None = None, sort: str = "ror",
            desc: bool = True, offset: int = 0, limit: int | None = None) -> dict:
    """Paginated slice of the full signal_scores matrix via DuckDB-over-S3 pushdown.

    Returns an envelope ``{total, offset, limit, rows}`` where ``total`` is the count of
    the full filtered set (so the UI shows true totals) and ``rows`` is one page. DuckDB
    pushes the filter/sort/limit into the Parquet scan — the whole matrix is never loaded.
    """
    page = _DEFAULT_PAGE if limit is None else max(1, min(int(limit), _MAX_PAGE))
    offset = max(0, int(offset))
    src = lakehouse.gold_source("signal_scores")
    empty = {"total": 0, "offset": offset, "limit": page, "rows": []}
    if not src:
        return empty

    where, params = _signals_where(drug, event, drug_class, flagged_only, min_reports, q)
    frm = f"read_parquet('{_quote(src)}')"
    sort_col = sort if sort in _SORTABLE else "ror"
    direction = "DESC" if desc else "ASC"

    try:
        total = int(lakehouse.pushdown_query(
            f"SELECT count(*) AS n FROM {frm}{where}", params).iloc[0]["n"])
        cols = ", ".join(_DASHBOARD_COLUMNS["signal_scores"])
        rows = lakehouse.pushdown_query(
            f"SELECT {cols} FROM {frm}{where} "
            f"ORDER BY {sort_col} {direction} NULLS LAST LIMIT {page} OFFSET {offset}",
            params)
    except Exception:  # malformed query / missing column on an older mart -> empty page
        return empty
    return {"total": total, "offset": offset, "limit": page,
            "rows": _records(rows, _DASHBOARD_COLUMNS["signal_scores"])}


def signals_summary() -> dict:
    """Server-computed aggregates over the *full* matrix + a bounded scatter sample.

    Lets the dashboard tiles show true totals and the scatter render without shipping the
    whole matrix. ``novel_total`` comes from the (small) drug_label_flags mart.
    """
    src = lakehouse.gold_source("signal_scores")
    out = {"signal_total": 0, "flagged_total": 0, "novel_total": 0, "signal_sample": []}
    if not src:
        return out
    frm = f"read_parquet('{_quote(src)}')"
    cols = ", ".join(_DASHBOARD_COLUMNS["signal_scores"])
    try:
        # Totals: prefer the precomputed one-row stats mart so the summary scans NOTHING
        # over the full matrix (keeps cold starts under the API-Gateway timeout). Fall back
        # to live COUNTs only if that mart isn't materialized.
        stats_src = lakehouse.gold_source("signal_scores_stats")
        if stats_src:
            st = lakehouse.pushdown_query(
                f"SELECT signal_total, flagged_total FROM read_parquet('{_quote(stats_src)}')").iloc[0]
            out["signal_total"] = int(st["signal_total"])
            out["flagged_total"] = int(st["flagged_total"])
        else:
            agg = lakehouse.pushdown_query(
                f"SELECT count(*) AS total, "
                f"count(*) FILTER (WHERE disproportionality_flag) AS flagged FROM {frm}").iloc[0]
            out["signal_total"] = int(agg["total"])
            out["flagged_total"] = int(agg["flagged"])
        # Scatter sample: prefer the precomputed top-by-ROR mart (tiny, no sort needed);
        # fall back to sorting the full matrix only if that mart isn't materialized.
        sample_src = lakehouse.gold_source("signal_scores_sample")
        if sample_src:
            sample = lakehouse.pushdown_query(
                f"SELECT {cols} FROM read_parquet('{_quote(sample_src)}')")
        else:
            sample = lakehouse.pushdown_query(
                f"SELECT {cols} FROM {frm} ORDER BY ror DESC NULLS LAST LIMIT {_SCATTER_SAMPLE}")
        out["signal_sample"] = _records(sample, _DASHBOARD_COLUMNS["signal_scores"])
    except Exception:
        return out
    labels = _load("drug_label_flags")
    if labels is not None and "novel_flag" in labels.columns:
        out["novel_total"] = int(labels["novel_flag"].fillna(False).astype(bool).sum())
    return out


def emerging(*, priority: str | None = None, limit: int | None = None) -> list[dict]:
    df = _filter(_load("emerging_signals"), priority_level=priority)
    if df is not None:
        if "priority_score" in df.columns:
            df = df.sort_values("priority_score", ascending=False)
        if limit:
            df = df.head(limit)
    return _records(df, _DASHBOARD_COLUMNS["emerging_signals"])


def nhanes() -> list[dict]:
    return _records(_load("nhanes_population_context"),
                    _DASHBOARD_COLUMNS["nhanes_population_context"])


def evidence(*, drug: str | None = None, event: str | None = None) -> list[dict]:
    df = _filter(_load("pubmed_evidence"), drug_name_normalized=drug, adverse_event=event)
    return _records(df, _DASHBOARD_COLUMNS["pubmed_evidence"])


def drug_profile(drug: str) -> dict:
    """Everything we know about one drug, for a drug-detail page."""
    return {
        "drug_name_normalized": drug.upper(),
        "data_source": data_source(),
        "signals": signals(drug=drug, limit=_MAX_PAGE)["rows"],
        "emerging": _records(_filter(_load("emerging_signals"), drug_name_normalized=drug),
                             _DASHBOARD_COLUMNS["emerging_signals"]),
        "nhanes": _records(_filter(_load("nhanes_population_context"),
                                   medication_name_normalized=drug)),
        "evidence": evidence(drug=drug),
    }


def optional_table(name: str, *, drug: str | None = None) -> list[dict]:
    """Serve an advanced mart if it exists on the active lakehouse, else []."""
    if name not in _OPTIONAL_TABLES:
        return []
    df = _load(name)
    if drug is not None and df is not None:
        col = "drug_name_normalized" if "drug_name_normalized" in df.columns else None
        if col:
            df = df[df[col].astype(str).str.upper() == drug.upper()]
    return _records(df, _OPTIONAL_COLUMNS.get(name))


def health() -> dict:
    """Liveness + which tables are visible and their row counts (cheap, cached)."""
    tables: dict[str, int] = {}
    for name in (*_DASHBOARD_COLUMNS, *_OPTIONAL_TABLES):
        if name == "signal_scores":
            continue  # counted via pushdown below — never load the full matrix
        df = _load(name)
        if df is not None:
            tables[name] = int(len(df))
    src = lakehouse.gold_source("signal_scores")
    if src:
        try:
            tables["signal_scores"] = int(lakehouse.pushdown_query(
                f"SELECT count(*) AS n FROM read_parquet('{_quote(src)}')").iloc[0]["n"])
        except Exception:
            pass
    return {"status": "ok", "data_source": data_source(),
            "cache_ttl_seconds": _CACHE_TTL, "tables": tables}
