"""RxNorm resolver tiering + ingredient-drift hardening (no network — _get is mocked).

The load-bearing guarantee: an exact ingredient name resolves to *itself*, never drifting
to a related stereoisomer/salt (the OMEPRAZOLE -> ESOMEPRAZOLE bug), and the search tiers
are tried exact-before-normalized-before-approximate with decreasing confidence.
"""
from __future__ import annotations

import pytest

from pharmasignal.transforms import rxnorm


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch, tmp_path):
    # Isolate from any on-disk bronze cache so every resolve() actually hits _query.
    monkeypatch.setenv("PHARMASIGNAL_DATA_ROOT", str(tmp_path))
    rxnorm.reset_cache()
    yield
    rxnorm.reset_cache()


def _router(routes):
    """Build a fake _get(path, params) that dispatches on (path, key params)."""
    def fake_get(path, params, *, timeout=20):
        if path == "rxcui.json":
            return routes.get(("name", params.get("name"), params.get("search")), {"idGroup": {}})
        if path.endswith("/properties.json"):
            cui = path.split("/")[1]
            return {"properties": routes.get(("props", cui), None)} if ("props", cui) in routes else {}
        if path.endswith("/related.json"):
            cui = path.split("/")[1]
            return routes.get(("related", cui), {"relatedGroup": {"conceptGroup": []}})
        if path == "approximateTerm.json":
            return routes.get(("approx", params.get("term")), {"approximateGroup": {}})
        return {}
    return fake_get


def test_exact_ingredient_does_not_drift(monkeypatch):
    # OMEPRAZOLE exact-matches rxcui 7646 whose TTY is IN -> must return omeprazole itself,
    # NOT climb related?tty=IN (which here offers esomeprazole) and swap the key.
    routes = {
        ("name", "OMEPRAZOLE", "0"): {"idGroup": {"rxnormId": ["7646"]}},
        ("props", "7646"): {"rxcui": "7646", "name": "omeprazole", "tty": "IN"},
        # A drift trap: if the resolver wrongly climbs, it'd pick esomeprazole.
        ("related", "7646"): {"relatedGroup": {"conceptGroup": [
            {"tty": "IN", "conceptProperties": [{"rxcui": "283742", "name": "esomeprazole"}]}]}},
    }
    monkeypatch.setattr(rxnorm, "_get", _router(routes))
    m = rxnorm.resolve("omeprazole")
    assert m.method == "rxnorm_exact" and m.confidence == "high"
    assert m.ingredient == "omeprazole"
    assert m.ingredient_rxcui == "7646"


def test_brand_climbs_to_ingredient(monkeypatch):
    # A brand (TTY=SBD) is not an ingredient -> climb related?tty=IN to the base ingredient.
    routes = {
        ("name", "ZANTAC", "0"): {"idGroup": {"rxnormId": ["202703"]}},
        ("props", "202703"): {"rxcui": "202703", "name": "Zantac", "tty": "SBD"},
        ("related", "202703"): {"relatedGroup": {"conceptGroup": [
            {"tty": "IN", "conceptProperties": [{"rxcui": "9143", "name": "ranitidine"}]}]}},
    }
    monkeypatch.setattr(rxnorm, "_get", _router(routes))
    m = rxnorm.resolve("zantac")
    assert m.method == "rxnorm_exact"
    assert m.ingredient == "ranitidine" and m.ingredient_rxcui == "9143"


def test_falls_back_exact_then_normalized_then_approximate(monkeypatch):
    # No exact hit; normalized (search=1) succeeds -> medium confidence.
    routes = {
        ("name", "METFORMIN HCL", "0"): {"idGroup": {}},
        ("name", "METFORMIN HCL", "1"): {"idGroup": {"rxnormId": ["6809"]}},
        ("props", "6809"): {"rxcui": "6809", "name": "metformin", "tty": "IN"},
    }
    monkeypatch.setattr(rxnorm, "_get", _router(routes))
    m = rxnorm.resolve("metformin hcl")
    assert m.method == "rxnorm_normalized" and m.confidence == "medium"
    assert m.ingredient == "metformin"

    # Neither exact nor normalized; approximate matches a typo -> low confidence.
    routes2 = {
        ("name", " METFORMINE", "0"): {"idGroup": {}},
        ("approx", "METFORMINE"): {"approximateGroup": {"candidate": [{"rxcui": "6809"}]}},
        ("props", "6809"): {"rxcui": "6809", "name": "metformin", "tty": "IN"},
    }
    monkeypatch.setattr(rxnorm, "_get", _router(routes2))
    m2 = rxnorm.resolve("metformine")
    assert m2.method == "rxnorm_approximate" and m2.confidence == "low"
    assert m2.ingredient == "metformin"


def test_unmatched_is_cached_miss(monkeypatch):
    routes = {("name", "ZXQWV", "0"): {"idGroup": {}}}
    monkeypatch.setattr(rxnorm, "_get", _router(routes))
    m = rxnorm.resolve("zxqwv")
    assert m.method == "rxnorm_unmatched" and m.ingredient is None


def test_combination_picks_lowest_rxcui_deterministically(monkeypatch):
    # Multi-ingredient concept -> deterministic pick (lowest RxCUI) regardless of order.
    routes = {
        ("name", "COMBO", "0"): {"idGroup": {"rxnormId": ["999"]}},
        ("props", "999"): {"rxcui": "999", "name": "Combo Tablet", "tty": "SCD"},
        ("related", "999"): {"relatedGroup": {"conceptGroup": [
            {"tty": "IN", "conceptProperties": [
                {"rxcui": "830", "name": "benazepril"},
                {"rxcui": "17767", "name": "amlodipine"}]}]}},
    }
    monkeypatch.setattr(rxnorm, "_get", _router(routes))
    m = rxnorm.resolve("combo")
    assert m.ingredient_rxcui == "830"  # lowest rxcui, stable across runs
