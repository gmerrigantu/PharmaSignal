# PharmaSignal — Full-FAERS Upgrade Plan

**Goal:** evolve PharmaSignal from a curated GLP-1 / metabolic-drug demonstrator into a
**whole-database FAERS analysis platform** — every drug, every adverse event — that pulls
thousands of PubMed articles, integrates deeply with NHANES, and surfaces genuinely
useful pharmacovigilance insight, while keeping AWS spend to **a few dollars a month**.

This document is the engineering plan: what the current architecture is, where it breaks
at full scale, the target architecture, the concrete workstreams, a cost model, and a
phased roadmap.

---

## 1. The core architectural pivot (read this first)

Today's pipeline (`pipeline/build_gold.py`) computes every signal by issuing **one
openFDA `count` API call per drug-event pair**, plus one call per quarter per top pair
for trend. For the 10 configured drugs (≈999 pairs) that is a few thousand HTTP calls and
runs in minutes.

At full FAERS scale that model is **structurally impossible**:

| Dimension | GLP-1 scope (today) | Full FAERS |
|---|---|---|
| Distinct drug ingredients | 10 | ~5,000+ |
| Distinct MedDRA reaction PTs | ~hundreds touched | ~26,000 |
| Drug-event pairs that actually co-occur | ~999 | ~5–15 million |
| API calls to score all pairs | ~few thousand | hundreds of millions ❌ |
| Universe size | 7.08M reports (windowed) | ~20M+ cumulative reports |

You cannot — and should not — score the whole database through the openFDA count API.
The entire platform must pivot to:

> **Bulk-ingest the FAERS quarterly extract files into the lakehouse, then compute every
> disproportionality statistic as a handful of set-based SQL `GROUP BY` queries over the
> silver tables in DuckDB (local) / Athena (cloud).** The whole drug × event signal matrix
> becomes ~3 aggregate queries, not 100M API calls.

The good news: **the hard part already exists.** `ingestion/faers_quarterly.py` already
downloads the quarterly ZIPs, parses the `$`-delimited ASCII tables (DEMO/DRUG/REAC/
OUTC/INDI), normalizes, and writes partitioned silver Parquet. It is simply **not wired
into `build_gold`** yet. The upgrade is largely *rerouting the gold build to read silver
instead of calling the API*, plus the scale concerns below (dedup, RxNorm, MedDRA, EBGM,
compute orchestration).

openFDA stays in the architecture, but demoted to its right role: **on-demand drill-downs
and "is this current quarter spiking?" checks** in the dashboard — not bulk scoring.

---

## 2. Current architecture — assessment

The medallion design (bronze → silver → gold Parquet, DuckDB local / S3+Athena cloud,
read-only gold served to Streamlit + FastAPI) is **fundamentally sound and worth keeping**.
The `storage.py` fsspec abstraction (local path vs `s3://` by env var) and the pure,
unit-tested `modeling/signal_scores.py` are exactly the right foundations for scaling.

What changes per component:

| Component | File | Scales as-is? | Action |
|---|---|---|---|
| Medallion lakehouse / fsspec storage | `serving/storage.py` | ✅ | Keep; add partition-aware readers |
| Quarterly FAERS ingester | `ingestion/faers_quarterly.py` | ⚠️ exists, unused | **Promote to primary**; add dedup, THER/RPSR, incremental |
| openFDA API client | `ingestion/openfda.py` | ✅ for drill-down | Demote from bulk scoring to on-demand |
| Gold build | `pipeline/build_gold.py` | ❌ API-loop per pair | **Rewrite as SQL aggregation over silver** |
| Signal statistics | `modeling/signal_scores.py` | ✅ pure functions | Keep; add vectorized + EBGM/MGPS |
| Drug normalization | `transforms/normalize.py` | ❌ 10-drug YAML | Add RxNorm tiered resolver |
| Reaction grouping | (none) | ❌ flat PT only | Add MedDRA hierarchy (PT→HLGT→SOC) |
| NHANES | `nhanes/ingest.py` | ⚠️ 1 cycle, ~10 drugs | Multi-cycle, survey variance, RxNorm-wide |
| PubMed | `pubmed/eutils.py` | ⚠️ per-pair keyword | Bulk + embeddings/vector search |
| Quality checks | `quality/checks.py` | ✅ | Extend for scale (dedup rate, null drift) |
| Serving API | `api/` (Lambda) | ⚠️ loads gold into memory | Add pagination / search / Athena passthrough |
| Dashboard | `dashboard/` | ⚠️ assumes small marts | Server-side search/pagination, lazy loads |

---

## 3. Target architecture

```
                                  ┌─────────────────────── BRONZE (immutable raw) ──────────────────────┐
 FAERS quarterly ZIPs  ──────────▶│ s3://…/bronze/faers/year=/quarter=/  (ASCII + sha256 sidecar)        │
 RxNorm REST / RRF     ──────────▶│ bronze/rxnorm/…    MedDRA (licensed) ─▶ bronze/meddra/…              │
 NHANES XPT (all cycles)─────────▶│ bronze/nhanes/cycle=/…                                               │
 PubMed baseline + E-utils ──────▶│ bronze/pubmed/…  (MEDLINE XML + per-query cache)                     │
 ClinicalTrials / DailyMed ──────▶│ bronze/<source>/…                                                    │
                                  └──────────────────────────────────────┬──────────────────────────────┘
                                                                         │  (AWS Batch / Fargate Spot job, quarterly)
                                  ┌──────────────── SILVER (typed, deduped, normalized) ─────────────────▼┐
                                  │ silver/faers/{reports,drugs,reactions,outcomes,indications,therapies} │
                                  │   • case-version dedup (latest per caseid)                            │
                                  │   • drug_name → RxCUI ingredient (RxNorm)                             │
                                  │   • reaction PT → HLT/HLGT/SOC (MedDRA)                                │
                                  │ silver/nhanes_*   silver/pubmed_articles (+ embeddings)               │
                                  └──────────────────────────────────────┬──────────────────────────────┘
                                                                         │  (set-based SQL: DuckDB / Athena CTAS)
                                  ┌──────────────────── GOLD (analytics marts) ─────────────────────────▼┐
                                  │ signal_scores (ROR/PRR/EBGM/EB05, whole DB)  emerging_signals (trend) │
                                  │ drug_label_flags  subgroup_signals  interaction_signals               │
                                  │ nhanes_population_context  pubmed_evidence  literature_embeddings      │
                                  │ soc_rollups  drug_dimension(RxCUI)  pipeline_health  dq_checks         │
                                  └──────────────────────────────────────┬──────────────────────────────┘
                                                                         │
                 read-only ┌───────────────────────────────────────────┴───────────────┐
                           ▼                         ▼                                   ▼
                  FastAPI (Lambda)          Streamlit dashboard                 Athena ad-hoc / notebooks
                  + Athena for search       (server-side paginated)             + Bedrock/Claude AI layer
                           │
                           ▼
                  Next.js frontend (Vercel)
```

Key principle preserved: **the dashboard and API still read only precomputed gold**, so
serving stays cheap regardless of how big the raw database gets. The expensive work is a
batch job that runs **once per quarter** when FDA publishes a new extract.

---

## 4. Workstreams

### WS1 — Full-database ingestion (FAERS quarterly, at scale)

**Why.** This is the load-bearing change. Everything else depends on having the full
relational FAERS in silver.

**What to do.**
1. **Promote `faers_quarterly.py` to the default path.** Add an `ingestion_mode:
   quarterly_file` switch in `config/faers_quarters.yml` (already stubbed) and a
   `make ingest-faers QUARTERS="2004q1..2025q1"` driver that loops all quarters.
2. **Backfill the full history.** FAERS/AERS legacy goes back to 2004 (cumulative ~20M+
   reports). Decide a start year (recommend 2012+ for MedDRA-era consistency, or full
   history with a `legacy_aers` flag). Raw ASCII for all quarters ≈ tens of GB; Parquet
   silver compresses to a **few GB**.
3. **Case-version deduplication (critical, currently missing).** FAERS issues multiple
   versions of the same case across quarters; `caseid` repeats with increasing
   `primaryid`/`fda_dt`. Without dedup, counts are inflated. Add a silver step: keep the
   **latest version per `caseid`**, and delete cases superseded by the `DELETED` files FDA
   ships each quarter.
4. **Add the missing tables.** Extend the ASCII reader to also parse **THER** (drug
   therapy dates → enables time-to-onset analysis) and **RPSR** (report source →
   distinguishes consumer vs healthcare-professional vs literature reports, a major
   quality signal).
5. **Incremental ingestion.** After the initial backfill, each run downloads only the new
   quarter and appends a partition — keeps the quarterly job cheap and fast.
6. **Storage hygiene.** Keep bronze ZIPs in S3 with a lifecycle rule → Glacier Deep
   Archive after 30 days (raw is reproducible and rarely re-read), or prune entirely as
   the current AWS deployment already does. Silver/gold stay in S3 Standard.

**Effort 🟡 (the ingester exists) · Impact ⭐⭐⭐ (unblocks everything).**

---

### WS2 — Signal computation rewrite: set-based, whole-database, EBGM

**Why.** Replace the per-pair API loop with SQL that scores all ~5–15M pairs at once, and
upgrade the statistics to regulatory-grade.

**What to do.**
1. **Rewrite `build_gold` as aggregation queries.** From `silver/faers`:
   - per-drug totals (`a+b`), per-event totals (`a+c`), grand total `N` — three GROUP BYs;
   - the 2×2 for every co-occurring pair derived from a single drug×event count;
   - feed counts into the **existing vectorized formulas** in `signal_scores.py`
     (`Contingency`, `disproportionality`) — apply them column-wise over a DataFrame
     instead of per-pair in a Python loop.
   - DuckDB handles this comfortably on a few GB of silver on one machine; on Athena use
     `CREATE TABLE AS SELECT` (CTAS) to materialize gold directly in S3.
2. **Implement proper Empirical Bayes (MGPS / EBGM)** — `future_enhancements.md` #8. Fit
   the gamma-Poisson mixture once over the full contingency table; emit `ebgm` and `eb05`
   (5th-percentile) columns. EBGM is *the* method that makes full-database scoring
   trustworthy: it shrinks the millions of small-count pairs that otherwise dominate raw
   ROR. This is now far stronger because it operates over the real whole-DB
   marginal distribution, not 10 drugs.
3. **MedDRA hierarchy rollups (WS-linked).** Add `signal_scores` at PT level **and**
   rolled up to HLGT/SOC, so a user can ask "all hepatic events for drug X" not just one
   PT. Requires the MedDRA map (see WS3 licensing note).
4. **Minimum-cell + threshold gating at scale.** With millions of pairs, default the gold
   mart to pairs meeting `minimum_reports` and an EB05 floor; keep the full matrix in a
   separate "all pairs" Parquet for power users / Athena. This keeps the served marts
   small (cheap) while preserving completeness.
5. **Trend at scale.** Compute quarterly counts for emerging signals directly from the
   partitioned silver (one GROUP BY over `faers_quarter`), not per-quarter API calls.

**Effort 🔴 · Impact ⭐⭐⭐.**

---

### WS3 — Drug & reaction normalization at scale

**Why.** A 10-drug curated YAML cannot normalize ~5,000 ingredients with thousands of
messy FAERS spellings, and flat MedDRA PTs miss clinically grouped events.

**What to do.**
1. **RxNorm tiered resolver** (`future_enhancements.md` #2). Upgrade
   `transforms/normalize.py` to: exact dictionary → **RxNorm REST API** (`/rxcui?name=`,
   `/approximateTerm`) → fuzzy → unmatched, persisting `rxcui`, `ingredient`,
   `normalization_method`, `confidence`. Cache lookups in `bronze/rxnorm/`. This collapses
   brand/generic/combination variants to stable ingredient RxCUIs and gives true
   **ingredient-level rollups** (the analysis key for whole-DB scoring). Optionally pull
   the RxNorm **ATC** crosswalk to get free drug-class grouping for all 5,000 drugs.
2. **MedDRA hierarchy.** FAERS reactions are already MedDRA Preferred Terms. To group them
   (PT → HLT → HLGT → SOC) you need the MedDRA dictionary. **Licensing note:** MedDRA
   requires a subscription (free for some non-commercial/individual uses — verify
   eligibility at meddra.org). If licensing is a blocker, an interim option is to use
   **SOC groupings already exposed by openFDA's reaction field metadata** or a public
   SOC-mapping crosswalk, clearly flagged as approximate. Store the map in
   `bronze/meddra/` and join in silver.
3. **Standardised MedDRA Queries (SMQs)** as a stretch: predefined event groupings (e.g.
   "drug-induced liver injury") that analysts actually use — high credibility if MedDRA is
   licensed.

**Effort 🟡 (RxNorm) / 🔴 (MedDRA licensing) · Impact ⭐⭐⭐.**

---

### WS4 — PubMed at scale: thousands of articles + semantic relevance

**Why.** Today's PubMed step is per-pair keyword E-utilities for the top ~25 signals. To
"pull thousands of articles" and make literature genuinely useful at full scale, the
retrieval and matching must change.

**What to do.**
1. **Scale retrieval.** Get an **NCBI API key** (10 req/s) and retrieve literature for the
   top **N thousand** prioritized signals (by EB05 / priority score), batching `efetch`
   (up to 200 PMIDs/call) and respecting backoff (the 429 lesson is already captured).
   Cache everything in `bronze/pubmed/` (already done by query hash).
2. **Bulk baseline option.** For true corpus scale, ingest the annual **PubMed/MEDLINE
   baseline** (free bulk XML from NLM) into `silver/pubmed_articles` once, then match
   locally — eliminates per-query API volume entirely and lets you search offline.
3. **Semantic relevance via embeddings (big upgrade).** The current matcher is exact/
   word-AND keyword matching (clever British→American handling, but still lexical).
   Replace/augment with **embeddings**: embed each abstract and each drug-event query,
   store vectors, and rank by cosine similarity. This catches papers that describe the
   association without using the MedDRA phrasing. Store vectors in a lightweight vector
   index — **LanceDB or DuckDB-VSS (file-based, $0 infra)** locally / in S3, or pgvector
   if you adopt Postgres (WS7). Embeddings via **Amazon Bedrock Titan Embeddings** or a
   local `sentence-transformers` model (free).
4. **Literature-support score** then becomes a blend of count + max semantic similarity +
   adverse-context terms, feeding the existing composite priority (`enrich_signals.py`
   already has the join seam — just richer inputs).

**Effort 🟡–🔴 · Impact ⭐⭐⭐.**

---

### WS5 — Deep NHANES integration

**Why.** NHANES is currently 1 cycle, ~10 drugs, point estimates only. "Deep integration"
means broader coverage, real survey statistics, and actual analytic use beyond a context
table — while keeping the **non-negotiable rule: population context only, never
person-linked to FAERS**.

**What to do.**
1. **Multi-cycle ingestion.** Ingest all available public cycles with per-drug names
   (2017–2020 "P_" and earlier two-year cycles back to 1999), so prevalence trends over
   time are available and small-n drugs aggregate across cycles. Generalize
   `nhanes/ingest.py` to loop cycles (the config is already cycle-keyed).
2. **Proper complex-survey variance.** Today only point prevalence is computed (the code
   honestly notes it omits strata/PSU variance). Add **Taylor-linearization / replicate-
   weight** standard errors and 95% CIs using the `samplics` library (or `statsmodels`
   survey support) and the existing `SDMVSTRA`/`SDMVPSU` design vars. This turns
   "6.21%" into "6.21% (95% CI 5.1–7.4)" — a real credibility upgrade.
3. **Wide drug coverage via RxNorm.** Reuse the WS3 RxNorm resolver to map NHANES
   `RXDDRUG` strings, so population context exists for *all* mapped FAERS drugs, not just
   10 hand-listed ones.
4. **Analytic payoff — demographic representativeness.** Compare the demographic profile
   of a drug's **FAERS reporters** vs its **NHANES users** (age/sex/BMI/diabetes/HbA1c).
   "Reports skew heavily female vs the actual user base" is a genuine reporting-bias
   insight, and a differentiator. Add comorbidity (DIQ, BPQ, MCQ) and polypharmacy
   covariates from NHANES questionnaires.

**Effort 🟡 · Impact ⭐⭐⭐.**

---

### WS6 — Additional data sources (compelling research tool)

Prioritized by impact-to-effort; each plugs into the existing silver/gold + composite
priority without reworking the medallion:

1. **openFDA Drug Label — labeled vs. novel** ✅ already implemented; at full scale this
   becomes hugely valuable (auto-triage of millions of pairs into "known" vs "potentially
   novel"). Just run it over the full `signal_scores`.
2. **ClinicalTrials.gov API** (`future_enhancements.md` #5) — "is this adverse event under
   active investigation?" Free API; adds an investigation dimension on the evidence page.
3. **DailyMed / FDA approval dates** (#4) — annotate trend charts with launch dates to
   distinguish *stimulated reporting* spikes from real safety changes; suppress anomaly
   scores within N quarters of launch.
4. **WHO VigiAccess / EMA EudraVigilance** (#3) — cross-database "corroborated in EU" flag;
   strongest signals appear in both. Limited APIs → scraping/summary counts; higher
   effort.
5. **RxNorm ATC class crosswalk** — free, gives drug-class grouping for all drugs (WS3).
6. **CMS / Medicare Part D or FDA utilization data** — denominator context (prescriptions
   dispensed) to move toward reporting *rates*, not just disproportionality. Stretch.

---

### WS7 — Storage & database choices at scale

The Parquet + Athena/DuckDB lakehouse stays the backbone. Decisions to make:

- **Keep gold marts small and served-friendly.** Materialize a "flagged signals" gold
  Parquet (the served product, tens of MB) plus a full "all pairs" Parquet for Athena/
  power use. The dashboard/API never scan the full matrix.
- **Search/serving store.** Two viable paths:
  - **(A, cheapest) Stay file-based:** DuckDB over S3 Parquet for the API, with a small
    SQLite/DuckDB index for drug/event autocomplete. ~$0 beyond S3.
  - **(B, richer) Add a small managed Postgres** (Supabase free tier, or RDS — but RDS
    isn't "a few dollars"; prefer Supabase/Neon free tier). Gives indexed search,
    pagination, and **pgvector** for PubMed embeddings in one place. Recommended if the
    frontend needs fast full-text search across millions of pairs.
- **Vector store for literature.** LanceDB / DuckDB-VSS (file-based, $0) or pgvector if
  you adopt Postgres.
- **Partitioning discipline** keeps Athena scans cheap: silver partitioned by
  `year`/`quarter`; gold kept tiny via precompute (already the design ethos).

**Recommendation:** start with **(A) file-based** to stay at a few dollars; add Supabase
free-tier Postgres + pgvector only if/when frontend search demands it.

---

### WS8 — Compute & orchestration (this is where cost lives)

The data is cheap to store; the **quarterly rebuild compute** is the only real cost lever.

- **One quarterly batch job.** Ingest new quarter → dedup → score (SQL) → enrich → publish
  gold. Options:
  - **GitHub Actions** (current MVP path): 2,000 free min/month. A full-DB DuckDB rebuild
    on a few GB may exceed the 7 GB / 6 hr runner limits for the *initial backfill* but is
    fine for *incremental quarterly* runs. **$0** within free tier.
  - **AWS Batch on Fargate Spot** or a one-shot **EC2 Spot** instance for the heavy
    initial backfill (a 16–32 GB box for an hour costs **cents**). Recommended for the
    backfill, then hand off to GitHub Actions / a small scheduled Fargate task for
    quarterly increments.
  - **AWS Step Functions** to chain ingest → score → enrich → publish with retries; cheap
    (first 4,000 transitions/month free).
- **Schedule.** FAERS publishes quarterly, so a cron 4×/year (plus PubMed/label refresh)
  is enough. Use EventBridge or the existing GitHub Actions schedule.
- **Idempotency & cost telemetry.** The pipeline already writes `estimated_cost_usd` and a
  `run_id` into `pipeline_health` — extend it to record bytes scanned and job seconds so
  cost stays observable on the Health page.

---

### WS9 — Serving / API / dashboard at scale

- **API (`api/`).** Today `service.py` loads whole gold tables into Lambda memory and
  caches with a TTL — fine for tiny marts, not for millions of pairs. Add:
  - **pagination + filtering** on `/signals` (by drug, event, SOC, label status, priority);
  - a **search endpoint** backed by Athena (or Postgres) instead of in-memory scans;
  - keep the served marts small (flagged signals) and push "all pairs" queries to Athena.
- **Dashboard (`dashboard/`).** Replace any "load full table" pages with **server-side
  paginated / lazy** queries; add typeahead drug/event search; keep the responsible-use
  disclaimers on every page (they remain essential at scale).
- **Frontend (Next.js/Vercel).** Already ISR-cached against the API; add search UX and a
  SOC/drug-class browse tree.

---

### WS10 — AI / LLM layer (Bedrock + Claude) — the "real insights" differentiator

This is what turns a big dashboard into a research tool. All optional, all batch (so cost
is controlled), all reading gold:

1. **Signal narrative summaries.** For each high-priority signal, have Claude (via
   **Amazon Bedrock**, `claude-opus-4-8` or a cheaper Haiku for volume) generate a short,
   sourced summary: the disproportionality numbers, label status, retrieved literature,
   NHANES context — *with explicit hypothesis-generating-only framing*. Cache to a
   `gold_signal_narratives` table. Pennies per run if scoped to top-N signals.
2. **Duplicate / case-cluster detection.** The live deployment already found that top-ROR
   pairs are often mass-reporting artifacts. Use embeddings + Claude to flag suspected
   duplicate report clusters as a quality signal feeding the priority score.
3. **RAG over the literature corpus.** With WS4 embeddings in a vector store, add a "ask
   about this drug-event pair" RAG endpoint that answers from retrieved abstracts with
   citations — grounded, not free-form.
4. **MedDRA/free-text normalization assist.** Use an LLM only for the *unmatched* residual
   of RxNorm/MedDRA mapping, with confidence flags — never as the primary path (keep
   normalization explainable).

> Guardrail: every AI output must carry the same hypothesis-generating, not-causal
> disclaimer the statistical pages already use, and must cite its sources. Use Bedrock so
> data stays in your AWS account.

---

## 5. Cost model at full scale (target: a few $/month)

| Resource | Driver | Est. monthly |
|---|---|---|
| S3 storage — silver+gold Parquet (~3–6 GB) | $0.023/GB | **~$0.10–0.15** |
| S3 storage — bronze raw ZIPs (tens of GB) → Glacier Deep Archive | $0.00099/GB | **~$0.05** (or prune → $0) |
| S3 requests | low (batch + cached serving) | **< $0.10** |
| Athena | $5/TB scanned; partitioned + precomputed gold keeps scans tiny | **< $1** |
| Quarterly compute — Fargate Spot / EC2 Spot (backfill) | cents/run, 4×/yr | **< $0.50 amortized** |
| Incremental rebuild — GitHub Actions | free tier | **$0** |
| Lambda API + HTTP API Gateway | low traffic, free-tier-ish | **~$0–0.50** |
| Bedrock (Claude) narratives — top-N, batch | Haiku pennies; Opus for few | **~$0.50–2** (scope-controlled) |
| NCBI E-utils / openFDA / NHANES / RxNorm / ClinicalTrials | all free | **$0** |
| Vector store (LanceDB/DuckDB-VSS file-based) | on S3 | **~$0** |
| **Total** | | **≈ $2–4 / month** |

**The cost discipline that makes this work:** precompute everything into small gold marts,
serve only gold, run heavy compute as an infrequent batch job, cache all external API
responses in bronze, and keep AI calls scoped to top-N signals in batch. None of it is
always-on.

Biggest cost risks to watch: (1) Athena queries that scan the *full* matrix instead of the
flagged gold mart — enforce the workgroup bytes-scanned cap (already set to 1 GiB);
(2) unbounded Bedrock usage — cap to top-N and prefer Haiku; (3) the initial full-history
backfill — do it once on Spot, not repeatedly.

---

## 6. Phased roadmap

**Phase 0 — De-risk the pivot (1 week).**
Wire `faers_quarterly.py` into a new `build_gold_bulk` that scores from silver via SQL for
a **single recent quarter, all drugs**. Prove the whole-DB scoring path end-to-end at small
scale. Add case-version dedup. ✅ Exit: gold `signal_scores` for one quarter, all drugs.

**Phase 1 — Full backfill + scale statistics (1–2 weeks).**
Backfill chosen history on EC2/Fargate Spot; implement EBGM/EB05; min-count + EB05 gating;
quarterly trend from silver. Run label flags over the full matrix. ✅ Exit: whole-database
`signal_scores` + `emerging_signals` in S3, served via existing API/dashboard with
pagination.

**Phase 2 — Normalization & grouping (1–2 weeks).**
RxNorm tiered resolver + ATC classes; MedDRA hierarchy (subject to licensing) with SOC
rollups and SMQs. ✅ Exit: ingredient-level rollups, SOC browse.

**Phase 3 — Deep PubMed + NHANES (2–3 weeks).**
NCBI key + batched retrieval for top-N-thousand signals (or MEDLINE baseline); embeddings +
vector search; multi-cycle NHANES with survey-variance CIs and FAERS-vs-NHANES
representativeness. ✅ Exit: thousands of articles indexed; NHANES CIs and reporting-bias
view.

**Phase 4 — AI layer + new sources (ongoing).**
Bedrock/Claude signal narratives + RAG; ClinicalTrials.gov, DailyMed approval dates,
EU corroboration. ✅ Exit: sourced AI summaries and corroboration flags on top signals.

---

## 7. Risks, constraints, and ethics

- **MedDRA licensing** is the main external blocker — confirm eligibility before depending
  on the hierarchy; have the SOC-approximation fallback ready.
- **Deduplication correctness** materially affects every count — validate against FDA's
  published quarterly report totals.
- **Disproportionality ≠ causality.** At whole-database scale the temptation to over-read
  ROR grows; EB05 gating, the composite priority, and the existing per-page disclaimers
  must stay front-and-center, and AI narratives must repeat them.
- **NHANES stays population-only** — never person-linked to FAERS (existing non-negotiable).
- **Reporting biases** (stimulated reporting, notoriety, mass tort campaigns) should be
  surfaced (RPSR source, launch-date annotation), not hidden.

---

## 8. Concrete first PRs (smallest valuable steps)

1. `config/faers_quarters.yml`: flip default to `quarterly_file`; add a `make ingest-faers`
   range driver looping `faers_quarterly.build_silver_from_quarter`.
2. New `pipeline/build_gold_bulk.py`: SQL aggregation from `silver/faers` → `signal_scores`
   reusing `modeling/signal_scores.py` vectorized; **no API calls**.
3. Silver dedup step: latest version per `caseid` + apply `DELETED` lists.
4. `transforms/normalize.py`: add RxNorm resolver tier behind a cache.
5. `modeling/`: add `ebgm()` / `eb05()` estimator + columns in gold + a dashboard ranking
   toggle.

These five land the architectural pivot and prove the cost model before any of the larger
AI/NHANES/PubMed expansion.
