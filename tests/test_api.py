"""Smoke tests for the FastAPI serving layer (pharmasignal.api).

Runs against the bundled demo gold dataset (no network/S3), exercising the
frontend contract (``/dashboard/summary``) and the filterable resource endpoints.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pharmasignal.api import service  # noqa: E402
from pharmasignal.api.main import app  # noqa: E402

client = TestClient(app)

# Tables the dashboard payload must always carry (matches frontend DashboardData).
# signal_scores is no longer embedded as a full array — it is the unbounded matrix,
# represented in the summary by aggregates + a bounded scatter sample.
_DASHBOARD_TABLES = [
    "emerging_signals", "nhanes_population_context",
    "pubmed_evidence", "pipeline_health", "data_quality_checks",
]


@pytest.fixture(autouse=True)
def _demo_dataset(tmp_path, monkeypatch):
    # Force the bundled demo gold: point the lakehouse at an empty root so reads fall
    # back to sample_data/, independent of any real data/gold present on the machine
    # (e.g. after a local backfill run). Keeps these contract tests deterministic.
    monkeypatch.setenv("PHARMASIGNAL_DATA_ROOT", str(tmp_path))
    service.clear_cache()
    yield
    service.clear_cache()


def test_health_lists_tables():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["data_source"] in {"demo", "pipeline", "s3"}
    assert body["tables"]["signal_scores"] > 0


def test_dashboard_summary_matches_contract():
    r = client.get("/dashboard/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["data_source"] in {"demo", "pipeline", "s3"}
    assert "generated_at" in body
    for table in _DASHBOARD_TABLES:
        assert table in body, f"missing {table}"
        assert isinstance(body[table], list)
    # Full-matrix aggregates + bounded scatter sample replace the embedded array.
    assert isinstance(body["signal_total"], int) and body["signal_total"] > 0
    assert isinstance(body["flagged_total"], int)
    assert isinstance(body["signal_sample"], list) and body["signal_sample"]
    row = body["signal_sample"][0]
    # Exactly the documented columns, nothing leaked from the internal model.
    assert set(row) == set(service._DASHBOARD_COLUMNS["signal_scores"])
    assert isinstance(row["disproportionality_flag"], bool)


def test_summary_is_json_safe_no_nan():
    # Raw text must not contain NaN/Infinity (invalid JSON that breaks JS parsers).
    raw = client.get("/dashboard/summary").text
    assert "NaN" not in raw
    assert "Infinity" not in raw


def test_signals_pagination_envelope_and_filters():
    body = client.get("/signals", params={"limit": 1000}).json()
    # Paginated envelope: total is the full filtered count, rows is one page.
    assert set(body) == {"total", "offset", "limit", "rows"}
    assert body["total"] >= len(body["rows"])
    rows = body["rows"]

    flagged = client.get("/signals", params={"flagged_only": True, "limit": 1000}).json()
    assert flagged["total"] <= body["total"]
    assert all(r["disproportionality_flag"] for r in flagged["rows"])
    # min_reports threshold honored
    big = client.get("/signals", params={"min_reports": 1000, "limit": 1000}).json()
    assert all(r["a_drug_event"] >= 1000 for r in big["rows"])
    # sorted by ROR desc by default
    rors = [r["ror"] for r in rows]
    assert rors == sorted(rors, reverse=True)


def test_signals_offset_pages_distinct_rows():
    page1 = client.get("/signals", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/signals", params={"limit": 2, "offset": 2}).json()
    assert page1["rows"] and page2["rows"]
    key = lambda r: (r["drug_name_normalized"], r["adverse_event"])
    assert {key(r) for r in page1["rows"]}.isdisjoint({key(r) for r in page2["rows"]})


def test_signals_drug_filter_case_insensitive():
    body = client.get("/signals", params={"drug": "semaglutide", "limit": 1000}).json()
    assert body["rows"], "expected SEMAGLUTIDE rows"
    assert all(r["drug_name_normalized"].upper() == "SEMAGLUTIDE" for r in body["rows"])


def test_emerging_priority_filter_and_sort():
    rows = client.get("/emerging").json()
    scores = [r["priority_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    high = client.get("/emerging", params={"priority": "High"}).json()
    assert all(r["priority_level"] == "High" for r in high)


def test_drug_profile_and_404():
    ok = client.get("/drugs/semaglutide")
    assert ok.status_code == 200
    assert ok.json()["signals"]
    missing = client.get("/drugs/not-a-real-drug-xyz")
    assert missing.status_code == 404


def test_optional_tables_degrade_to_empty():
    # interaction/subgroup marts are absent from the demo dataset -> [].
    assert client.get("/interactions").json() == []
    assert client.get("/subgroups").json() == []
