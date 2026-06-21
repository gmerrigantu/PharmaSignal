# PharmaSignal — Cost Estimate

Design goal: **hobby-project cost** while using professional data-platform patterns.
Achieved by serverless / query-on-demand services, Parquet + partitioning, and
precomputed gold tables (no always-on compute).

## Option A — Local + GitHub Actions + S3 + Streamlit Community Cloud (recommended MVP)

| Resource | Driver | Est. monthly cost |
|---|---|---|
| S3 storage (gold + sample, < 5 GB) | $0.023/GB | **< $0.15** |
| S3 GET/PUT requests | low volume | **< $0.05** |
| GitHub Actions (pipeline runs) | 2,000 free min/mo | **$0** within free tier |
| Streamlit Community Cloud | free tier | **$0** |
| openFDA API | free (240/min, 120k/day with key) | **$0** |
| NCBI E-utilities | free (3–10 req/s) | **$0** |
| **Total** | | **≈ $0–1 / month** |

## Option B — AWS S3 + Athena + Glue + Streamlit

| Resource | Driver | Est. monthly cost |
|---|---|---|
| S3 storage | as above | < $0.20 |
| Athena queries | $5 / TB scanned; partitioned Parquet keeps scans tiny | < $1 (typical dev) |
| Glue Data Catalog | first 1M objects free | $0 |
| Glue crawler (optional) | $0.44/DPU-hr | < $1 if run sparingly |
| **Total** | | **≈ $1–3 / month** |

Cost controls (enforced in code/config):
- Parquet + year/quarter partitioning; never scan raw ASCII repeatedly.
- Precompute `gold_signal_scores` offline; the dashboard never recomputes ROR/PRR.
- openFDA responses and PubMed queries cached in bronze by query hash.
- Demo dataset bundled so reviewers incur **zero** cloud cost.
- Athena workgroup with a per-query data-scanned limit (see Terraform).

## Option C — Databricks Free/Community Edition
Platform cost $0 with feature limits; best for the Delta/lakehouse demonstration.

> The pipeline writes `estimated_cost_usd` into `gold_pipeline_health` each run so cost
> is observable on the Pipeline Health dashboard page (§14.3).
