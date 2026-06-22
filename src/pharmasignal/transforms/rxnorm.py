"""RxNorm tiered resolver (WS3 / `future_enhancements.md` #2).

A 10-drug curated YAML cannot normalize ~5,000 ingredients across the thousands of
messy FAERS spellings. This module resolves a cleaned drug string to a stable RxNorm
ingredient (RxCUI) via the public RxNorm REST API, with **every lookup cached to
``bronze/rxnorm/``** so a run is reproducible, offline-replayable, and free after the
first pass (requirements: cache all external API responses in bronze).

Resolution tiers, in order (first hit wins, confidence decreasing):
  1. ``/rxcui?name=`` exact            -> high
  2. ``/approximateTerm``  best match  -> medium
  3. unmatched                          -> none

For a matched RxCUI we then resolve its **ingredient** RxCUI via
``/rxcui/{cui}/related?tty=IN`` so brand/combination variants collapse to one
analysis key. Results (including misses) are memoized to avoid re-querying.

Network is only touched on a cache miss; callers that want a purely offline run
should leave ``PHARMASIGNAL_RXNORM`` unset (see ``normalize.normalize_drug``).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass

import requests

from ..serving import storage

RXNAV_BASE = "https://rxnav.nlm.nih.gov/REST"
_CACHE_URI = None  # resolved lazily so PHARMASIGNAL_DATA_ROOT is honored at call time
_CACHE: dict[str, dict] | None = None
_LOCK = threading.Lock()


@dataclass(frozen=True)
class RxNormMatch:
    rxcui: str | None
    ingredient: str | None        # ingredient name (TTY=IN)
    ingredient_rxcui: str | None
    method: str                   # rxnorm_exact | rxnorm_approximate | rxnorm_unmatched
    confidence: str               # high | medium | none


_UNMATCHED = RxNormMatch(None, None, None, "rxnorm_unmatched", "none")


def _cache_uri() -> str:
    global _CACHE_URI
    if _CACHE_URI is None:
        _CACHE_URI = storage.bronze_uri("rxnorm", "resolver_cache.json")
    return _CACHE_URI


def _load_cache() -> dict[str, dict]:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = storage.read_json(_cache_uri()) if storage.exists(_cache_uri()) else {}
        except Exception:
            _CACHE = {}
    return _CACHE


def _save_cache() -> None:
    if _CACHE is not None:
        storage.write_json(_CACHE, _cache_uri())


def _get(path: str, params: dict, *, timeout: int = 20) -> dict:
    resp = requests.get(f"{RXNAV_BASE}/{path}", params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _resolve_ingredient(rxcui: str) -> tuple[str | None, str | None]:
    """Return (ingredient_name, ingredient_rxcui) for an RxCUI, or (None, None)."""
    try:
        data = _get(f"rxcui/{rxcui}/related.json", {"tty": "IN"})
    except requests.RequestException:
        return None, None
    groups = data.get("relatedGroup", {}).get("conceptGroup", []) or []
    for grp in groups:
        if grp.get("tty") == "IN":
            props = grp.get("conceptProperties", []) or []
            if props:
                return props[0].get("name"), props[0].get("rxcui")
    return None, None


def resolve(name: str, *, persist: bool = True) -> RxNormMatch:
    """Resolve a (cleaned) drug name to a RxNorm match, caching the result.

    Misses are cached too, so an unresolvable spelling is queried at most once.
    """
    key = (name or "").strip().upper()
    if not key:
        return _UNMATCHED

    with _LOCK:
        cache = _load_cache()
        if key in cache:
            return RxNormMatch(**cache[key])

    match = _query(key)

    with _LOCK:
        cache = _load_cache()
        cache[key] = match.__dict__
        if persist:
            _save_cache()
    return match


def _query(key: str) -> RxNormMatch:
    # Tier 1 — exact name -> RxCUI.
    try:
        data = _get("rxcui.json", {"name": key, "search": "1"})
        ids = data.get("idGroup", {}).get("rxnormId", []) or []
        if ids:
            ing, ing_cui = _resolve_ingredient(ids[0])
            return RxNormMatch(ids[0], ing, ing_cui, "rxnorm_exact", "high")
    except requests.RequestException:
        return _UNMATCHED

    # Tier 2 — approximate match.
    try:
        data = _get("approximateTerm.json", {"term": key, "maxEntries": 1})
        cands = data.get("approximateGroup", {}).get("candidate", []) or []
        if cands:
            rxcui = cands[0].get("rxcui")
            ing, ing_cui = _resolve_ingredient(rxcui) if rxcui else (None, None)
            return RxNormMatch(rxcui, ing, ing_cui, "rxnorm_approximate", "medium")
    except requests.RequestException:
        return _UNMATCHED

    return _UNMATCHED


def reset_cache() -> None:
    """Drop the in-memory cache (mainly for tests)."""
    global _CACHE, _CACHE_URI
    _CACHE = None
    _CACHE_URI = None
