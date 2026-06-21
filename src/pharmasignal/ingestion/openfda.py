"""openFDA Drug Adverse Event API client (requirements §7.2).

Used for MVP / prototyping ingestion and on-demand dashboard counts. Every raw
response is cached to the bronze zone keyed by a query hash, preserving source URL
and ingestion timestamp for traceability (requirements §3, §5.3).

For production-scale ingestion use the quarterly extract files instead
(see faers_quarterly.py) to avoid API rate limits.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date, datetime, timezone
from typing import Any

import requests

from ..serving import storage

BASE_URL = "https://api.fda.gov/drug/event.json"
DRUG_FIELD = "patient.drug.medicinalproduct.exact"
EVENT_FIELD = "patient.reaction.reactionmeddrapt.exact"

# Optional key raises the rate limit. Never hard-code real keys in committed code;
# set OPENFDA_API_KEY in the environment. The literal below is only a placeholder.
DEFAULT_API_KEY = os.getenv("OPENFDA_API_KEY", "")


class OpenFDAError(RuntimeError):
    pass


class OpenFDABadQuery(OpenFDAError):
    """A 400 from openFDA — the query for this term is malformed/unsupported.

    Raised immediately (no retry) so callers can skip the offending term rather than
    failing the whole pipeline. Some MedDRA terms contain characters that the openFDA
    query parser rejects (e.g. apostrophes in "FOURNIER'S GANGRENE")."""


def _query_hash(params: dict[str, Any]) -> str:
    blob = json.dumps(params, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _bronze_uri(params: dict[str, Any]) -> str:
    """Bronze cache location (local path or s3://) for a query, keyed by hash."""
    day = date.today().isoformat()
    return storage.bronze_uri("openfda", f"date={day}", f"query_{_query_hash(params)}.json")


def quote_term(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def exact_clause(field: str, term: str) -> str:
    return f"{field}:{quote_term(term)}"


def and_query(clauses: list[str]) -> str:
    return " AND ".join(f"({c})" for c in clauses)


def date_range_clause(start: str, end: str) -> str:
    """receivedate range, dates as YYYYMMDD or YYYY-MM-DD."""
    s, e = start.replace("-", ""), end.replace("-", "")
    return f"receivedate:[{s} TO {e}]"


def sex_clause(code: int) -> str:
    """FAERS patient sex: 1 = male, 2 = female (0/blank = unknown)."""
    return f"patient.patientsex:{code}"


def age_clause(low: int, high: int) -> str:
    """Onset-age band in YEARS. Restrict to unit=801 (years) for comparable ages."""
    return and_query([
        "patient.patientonsetageunit:801",
        f"patient.patientonsetage:[{low} TO {high}]",
    ])


def _get(params: dict[str, Any], *, api_key: str, use_cache: bool, retries: int = 3) -> dict:
    clean = {k: v for k, v in params.items() if v not in ("", None)}
    if api_key:
        clean["api_key"] = api_key
    cache_uri = _bronze_uri(clean)
    if use_cache and storage.exists(cache_uri):
        # Bronze envelope wraps the raw payload under "data" (preserves source URL +
        # ingest timestamp for lineage); return the payload itself.
        return storage.read_json(cache_uri).get("data", {})

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(BASE_URL, params=clean, timeout=30)
            if resp.status_code == 404:
                # openFDA returns 404 for "no results" — treat as empty, not error.
                data = {"meta": {"results": {"total": 0}}, "results": []}
            elif resp.status_code == 400:
                # Malformed query for this term — skippable, do not retry.
                raise OpenFDABadQuery(f"openFDA 400 for query: {clean.get('search', '')}")
            else:
                resp.raise_for_status()
                data = resp.json()
            storage.write_json(
                {
                    "_source_url": resp.url,
                    "_ingest_timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": data,
                },
                cache_uri,
            )
            return data
        except requests.RequestException as exc:  # noqa: PERF203
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise OpenFDAError(f"openFDA request failed after {retries} attempts: {last_exc}")


# --------------------------------------------------------------------------- #
# High-level helpers
# --------------------------------------------------------------------------- #
def total_reports(*, api_key: str = DEFAULT_API_KEY, use_cache: bool = True) -> int:
    data = _get({"limit": 1}, api_key=api_key, use_cache=use_cache)
    return int(data.get("meta", {}).get("results", {}).get("total", 0))


def count(search: str, *, api_key: str = DEFAULT_API_KEY, use_cache: bool = True) -> int:
    """Total report count matching a search expression."""
    data = _get({"search": search, "limit": 1}, api_key=api_key, use_cache=use_cache)
    return int(data.get("meta", {}).get("results", {}).get("total", 0))


def count_field(
    search: str,
    field: str,
    limit: int = 100,
    *,
    api_key: str = DEFAULT_API_KEY,
    use_cache: bool = True,
) -> list[dict]:
    """Ranked term counts (e.g. top reaction terms for a drug). Returns rows of
    ``{"term": str, "count": int}``."""
    data = _get(
        {"search": search, "count": field, "limit": limit},
        api_key=api_key,
        use_cache=use_cache,
    )
    return data.get("results", [])


def drug_clause(canonical_or_alias: str) -> str:
    return exact_clause(DRUG_FIELD, canonical_or_alias.upper())


def event_clause(event: str) -> str:
    return exact_clause(EVENT_FIELD, event.upper())
