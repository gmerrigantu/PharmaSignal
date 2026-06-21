# PharmaSignal — Architecture

PharmaSignal is a cost-aware, cloud-native **medallion lakehouse** for drug-safety
signal detection. The same logical design runs locally (DuckDB + Parquet) and in the
cloud (S3 + Athena, or Databricks + Delta). The dashboard only ever reads
precomputed **gold** tables, so it stays fast and cheap.

## Logical flow

```
External sources                Ingestion (bronze)         Transform (silver)        Analytics (gold)            Serving
─────────────────               ──────────────────         ──────────────────        ─────────────────           ───────
openFDA Drug Event API   ─┐     raw JSON + query hash       silver_reports            drug_event_counts           DuckDB / Athena
FAERS quarterly ZIPs     ─┼──▶  immutable raw + checksum ▶  silver_drugs (normalized) signal_scores (ROR/PRR/CI)  Streamlit dashboard
NHANES XPT files         ─┤     raw XPT                      silver_reactions          emerging_signals (trend)    optional FastAPI
PubMed E-utilities       ─┘     raw XML + retrieval date     silver_nhanes_*           nhanes_population_context
                                                            silver_pubmed_articles    pubmed_evidence
                                                                                      pipeline_health
```

Governance (data-quality checks, lineage metadata, cost monitoring, responsible-use
disclaimers) wraps every layer.

## Components in this repo

| Concern | Module | Notes |
|---|---|---|
| openFDA ingestion (MVP) | `src/pharmasignal/ingestion/openfda.py` | Bronze JSON cache keyed by query hash. |
| FAERS quarterly ingestion (prod) | `src/pharmasignal/ingestion/faers_quarterly.py` | Downloads ZIPs, parses ASCII, writes partitioned silver Parquet. |
| Drug/reaction normalization | `src/pharmasignal/transforms/normalize.py` | Raw preserved; method + confidence tracked. |
| Signal statistics | `src/pharmasignal/modeling/signal_scores.py` | Pure, unit-tested ROR/PRR/CI/shrinkage/anomaly/priority. |
| NHANES context | `src/pharmasignal/nhanes/ingest.py` | Survey-weighted prevalence; never person-linked to FAERS. |
| PubMed evidence | `src/pharmasignal/pubmed/` | E-utilities retrieval + transparent relevance scoring. |
| Lakehouse I/O | `src/pharmasignal/serving/lakehouse.py` | Parquet write + DuckDB query; demo-data fallback. |
| Pipeline orchestration | `src/pharmasignal/pipeline/` | `build_gold` (live) and `generate_demo` (offline). |
| Quality checks | `src/pharmasignal/quality/checks.py` | Completeness / validity / uniqueness / distribution. |
| Dashboard | `dashboard/` | 9-page Streamlit app reading gold tables. |

## Storage naming convention

```
s3://pharmasignal-data/
  bronze/faers/year=2025/quarter=Q1/...
  bronze/openfda/date=2026-06-20/query_<hash>.json
  bronze/nhanes/cycle=2021-2023/component=questionnaire/RXQ_RX_L.xpt
  bronze/pubmed/date=2026-06-20/query_<hash>.json
  silver/faers/{reports,drugs,reactions}/year=.../quarter=.../data.parquet
  gold/{signal_scores,emerging_signals,nhanes_population_context,pubmed_evidence,pipeline_health}.parquet
```

Locally these map to `data/{bronze,silver,gold}/`. Override the root with the
`PHARMASIGNAL_DATA_ROOT` env var (e.g. point at a mounted S3 prefix).

## Deployment targets

See [cost_estimate.md](cost_estimate.md) and
[../infrastructure/CLOUD_SETUP.md](../infrastructure/CLOUD_SETUP.md). The recommended
MVP path is **Local/GitHub Actions compute → S3 gold tables → Streamlit Community
Cloud**, which runs at hobby-project cost.
```
```
