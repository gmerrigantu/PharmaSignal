"""RxNorm tiered resolver (WS3 / `future_enhancements.md` #2).

A 10-drug curated YAML cannot normalize ~5,000 ingredients across the thousands of
messy FAERS spellings. This module resolves a cleaned drug string to a stable RxNorm
ingredient (RxCUI) via the public RxNorm REST API, with **every lookup cached to
``bronze/rxnorm/``** so a run is reproducible, offline-replayable, and free after the
first pass (requirements: cache all external API responses in bronze).

Resolution tiers, in order (first hit wins, confidence decreasing):
  1. ``/rxcui?name=&search=0``  EXACT name      -> rxnorm_exact      / high
  2. ``/rxcui?name=&search=1``  normalized name -> rxnorm_normalized / medium
  3. ``/approximateTerm``       fuzzy best match-> rxnorm_approximate/ low
  4. unmatched                                  -> rxnorm_unmatched  / none

For a matched RxCUI we resolve its **ingredient** so brand/dose variants collapse to one
analysis key — but carefully: if the matched concept is *itself* an ingredient (TTY=IN)
we return it directly. Climbing ``/rxcui/{cui}/related?tty=IN`` from an ingredient can
drift to a stereoisomer/salt (e.g. OMEPRAZOLE -> ESOMEPRAZOLE), so we only climb for
non-ingredient concepts (brands, clinical drugs). Results (incl. misses) are memoized.

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
    method: str                   # rxnorm_exact | rxnorm_normalized | rxnorm_approximate | rxnorm_unmatched
    confidence: str               # high | medium | low | none


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


def _properties(rxcui: str) -> dict | None:
    """RxNorm concept properties (name, tty, ...) for an RxCUI, or None."""
    try:
        data = _get(f"rxcui/{rxcui}/properties.json", {})
    except requests.RequestException:
        return None
    return data.get("properties") or None


def _resolve_ingredient(rxcui: str) -> tuple[str | None, str | None]:
    """Return (ingredient_name, ingredient_rxcui) for an RxCUI, or (None, None).

    If the concept is already an ingredient (TTY=IN) we return it verbatim — climbing the
    IN relation from an ingredient can return related stereoisomers/salts and silently
    swap the analysis key (the OMEPRAZOLE -> ESOMEPRAZOLE bug). Only brands / clinical
    drugs are climbed to their base ingredient.
    """
    props = _properties(rxcui)
    if props is not None and (props.get("tty") or "").upper() == "IN":
        return props.get("name"), props.get("rxcui")

    try:
        data = _get(f"rxcui/{rxcui}/related.json", {"tty": "IN"})
    except requests.RequestException:
        return None, None
    ingredients: list[tuple[str | None, str | None]] = []
    for grp in data.get("relatedGroup", {}).get("conceptGroup", []) or []:
        if grp.get("tty") == "IN":
            for p in grp.get("conceptProperties", []) or []:
                if p.get("rxcui"):
                    ingredients.append((p.get("name"), p.get("rxcui")))
    if not ingredients:
        return None, None
    # A single-ingredient brand/drug resolves cleanly. For a multi-ingredient (combination)
    # concept there is no single ingredient — pick the lowest RxCUI so the key is stable
    # and reproducible across runs (combination splitting is a separate, deferred concern).
    ingredients.sort(key=lambda x: int(x[1]) if str(x[1]).isdigit() else 1 << 62)
    return ingredients[0]


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


def _name_to_rxcui(key: str, search: str) -> str | None:
    """First RxCUI for ``key`` under the given search mode ('0' exact, '1' normalized)."""
    data = _get("rxcui.json", {"name": key, "search": search})
    ids = data.get("idGroup", {}).get("rxnormId", []) or []
    return ids[0] if ids else None


def _query(key: str) -> RxNormMatch:
    try:
        # Tier 1 — EXACT name match (search=0): the precise concept, no normalization
        # drift. This is what keeps OMEPRAZOLE on omeprazole.
        rxcui = _name_to_rxcui(key, "0")
        if rxcui:
            ing, ing_cui = _resolve_ingredient(rxcui)
            return RxNormMatch(rxcui, ing, ing_cui, "rxnorm_exact", "high")

        # Tier 2 — normalized name match (search=1): tolerant of spacing/case/word order,
        # but fuzzier, so only after an exact miss and at lower confidence.
        rxcui = _name_to_rxcui(key, "1")
        if rxcui:
            ing, ing_cui = _resolve_ingredient(rxcui)
            return RxNormMatch(rxcui, ing, ing_cui, "rxnorm_normalized", "medium")
    except requests.RequestException:
        return _UNMATCHED

    # Tier 3 — approximate fuzzy match (typos / partial strings).
    try:
        data = _get("approximateTerm.json", {"term": key, "maxEntries": 1})
        cands = data.get("approximateGroup", {}).get("candidate", []) or []
        if cands:
            rxcui = cands[0].get("rxcui")
            ing, ing_cui = _resolve_ingredient(rxcui) if rxcui else (None, None)
            return RxNormMatch(rxcui, ing, ing_cui, "rxnorm_approximate", "low")
    except requests.RequestException:
        return _UNMATCHED

    return _UNMATCHED


_ATC_CACHE_URI = None
_ATC_CACHE: dict[str, dict] | None = None


def _atc_cache_uri() -> str:
    global _ATC_CACHE_URI
    if _ATC_CACHE_URI is None:
        _ATC_CACHE_URI = storage.bronze_uri("rxnorm", "atc_cache.json")
    return _ATC_CACHE_URI


def _load_atc_cache() -> dict[str, dict]:
    global _ATC_CACHE
    if _ATC_CACHE is None:
        try:
            _ATC_CACHE = storage.read_json(_atc_cache_uri()) if storage.exists(_atc_cache_uri()) else {}
        except Exception:
            _ATC_CACHE = {}
    return _ATC_CACHE


def atc_class(rxcui: str | None, *, persist: bool = True) -> tuple[str | None, str | None]:
    """Resolve an ingredient RxCUI to its top-level ATC class ``(atc_code, atc_name)``.

    Uses RxClass ``/class/byRxcui`` with ``relaSource=ATC``; the 1-letter anatomical
    main group (e.g. "A — Alimentary tract and metabolism") is returned as the coarse
    drug class. Cached to bronze like :func:`resolve`; misses cached too.
    """
    if not rxcui:
        return None, None
    key = str(rxcui)
    with _LOCK:
        cache = _load_atc_cache()
        if key in cache:
            hit = cache[key]
            return hit.get("atc_code"), hit.get("atc_name")

    atc_code: str | None = None
    atc_name: str | None = None
    try:
        data = _get("rxclass/class/byRxcui.json", {"rxcui": key, "relaSource": "ATC"})
        infos = data.get("rxclassDrugInfoList", {}).get("rxclassDrugInfo", []) or []
        # Prefer the most-specific ATC level returned, then derive the 1-letter main group.
        best = None
        for info in infos:
            cls = info.get("rxclassMinConceptItem", {})
            cid = cls.get("classId")
            if cid and (best is None or len(cid) > len(best[0])):
                best = (cid, cls.get("className"))
        if best:
            atc_code = best[0][:1]  # anatomical main group (coarse, stable class)
            atc_name = _ATC_MAIN_GROUPS.get(atc_code, best[1])
    except requests.RequestException:
        pass

    with _LOCK:
        cache = _load_atc_cache()
        cache[key] = {"atc_code": atc_code, "atc_name": atc_name}
        if persist:
            storage.write_json(cache, _atc_cache_uri())
    return atc_code, atc_name


# ATC level-1 anatomical main groups (stable WHO reference) — coarse, human-readable class.
_ATC_MAIN_GROUPS = {
    "A": "Alimentary tract and metabolism",
    "B": "Blood and blood forming organs",
    "C": "Cardiovascular system",
    "D": "Dermatologicals",
    "G": "Genito-urinary system and sex hormones",
    "H": "Systemic hormonal preparations, excl. sex hormones and insulins",
    "J": "Antiinfectives for systemic use",
    "L": "Antineoplastic and immunomodulating agents",
    "M": "Musculo-skeletal system",
    "N": "Nervous system",
    "P": "Antiparasitic products, insecticides and repellents",
    "R": "Respiratory system",
    "S": "Sensory organs",
    "V": "Various",
}


def reset_cache() -> None:
    """Drop the in-memory cache (mainly for tests)."""
    global _CACHE, _CACHE_URI, _ATC_CACHE, _ATC_CACHE_URI
    _CACHE = None
    _CACHE_URI = None
    _ATC_CACHE = None
    _ATC_CACHE_URI = None
