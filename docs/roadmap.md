# PharmaSignal — Roadmap & Implementation Status

This document tracks what is fully implemented, what is partially built, and the
prioritized backlog for evolving PharmaSignal from a curated GLP-1 / metabolic-drug
demonstrator into a whole-database FAERS analysis platform.

---

## Current implementation (fully done)

### Core platform

| Area | Status | Where |
|---|---|---|
| Repo skeleton, config, Makefile, disclaimers | ✅ | repo root, `config/`, `docs/` |
| GLP-1 + metabolic drug config (10 drugs) | ✅ | `config/drugs_of_interest.yml` |
| openFDA API ingestion with bronze caching | ✅ | `ingestion/openfda.py` |
| FAERS quarterly-file ingestion (all tables) | ✅ | `ingestion/faers_quarterly.py` |
| FAERS staging for Spark (download + extract) | ✅ | `ingestion/stage_faers.py` |
| Drug-name normalization (raw preserved, confidence tracked) | ✅ | `transforms/normalize.py` |
| ROR / PRR / CI / χ² / shrinkage + signal flags | ✅ unit-tested | `modeling/signal_scores.py` |
| Set-based SQL gold build from silver (whole-DB, no API) | ✅ | `pipeline/build_gold_bulk.py` |
| Shared scoring implementation (DuckDB + Spark) | ✅ | `pipeline/scoring.py` |
| PySpark jobs for EMR Serverless backfill | ✅ | `spark/jobs/` |
| openFDA Drug Label flags — labeled vs. novel | ✅ | `pipeline/build_label_flags.py` |
| Age/sex demographic subgroup signals | ✅ | `pipeline/build_subgroups.py` |
| Drug-drug interaction signals | ✅ | `pipeline/build_interactions.py` |
| Priority enrichment (all 5 components) | ✅ | `pipeline/enrich_signals.py` |
| NHANES weighted prevalence + demographic context | ✅ | `nhanes/ingest.py` |
| PubMed retrieval + relevance scoring | ✅ | `pubmed/` |
| Data-quality checks → `pipeline_health` | ✅ | `quality/checks.py` |
| 43 unit / API / Streamlit smoke tests | ✅ | `tests/` |
| GitHub Actions scheduled pipeline | ✅ | `.github/workflows/pipeline.yml` |
| Offline demo mode (bundled gold dataset) | ✅ | `pipeline/generate_demo.py`, `sample_data/gold/` |

### Dashboard (9 pages)

| Page | Status |
|---|---|
| Overview, Signal Explorer, Drug/Event Profile | ✅ |
| Emerging Signals, Subgroup Signals, Drug Interactions | ✅ |
| Literature Evidence, NHANES Context, Pipeline Health | ✅ |

### Serving & frontend

| Area | Status | Where |
|---|---|---|
| FastAPI serving layer — 9 endpoints | ✅ | `src/pharmasignal/api/` |
| Lambda container image + HTTP API Gateway IaC | ✅ | `infrastructure/api_deploy.py` |
| Next.js App Router frontend (Vercel, ISR-cached) | ✅ | `frontend/` |

### AWS deployment (live)

| Resource | Status |
|---|---|
| S3 lakehouse bucket `pharmasignal-data-<suffix>` | ✅ provisioned |
| Glue DB `pharmasignal` + Athena workgroup (1 GiB cap) | ✅ provisioned |
| 11 gold tables registered as Glue external tables | ✅ queryable via Athena |
| Lambda + HTTP API Gateway serving the FastAPI layer | ✅ deployed |
| Terraform IaC | ✅ |

### Live data produced (real, not demo)

- 10 drugs, **999 drug-event pairs**, **562 disproportionality-flagged**
- Universe: 7,084,818 FAERS reports (2021–2025 window)
- Real NHANES 2017–2020 survey-weighted prevalence
- Real PubMed PMIDs for top non-artifact signals
- Notable signal: `semaglutide → OPTIC ISCHAEMIC NEUROPATHY` (123 reports, ~99% serious,
  correctly reads **novel** — consistent with the 2024 NAION pharmacovigilance signal)
- Bronze cache pruned post-build → bucket ~0.37 MB (~$0/month storage)

---

## Acceptance criteria status

- [x] Signal Explorer shows count, serious count, ROR, PRR, CI, trend, label status per filters.
- [x] Drug Profile shows top events, seriousness, demographics (NHANES), trends.
- [x] Pipeline Health shows ingestion timestamp, row counts, missingness, warnings.
- [x] FAERS caveat on every relevant page.
- [x] Reproducible local execution + sample outputs bundled.
- [x] FastAPI `/dashboard/summary` returns the exact `DashboardData` contract (NaN-free JSON).
- [x] All 9 Streamlit pages render with no exceptions via `streamlit.testing.v1.AppTest`.
- [~] "≥ 8 FAERS quarters ingested" — API-mode build covers the configured window; the
  quarterly-file path is implemented and runnable (`make ingest-faers`) but full-history
  data is not materialized in the current environment. Run `make ingest-faers QUARTERS="..."`.

---

## Core architectural pivot (priority work)

Today's `build_gold.py` scores signals by issuing one openFDA `count` API call per
drug-event pair. For 10 drugs (~999 pairs) this is fast. At full FAERS scale:

| Dimension | GLP-1 scope (now) | Full FAERS |
|---|---|---|
| Distinct drug ingredients | 10 | ~5,000+ |
| Drug-event pairs that co-occur | ~999 | ~5–15 million |
| API calls to score all pairs | ~few thousand | hundreds of millions ❌ |

**The solution is already built:** `build_gold_bulk.py` + `pipeline/scoring.py` score the
full drug × event matrix via set-based SQL `GROUP BY` over silver — no API calls. The
Spark jobs handle the heavy initial backfill. The remaining work below wires everything
together and extends quality.

---

## Priority backlog (next work items)

### P0 — Full-database backfill & dedup (unblocks everything)

**WS1 — Promote the quarterly-file path as the default production path**

1. Backfill full history: run `make stage-faers QUARTERS="2012q4..2024q4"`, then the EMR
   Serverless jobs (`infrastructure/spark_backfill.py ingest && score`). One-time cost ~$1–4.
   See [deployment.md](deployment.md#full-history-faers-backfill-emr-serverless).
2. **Case-version deduplication (critical — currently missing in silver).** FAERS issues
   multiple versions of the same case across quarters; `caseid` repeats with increasing
   `primaryid`/`fda_dt`. Without dedup, counts are inflated. Add a silver step: keep the
   **latest version per `caseid`**, and exclude cases in FDA's `DELETED` files.
   Validate against FDA's published quarterly case totals.
3. **Add missing FAERS tables to the ingester.** Extend `faers_quarterly.py` to parse:
   - **THER** (drug therapy dates → enables time-to-onset analysis)
   - **RPSR** (report source → distinguishes consumer / HCP / literature reports)
4. **Incremental quarterly refresh.** After the backfill, each run downloads only the new
   quarter and appends a partition. `make gold-bulk` handles this on GitHub Actions ($0).

*Effort: medium (ingester exists). Impact: ★★★ — unblocks all downstream work.*

---

### P1 — Drug & reaction normalization at scale

**WS3 — Replace the 10-drug YAML with RxNorm-backed normalization**

1. **RxNorm tiered resolver** — upgrade `transforms/normalize.py`:
   exact dictionary → RxNorm REST API (`/rxcui?name=`, `/approximateTerm`) → fuzzy →
   unmatched, persisting `rxcui`, `ingredient`, `normalization_method`, `confidence`.
   Cache lookups in `bronze/rxnorm/`. Collapses brand/generic/combination variants to
   stable ingredient RxCUIs. Pulls the RxNorm ATC crosswalk for free drug-class grouping.
2. **MedDRA hierarchy** — FAERS reactions are already MedDRA Preferred Terms. To group
   (PT → HLT → HLGT → SOC) requires the MedDRA dictionary. **Licensing note:** MedDRA
   requires a subscription (verify non-commercial eligibility at meddra.org). Interim
   option: use openFDA reaction field SOC metadata or a public SOC crosswalk, flagged
   as approximate.

*Effort: medium (RxNorm) / high (MedDRA licensing). Impact: ★★★.*

---

### P1 — Proper Empirical Bayes (EBGM / EB05)

**WS2 — Upgrade the simplified shrinkage to regulatory-grade MGPS**

Current code has "simplified empirical-Bayes shrinkage" (named honestly to avoid implying
regulatory equivalence). Upgrade:

1. Implement the gamma-Poisson mixture EBGM estimator in `modeling/signal_scores.py` /
   `pipeline/scoring.py`.
2. Add `ebgm` and `eb05` (5th-percentile lower bound) columns to `gold_signal_scores`.
3. Offer EBGM as an alternate ranking toggle in the Signal Explorer.
4. **Minimum-cell + EB05 gating at scale:** with millions of pairs, default the served
   gold mart to pairs meeting `minimum_reports` and an EB05 floor; keep the full matrix
   in a separate `signal_scores_all` Parquet for power users / Athena.

EBGM is especially powerful at full-database scale: it shrinks the millions of small-count
pairs that otherwise dominate raw ROR, making the output trustworthy.

*Effort: high. Impact: ★★★.*

---

### P2 — Deep NHANES integration

**WS5 — Multi-cycle, survey-variance CIs, FAERS-vs-NHANES representativeness**

1. **Multi-cycle ingestion.** Generalize `nhanes/ingest.py` to loop all available public
   cycles (2017–2020, 2015–2016, ... back to 1999), so prevalence trends over time are
   available and small-n drugs aggregate across cycles.
2. **Proper complex-survey variance.** Add Taylor-linearization / replicate-weight
   standard errors and 95% CIs using `samplics` or `statsmodels` survey support and the
   existing `SDMVSTRA`/`SDMVPSU` design variables. Turns "6.21%" into "6.21% (95% CI
   5.1–7.4)".
3. **Wide drug coverage via RxNorm.** Reuse the WS3 RxNorm resolver to map NHANES
   `RXDDRUG` strings, so population context exists for all mapped FAERS drugs.
4. **Reporting-bias view.** Compare the demographic profile of a drug's FAERS reporters vs.
   its NHANES users (age/sex/BMI/diabetes/HbA1c). "Reports skew heavily female vs. the
   actual user base" is a genuine pharmacovigilance insight.

*Effort: medium. Impact: ★★★.*

---

### P2 — PubMed at scale + semantic relevance

**WS4 — Thousands of articles + embedding-based matching**

1. **Scale retrieval.** Get an NCBI API key (10 req/s) and retrieve literature for the
   top N-thousand prioritized signals, batching `efetch` (up to 200 PMIDs/call).
2. **Bulk MEDLINE baseline option.** Ingest the annual PubMed/MEDLINE baseline (free bulk
   XML from NLM) into `silver/pubmed_articles` once, then match locally — eliminates
   per-query API volume.
3. **Semantic relevance via embeddings.** Current matcher is keyword-based. Augment with
   embeddings (embed abstracts + drug-event queries, rank by cosine similarity via
   LanceDB or DuckDB-VSS, $0 infra). Catches papers that describe the association without
   using MedDRA phrasing. Use Amazon Bedrock Titan Embeddings or local `sentence-transformers`.

*Effort: medium–high. Impact: ★★★.*

---

### P3 — AI / LLM layer (Bedrock + Claude)

**WS10 — Signal narrative summaries and RAG**

1. **Signal narrative summaries.** For each high-priority signal, generate a short sourced
   summary (disproportionality, label status, literature, NHANES context) via Amazon
   Bedrock (claude-haiku-4-5 for volume / claude-opus-4-8 for quality). Cache to
   `gold_signal_narratives`. Batch job, top-N only. Every narrative must carry the
   hypothesis-generating disclaimer and cite sources.
2. **Duplicate / case-cluster detection.** Use embeddings + Claude to flag suspected
   duplicate report clusters — feeds the composite priority score as a quality signal.
3. **RAG over literature corpus.** With WS4 embeddings in a vector store, add a "ask
   about this drug-event pair" RAG endpoint answering from retrieved abstracts with citations.
4. **MedDRA/free-text normalization assist.** Use LLM only for the *unmatched* residual
   of RxNorm/MedDRA mapping, with confidence flags — never as the primary normalization path.

> All AI output must carry the same hypothesis-generating disclaimer the statistical pages
> already use, and must cite sources. Use Bedrock so data stays in the AWS account.

*Effort: medium per item (gated on WS4 for RAG). Impact: ★★★ differentiator.*

---

### P3 — Serving API & dashboard at scale

**WS9 — Pagination, search, SOC browse**

1. **API pagination + filtering** on `/signals` (by drug, event, SOC, label status,
   priority); a **search endpoint** backed by Athena (or a small managed Postgres on
   Supabase/Neon free tier) for full-text search across millions of pairs.
2. **Dashboard server-side pagination** — replace any "load full table" pages with lazy
   queries; add typeahead drug/event search.
3. **Frontend (Next.js)** — add search UX and a SOC/drug-class browse tree.

*Effort: medium. Impact: ★★ (needed when signal_scores grows to millions of rows).*

---

### P3 — Additional data sources

**WS6 — Highest-impact integrations**

| Source | Value | Effort |
|---|---|---|
| **ClinicalTrials.gov API** | "Under active investigation" flag on the evidence page | 🟡 |
| **DailyMed / FDA approval dates** | Annotate trend charts; suppress stimulated-reporting spikes post-launch | 🟡 |
| **RxNorm ATC class crosswalk** | Free drug-class grouping for all drugs (piggybacks WS3) | 🟢 |
| **WHO VigiAccess / EMA EudraVigilance** | "Corroborated in EU" flag; signals in both DBs are strongest | 🔴 |
| **CMS / Medicare Part D utilization** | True denominator context (reporting rates vs. disproportionality) | 🔴 |

---

### P3 — Compute & orchestration improvements

**WS8 — Quarterly pipeline automation**

1. **AWS Step Functions** to chain ingest → score → enrich → publish with retries;
   first 4,000 state transitions/month are free.
2. **EventBridge schedule** for the quarterly batch (FAERS publishes quarterly).
3. **Cost telemetry** — extend `pipeline_health` to record bytes scanned and job seconds
   so cost stays observable on the Health page.
4. **Legacy AERS support** — pre-2012 FAERS extracts use `ISR`-keyed filenames and a
   different schema; flagged as a follow-up reader.

---

## Phased roadmap summary

| Phase | Focus | Exit criteria |
|---|---|---|
| **Phase 0** (P0) | Full backfill + case-version dedup | Whole-database `signal_scores` in S3 for ≥2012, dedup validated against FDA totals |
| **Phase 1** (P1) | EBGM / EB05 + RxNorm normalization | `ebgm`/`eb05` columns in gold; ingredient-level rollups; EBGM ranking toggle in dashboard |
| **Phase 2** (P2) | Deep NHANES + PubMed at scale | Survey-variance CIs; FAERS-vs-NHANES representativeness view; thousands of abstracts indexed |
| **Phase 3** (P3) | AI layer + new sources + scale serving | Claude signal narratives on top-N; RAG endpoint; ClinicalTrials flags; paginated API |

---

## Risks & constraints

- **MedDRA licensing** is the main external blocker for hierarchy rollups — confirm
  non-commercial eligibility before depending on PT→SOC groupings; have the openFDA SOC
  approximation fallback ready.
- **Deduplication correctness** materially affects every count — validate against FDA's
  published quarterly totals before publishing whole-DB results.
- **Disproportionality ≠ causality.** At whole-database scale the temptation to over-read
  ROR grows. EB05 gating, the composite priority, and per-page disclaimers must stay
  front-and-center. AI narratives must repeat the hypothesis-generating framing.
- **NHANES stays population-only** — never person-linked to FAERS (non-negotiable).
- **Reporting biases** (stimulated reporting, notoriety, mass tort) should be surfaced via
  the RPSR source field and launch-date annotations, not hidden.
- **Cost discipline** is the invariant that makes the whole-DB ambition viable: precompute
  gold, serve only gold, run heavy compute infrequently, cache all external API responses
  in bronze, cap Bedrock to top-N in batch.
