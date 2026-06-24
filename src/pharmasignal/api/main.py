"""FastAPI serving layer for PharmaSignal gold marts (requirements §13.1).

Read-only. Every route returns JSON assembled from the gold lakehouse by
``service``. Designed to run two ways from the same code:

  * **Local / container dev:**  ``uvicorn pharmasignal.api.main:app --reload``
  * **AWS Lambda (prod):**      the module-level ``handler`` (Mangum ASGI adapter),
    fronted by an HTTP API Gateway. See ``infrastructure/api_deploy.py``.

CORS: the Vercel frontend calls this cross-origin, so allowed origins are configured
via ``PHARMASIGNAL_CORS_ORIGINS`` (comma-separated). Default ``*`` is convenient for
local dev — set it to your Vercel domain(s) in production.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import service

app = FastAPI(
    title="PharmaSignal API",
    version="1.0.0",
    description="Read-only serving layer over the PharmaSignal gold lakehouse. "
                "Educational pharmacovigilance signals — not medical advice; "
                "disproportionality is hypothesis-generating, not causal.",
)

_origins = [o.strip() for o in os.getenv("PHARMASIGNAL_CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
    max_age=3600,
)


@app.get("/")
def root() -> dict:
    return {
        "service": "pharmasignal-api",
        "version": app.version,
        "endpoints": [
            "/health", "/dashboard/summary", "/signals", "/emerging",
            "/drugs/{drug}", "/nhanes", "/evidence", "/interactions", "/subgroups",
            "/label-flags",
        ],
        "disclaimer": "Hypothesis-generating safety signals from public FAERS/openFDA "
                      "data. Not medical advice; not causal evidence.",
    }


@app.get("/health")
def health() -> dict:
    return service.health()


@app.get("/dashboard/summary")
def dashboard_summary() -> dict:
    """Primary endpoint consumed by the Next.js frontend (lib/api.ts)."""
    return service.dashboard_summary()


@app.get("/signals")
def signals(
    drug: str | None = Query(None, description="Filter by normalized drug name"),
    event: str | None = Query(None, description="Filter by adverse event term"),
    drug_class: str | None = Query(None),
    flagged_only: bool = Query(False, description="Only disproportionality-flagged pairs"),
    novel_only: bool = Query(False, description="Only novel pairs (event not in the label)"),
    min_reports: int = Query(0, ge=0, description="Minimum report count (a_drug_event)"),
    q: str | None = Query(None, description="Substring search over drug + event names"),
    sort: str = Query("ror", description="Sort column (ror, a_drug_event, prr, ...)"),
    desc: bool = Query(True, description="Sort descending"),
    offset: int = Query(0, ge=0, description="Pagination offset into the full filtered set"),
    limit: int = Query(100, ge=1, le=1000, description="Page size"),
) -> dict:
    """Paginated slice of the full signal_scores matrix. Returns
    ``{total, offset, limit, rows}`` — page through ``total`` via ``offset``."""
    return service.signals(drug=drug, event=event, drug_class=drug_class,
                           flagged_only=flagged_only, novel_only=novel_only,
                           min_reports=min_reports, q=q,
                           sort=sort, desc=desc, offset=offset, limit=limit)


@app.get("/emerging")
def emerging(
    priority: str | None = Query(None, description="High | Moderate | Low"),
    limit: int | None = Query(None, ge=1, le=1000),
) -> list[dict]:
    return service.emerging(priority=priority, limit=limit)


@app.get("/drugs/{drug}")
def drug_profile(drug: str) -> dict:
    profile = service.drug_profile(drug)
    if not profile["signals"] and not profile["emerging"] and not profile["nhanes"]:
        raise HTTPException(status_code=404, detail=f"No data for drug '{drug}'")
    return profile


@app.get("/facets/drug-classes")
def facet_drug_classes() -> list[str]:
    """Full distinct drug-class list for the class filter (small)."""
    return service.facet_drug_classes()


@app.get("/facets/drugs")
def facet_drugs() -> list[dict]:
    """All distinct drugs (name, class, case count) for the full-scale picker/autocomplete."""
    return service.facet_drugs()


@app.get("/facets/events")
def facet_events() -> list[dict]:
    """All distinct adverse events (name, case count) for the full-scale picker/autocomplete."""
    return service.facet_events()


@app.get("/nhanes")
def nhanes() -> list[dict]:
    return service.nhanes()


@app.get("/evidence")
def evidence(drug: str | None = Query(None), event: str | None = Query(None)) -> list[dict]:
    return service.evidence(drug=drug, event=event)


@app.get("/interactions")
def interactions(drug: str | None = Query(None)) -> list[dict]:
    return service.optional_table("interaction_signals", drug=drug)


@app.get("/subgroups")
def subgroups(drug: str | None = Query(None)) -> list[dict]:
    return service.optional_table("subgroup_signals", drug=drug)


@app.get("/label-flags")
def label_flags(drug: str | None = Query(None)) -> list[dict]:
    """Labeled-vs-novel status per drug-event pair (drug_label_flags mart)."""
    return service.optional_table("drug_label_flags", drug=drug)


# AWS Lambda entry point. Imported lazily-tolerant: uvicorn/local dev never needs it,
# and importing mangum only here keeps it an optional dependency for non-Lambda runs.
try:  # pragma: no cover - exercised only in the Lambda container
    from mangum import Mangum

    handler = Mangum(app)
except ModuleNotFoundError:  # mangum not installed locally — fine for uvicorn dev
    handler = None
