# PharmaSignal — Architecture

PharmaSignal is a cost-aware, cloud-native **medallion lakehouse** for drug-safety signal
detection. The same logical design runs locally (DuckDB + Parquet) and in the cloud
(S3 + Athena). The dashboard and API **only ever read precomputed gold tables**, so serving
stays fast and cheap regardless of raw data scale.

---

## Logical flow

```
External sources           Ingestion (bronze)             Transform (silver)           Analytics (gold)         Serving
──────────────             ──────────────────             ──────────────────           ─────────────────        ───────
openFDA Drug Event API ─┐  raw JSON + query hash          silver_reports               drug_event_counts        DuckDB / Athena
FAERS quarterly ZIPs   ─┼─▶ immutable raw + checksum ───▶ silver_drugs (normalized)   signal_scores            FastAPI (Lambda)
openFDA Drug Label API ─┤  raw XPT / XML                  silver_reactions             emerging_signals         Next.js (Vercel)
NHANES XPT files       ─┤                                 silver_nhanes_*              drug_label_flags         Streamlit (legacy)
PubMed E-utilities     ─┘                                 silver_pubmed_articles       subgroup_signals
                                                                                       interaction_signals
                                                                                       nhanes_population_context
                                                                                       pubmed_evidence
                                                                                       pipeline_health
                                                                                       data_quality_checks
```

Governance (data-quality checks, lineage metadata, cost monitoring, responsible-use
disclaimers) wraps every layer.

---

## Compute paths

### Path A — openFDA API mode (demo / prototype scope)

`build_gold.py` scores drug-event pairs by issuing one openFDA `count` API call per pair.
Fast for the 10 configured drugs (~999 pairs, few thousand HTTP calls). **Not viable at
full FAERS scale.** Used for the offline demo dataset and the current live AWS deployment.

### Path B — Bulk FAERS quarterly-file mode (production)

`ingest-faers` → `gold-bulk`. `faers_quarterly.py` downloads quarterly ZIPs, parses
`$`-delimited ASCII files (DEMO/DRUG/REAC/OUTC/INDI), and writes partitioned silver Parquet.
`build_gold_bulk.py` then computes the full drug × event signal matrix via set-based SQL
`GROUP BY` aggregations over silver — no API calls. Handles the entire FAERS database
(~20M+ reports) on DuckDB locally or via Athena CTAS in the cloud.

### Path C — Spark / EMR Serverless (full-history backfill)

`spark/jobs/ingest_faers_spark.py` and `build_gold_spark.py` implement the same logic as
Path B in PySpark. Used for the one-time full-history backfill (2012Q4 → present, tens of
GB). Both paths call `pharmasignal.pipeline.scoring` — one canonical scoring implementation.
After the backfill, incremental quarterly runs use Path B (DuckDB, free on GitHub Actions).

---

## Components

| Concern | Module | Notes |
|---|---|---|
| openFDA ingestion (API mode) | `ingestion/openfda.py` | Bronze JSON cache keyed by query hash. |
| FAERS quarterly ingestion | `ingestion/faers_quarterly.py` | Downloads ZIPs, parses ASCII, writes partitioned silver Parquet. |
| FAERS staging (Spark path) | `ingestion/stage_faers.py` | Downloads + extracts ASCII to bronze for Spark jobs. |
| Drug Label ingestion | `ingestion/drug_label.py` | openFDA Drug Label API; British→American spelling-aware matcher. |
| Drug/reaction normalization | `transforms/normalize.py` | Raw preserved; method + confidence tracked. |
| Signal statistics | `modeling/signal_scores.py` | Pure, unit-tested ROR/PRR/CI/shrinkage/anomaly/priority. |
| NHANES context | `nhanes/ingest.py` | Survey-weighted prevalence; never person-linked to FAERS. |
| PubMed evidence | `pubmed/` | E-utilities retrieval + transparent relevance scoring. |
| Lakehouse I/O | `serving/lakehouse.py` · `serving/storage.py` | fsspec abstraction: local path or `s3://` by env var. |
| Gold build — API mode | `pipeline/build_gold.py` | openFDA API per pair → gold (demo scope). |
| Gold build — bulk SQL | `pipeline/build_gold_bulk.py` | Set-based SQL from silver → gold (full scale). |
| Shared scoring logic | `pipeline/scoring.py` | Single ROR/PRR/EBGM impl used by both DuckDB and Spark paths. |
| Label flags | `pipeline/build_label_flags.py` | → `gold_drug_label_flags`. |
| Subgroup signals | `pipeline/build_subgroups.py` | → `gold_subgroup_signals`. |
| Interaction signals | `pipeline/build_interactions.py` | → `gold_interaction_signals`. |
| Priority enrichment | `pipeline/enrich_signals.py` | Joins PubMed + NHANES into emerging_signals; 5-component priority. |
| Spark ingest | `spark/jobs/ingest_faers_spark.py` | Bronze → silver in PySpark (EMR Serverless). |
| Spark gold | `spark/jobs/build_gold_spark.py` | Silver → gold in PySpark (EMR Serverless). |
| Quality checks | `quality/checks.py` | Completeness / validity / uniqueness / distribution. |
| FastAPI serving | `api/main.py` · `api/service.py` | Read-only JSON API over gold tables via DuckDB/pandas. |
| Streamlit dashboard | `dashboard/` | 9-page app reading gold tables. |
| Next.js frontend | `frontend/` | App Router; ISR-cached fetches from FastAPI. |

---

## Serving stack

```
User browser
     │
     ▼
Next.js (Vercel, App Router + ISR — 5-min cache)
     │  HTTPS / CORS
     ▼
HTTP API Gateway ($default stage)
     │  AWS_PROXY payload v2
     ▼
Lambda (container image: FastAPI + Mangum)
     │  IAM role → read-only S3 (no static keys in request path)
     ▼
s3://<bucket>/gold/*.parquet  (read with DuckDB/pandas, NOT Athena per-request)
```

The gold tables total ~0.36 MB; reading Parquet directly is faster and cheaper than
per-request Athena queries. Athena and Glue remain available for ad-hoc analytics
(`infrastructure/aws_deploy.py query`).

The legacy Streamlit dashboard reads the same gold tables and can run locally or against
S3 — kept intact for quick exploration and development.

---

## Storage naming convention

```
s3://pharmasignal-data-<suffix>/
  bronze/faers/year=2025/quarter=Q1/ascii/*.txt
  bronze/openfda/date=2026-06-22/query_<hash>.json
  bronze/nhanes/cycle=2021-2023/component=questionnaire/RXQ_RX_L.xpt
  bronze/pubmed/date=2026-06-22/query_<hash>.json
  silver/faers/{reports,drugs,reactions,outcomes,indications,therapies,report_sources}/
    year=.../quarter=.../data.parquet
  gold/{signal_scores, signal_scores_all, emerging_signals, drug_event_counts,
        drug_label_flags, subgroup_signals, interaction_signals,
        nhanes_population_context, pubmed_evidence, pubmed_support_summary,
        pipeline_health, data_quality_checks}.parquet
  gold_tables/<name>/   ← per-table copies backing Glue external tables
  athena-results/       ← query outputs (auto-expire 14 days)
```

Locally these map to `data/{bronze,silver,gold}/`. Override the root with the
`PHARMASIGNAL_DATA_ROOT` env var (e.g. `s3://pharmasignal-data-<suffix>`).

---

## Design principles

- **Gold is the only read surface.** Dashboards and APIs never scan raw or silver.
- **Precompute on batch, not on request.** Heavy compute runs once per quarter; serving
  reads static Parquet files.
- **One scoring implementation.** `pipeline/scoring.py` is shared by the DuckDB bulk path
  and the Spark path so statistics cannot drift between engines.
- **fsspec abstraction.** All lakehouse I/O goes through `storage.py`; switching between
  local and S3 requires only the `PHARMASIGNAL_DATA_ROOT` env var.
- **Cost discipline.** Bronze cache pruned after each build; Athena workgroup capped at
  1 GiB/query; Lambda + ISR caching minimise AWS API hits.
