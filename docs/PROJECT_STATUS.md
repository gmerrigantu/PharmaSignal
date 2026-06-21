# PharmaSignal — Project Status & Build Log

Snapshot of what is implemented in this repo against the requirements roadmap (§17)
and acceptance criteria (§19). This is the handoff document.

## Implemented (this build)

| Phase | Requirement | Status | Where |
|---|---|---|---|
| 0 | Repo skeleton, config, Makefile, README, disclaimers | ✅ | repo root, `config/`, `docs/` |
| 0 | Selected drug-class config (GLP-1 + adjacent) | ✅ | `config/drugs_of_interest.yml` |
| 1 | openFDA API ingestion w/ bronze caching | ✅ | `ingestion/openfda.py` |
| 1 | FAERS quarterly-file ingestion (DEMO/DRUG/REAC/OUTC/INDI) | ✅ runnable | `ingestion/faers_quarterly.py` |
| 1 | Drug-name normalization (raw preserved, confidence tracked) | ✅ | `transforms/normalize.py` |
| 2 | ROR/PRR/CI/χ²/shrinkage + signal flags | ✅ unit-tested | `modeling/signal_scores.py` |
| 2 | Gold `signal_scores`, `drug_event_counts` | ✅ | `pipeline/build_gold.py` |
| 2 | Unit tests for formulas + edge cases | ✅ 24 tests | `tests/` |
| 3 | Streamlit MVP (Overview, Signal Explorer, Drug Profile, Emerging, Pipeline Health) | ✅ | `dashboard/` |
| 3 | Bubble / ranked table / CI plot / trend / metric cards | ✅ | `dashboard/` |
| 3 | Offline demo mode (bundled gold dataset) | ✅ | `pipeline/generate_demo.py`, `sample_data/gold/` |
| 4 | NHANES weighted prevalence + demographic/clinical context | ✅ runnable | `nhanes/ingest.py` |
| 4 | NHANES Context page + FAERS-vs-NHANES framing | ✅ | `dashboard/pages/6_NHANES_Context.py` |
| 5 | PubMed query builder, retrieval cache, relevance scoring | ✅ | `pubmed/` |
| 5 | Literature Evidence page | ✅ | `dashboard/pages/5_Literature_Evidence.py` |
| 6 | Simplified empirical-Bayes shrinkage | ✅ | `modeling/signal_scores.py` |
| 6 | EWMA / Poisson anomaly detection | ✅ | `modeling/signal_scores.py` |
| 6 | IaC (Terraform) + cost docs + data-quality reports | ✅ | `infrastructure/`, `docs/` |
| — | Data-quality checks → `pipeline_health` | ✅ | `quality/checks.py` |
| — | GitHub Actions scheduled pipeline | ✅ | `.github/workflows/pipeline.yml` |

## MVP acceptance criteria (§19.1)
- [x] Signal Explorer shows count, serious count, ROR, PRR, CI, trend per filters.
- [x] Drug Profile shows top events, seriousness, demographics (NHANES), trends.
- [x] Pipeline Health shows ingestion timestamp, row counts, missingness, warnings.
- [x] FAERS caveat on every relevant page.
- [x] Reproducible local execution + sample outputs bundled.
- [~] "≥ 8 FAERS quarters ingested" — API-mode build covers the configured window;
  the **quarterly-file path is implemented** (`faers_quarterly.py`) but the multi-GB
  downloads are not run in this environment. Run `python -m
  pharmasignal.ingestion.faers_quarterly 2023q1 2023q2 ...` to materialize them.

## Cloud deployment (LIVE)
The platform is deployed and running on AWS — full record in
[aws_deployment.md](aws_deployment.md).
- S3 lakehouse `pharmasignal-data-<unique-suffix>`, Glue DB `pharmasignal`, Athena
  workgroup `pharmasignal` (1 GiB/query guardrail).
- Pipeline runs with `PHARMASIGNAL_DATA_ROOT=s3://…` write gold straight to S3; **8
  gold tables** registered as Glue external tables and queryable via Athena.
- Real data: 999 drug-event pairs, 562 flagged; real PubMed PMIDs; NHANES 2017–2020
  survey-weighted prevalence. Dashboard verified reading from S3 (all 9 pages).
- Bronze cache pruned post-build → bucket ~0.37 MB (~$0/month).

## Priority-score enrichment (DONE)
`pipeline/enrich_signals.py` joins the real `pubmed_support_summary` +
`nhanes_population_context` back onto `emerging_signals` and recomputes the composite
priority with **all five components populated** (D/T/S/L/P), each persisted as a
transparent `*_component` column (21-col table). Run order:
`make pipeline-full` = pipeline → nhanes → pubmed → enrich. Verified on AWS via Athena;
signals with real literature support (e.g. semaglutide → optic ischaemic neuropathy)
now rank above the artifact cluster.

## Advanced signal features (DONE)
- **Labeled-vs-novel** (`drug_label_flags`) — openFDA Drug Label API, spelling-aware
  matcher; Signal Explorer filter/column.
- **Age/sex subgroup signals** (`subgroup_signals`) — ROR within sex + age-band strata;
  Subgroup Signals page. `make subgroups`.
- **Drug–drug interaction signals** (`interaction_signals`) — co-reported drug-pair ROR
  vs. single-agent baselines (only flagged when a real single-agent baseline exists);
  Drug Interactions page. `make interactions`.
All three run on AWS and are registered as Glue/Athena tables (11 tables total).

## Known gaps / next steps (deliberately deferred)
- **Live data not materialized here.** `make pipeline`, `make nhanes`, `make pubmed`
  need network access; this build ships the deterministic **demo** dataset instead.
  All three commands are implemented and runnable.
- **Quarterly-file silver → gold join.** `faers_quarterly.py` writes silver Parquet;
  wiring a silver-based `build_gold` (instead of API marginals) is the natural next
  step for full relational fidelity and report-level de-duplication.
- **Complex-survey variance** for NHANES (strata/PSU CIs) is documented but not
  computed — MVP shows point estimates + unweighted n only (avoids overstating
  precision).
- **NLP upgrades** (embeddings, topic modeling, NER, guarded LLM summaries) are
  scaffolded as a documented option, not built.
- **Optional FastAPI serving layer** (§13.1) not built; dashboard reads gold directly.

## How this was verified
- `make demo` generates 9 gold tables (2,226 rows) using the real modeling functions.
- `pytest` → 24 passed (ROR/PRR/CI/shrinkage/trend/priority/normalization/quality).
- All 9 Streamlit pages render with no exceptions via `streamlit.testing.v1.AppTest`.
