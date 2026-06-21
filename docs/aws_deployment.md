# PharmaSignal — Live AWS Deployment Record

This documents the actual cloud deployment that was provisioned and run (not just the
plan). Reproduce with `infrastructure/aws_deploy.py` (see
[../infrastructure/CLOUD_SETUP.md](../infrastructure/CLOUD_SETUP.md)).

## Provisioned resources (AWS account `<account-id>`, region us-east-1)

| Resource | Name | Notes |
|---|---|---|
| S3 lakehouse bucket | `pharmasignal-data-<unique-suffix>` | public access blocked, versioned, AES256, lifecycle on `athena-results/` + `tmp/` |
| Glue catalog database | `pharmasignal` | 8 external tables over the S3 gold Parquet |
| Athena workgroup | `pharmasignal` | 1 GiB/query bytes-scanned guardrail; results → `s3://…/athena-results/` |
| IAM user | `PharmaSignal` | programmatic access; S3 scoped to `pharmasignal-*` |

## S3 layout
```
s3://pharmasignal-data-<unique-suffix>/
  gold/              <- 8 analytics Parquet tables (the data product, ~0.36 MB)
  gold_tables/<name>/ <- per-table folder copies that back the Athena external tables
  athena-results/    <- query outputs (auto-expire after 14 days)
```
The bronze openFDA cache (~650 MB) is **pruned after each build** to keep storage at
~0.4 MB; it is fully reproducible from the API.

## Gold tables registered in Glue / queryable via Athena (11)
`signal_scores` (999 rows), `drug_event_counts`, `emerging_signals` (50),
`drug_label_flags` (999), `subgroup_signals` (138, age/sex strata),
`interaction_signals` (1820, co-reported drug pairs), `nhanes_population_context` (9),
`pubmed_evidence`, `pubmed_support_summary`, `pipeline_health`, `data_quality_checks`.

## Live data produced (real, not demo)
- **openFDA:** 10 drugs, **999 drug-event pairs**, **562 disproportionality-flagged**,
  universe = 7,084,818 reports in the 2021–2025 window.
- **Notable real signal surfaced:** `semaglutide → OPTIC ISCHAEMIC NEUROPATHY`
  (123 reports, ~99% serious) — consistent with the real-world 2024 NAION signal —
  and corroborated by a retrieved PubMed citation.
- **Data-quality lesson captured:** several top-ROR pairs (e.g. `linagliptin →
  CARDIOSPASM`) are **mass-reporting / duplicate-cluster artifacts** (many diabetes
  drugs co-listed on the same odd reports). The composite priority score + shrinkage
  correctly demote them below genuine signals — a live demonstration of why raw ROR
  must not be read as causal.
- **NHANES (2017–2020 pre-pandemic):** survey-weighted medication prevalence, e.g.
  metformin 6.21% (unweighted n=960); GLP-1 agents flagged small-n.
- **PubMed:** real PMIDs retrieved for the top non-artifact signals.

## Verified
- `aws_deploy.py provision` → bucket + Glue DB + Athena workgroup created.
- Pipeline run with `PHARMASIGNAL_DATA_ROOT=s3://…` → gold written straight to S3.
- `aws_deploy.py register` → 8 Glue external tables.
- `aws_deploy.py query` → Athena returns ranked signals from S3 gold.
- Streamlit `AppTest` → all 9 pages render with **Data source: s3**.

## Cost
~$0.01/month storage (0.37 MB), Athena ~$0.00005/query (10 MB minimum, 1 GiB cap),
Glue free. Effectively $0 under the new-account free tier. Nothing always-on.

## Run the dashboard against the cloud lakehouse
```bash
export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_DEFAULT_REGION=us-east-1
PHARMASIGNAL_DATA_ROOT=s3://pharmasignal-data-<unique-suffix> \
  PYTHONPATH=src streamlit run dashboard/app.py
```
