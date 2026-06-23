# PharmaSignal

> A cloud-native pharmacovigilance, population-context, and biomedical-literature
> intelligence platform that detects, prioritizes, and contextualizes emerging drug
> safety signals from **FDA FAERS / openFDA**, **NHANES**, and **PubMed** — built on a
> medallion lakehouse with statistical signal detection, NLP, and interactive dashboards.
> Initial domain: **metabolic / GLP-1 therapies**; architecture scales to whole-database FAERS.

⚠️ **Research tool — not clinical advice.** FAERS signals are
*hypothesis-generating reporting associations*, not causality, incidence, or
prevalence. See [docs/limitations.md](docs/limitations.md).

---

## Quickstart (offline, no cloud, no API keys)

```bash
make install        # or: FDA-AE/bin/pip install -r requirements.txt
make demo           # generate the bundled synthetic gold dataset
make dashboard      # launch Streamlit -> http://localhost:8501
make test           # 43 unit + API + Streamlit smoke tests
```

The dashboard ships with a deterministic demo dataset — explore it with **zero credentials**.
To use live data, run the pipeline:

```bash
export OPENFDA_API_KEY=...    # optional, raises rate limit
make pipeline                 # openFDA -> data/gold/*.parquet  (API / demo scope)
make labels                   # labeled-vs-novel flags
make subgroups                # age/sex subgroup ROR
make interactions             # drug-drug interaction signals
make nhanes                   # NHANES population context
make pubmed                   # PubMed evidence for top signals
make enrich                   # recompute priority with all components
# or all at once:
make pipeline-full
```

For the full production path (all FAERS quarters, set-based SQL scoring):

```bash
make ingest-faers QUARTERS="2021q1..2024q4"   # FAERS ZIPs -> silver
make gold-bulk                                 # silver -> gold via DuckDB SQL
```

## What it does

| Engine | Output | Where |
|---|---|---|
| **Signal detection** | ROR, PRR, 95% CIs, χ², simplified empirical-Bayes shrinkage, disproportionality flags | `modeling/signal_scores.py` |
| **Trend / anomaly** | Quarterly trend, z-score, EWMA, Poisson anomaly, composite priority score | `modeling/`, `pipeline/` |
| **Label enrichment** | openFDA Drug Label API — labeled vs. novel per signal | `pipeline/build_label_flags.py` |
| **Subgroup signals** | ROR within sex + age-band strata | `pipeline/build_subgroups.py` |
| **Interaction signals** | Co-reported drug-pair ROR vs. single-agent baseline | `pipeline/build_interactions.py` |
| **Population context** | NHANES survey-weighted medication prevalence + demographic/clinical profiles | `nhanes/ingest.py` |
| **Literature evidence** | PubMed retrieval + transparent relevance & support scoring | `pubmed/` |
| **Streamlit dashboard** | 9 pages: Overview, Signal Explorer, Drug/Event Profile, Emerging Signals, Subgroup Signals, Drug Interactions, Literature, NHANES, Pipeline Health | `dashboard/` |
| **Serving API** | Read-only FastAPI layer over gold tables; decouples frontend from S3 | `src/pharmasignal/api/` |
| **Next.js frontend** | ISR-cached App Router frontend served from Vercel | `frontend/` |

## Architecture

Medallion lakehouse — **bronze** (immutable raw) → **silver** (cleaned, normalized) →
**gold** (analytics marts). Runs locally on DuckDB + Parquet; the same design scales to
S3 + Athena (current cloud deployment) or Spark + EMR Serverless (full FAERS backfill).
The dashboard and API read **only precomputed gold** — serving stays fast and cheap
regardless of raw data scale.

```
src/pharmasignal/
  ingestion/   openfda.py · faers_quarterly.py · drug_label.py · stage_faers.py
  transforms/  normalize.py
  modeling/    signal_scores.py       # pure, unit-tested statistics
  nhanes/      ingest.py
  pubmed/      eutils.py · relevance.py · build_evidence.py
  quality/     checks.py
  serving/     lakehouse.py · storage.py
  pipeline/    build_gold.py · build_gold_bulk.py · build_label_flags.py
               build_subgroups.py · build_interactions.py · enrich_signals.py
               generate_demo.py · scoring.py
  api/         main.py · service.py   # FastAPI serving layer
spark/         jobs/ingest_faers_spark.py · build_gold_spark.py   # EMR Serverless path
config/        drugs_of_interest.yml · signal_thresholds.yml · faers_quarters.yml · ...
dashboard/     app.py + pages/
frontend/      Next.js App Router (Vercel)
infrastructure/ terraform/ · aws_deploy.py · api_deploy.py · spark_backfill.py
docs/          architecture · deployment · roadmap · methodology · data_dictionary · limitations
tests/         43 tests
```

Full diagram: [docs/architecture.md](docs/architecture.md).

## Data sources & caveats

| Source | Use | Caveat |
|---|---|---|
| [openFDA Drug Event API](https://open.fda.gov/apis/drug/event/) | Prototyping, on-demand drill-downs | Spontaneous reports; no denominator. |
| [FAERS Quarterly Files](https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html) | Production batch ingestion | Schema drift across quarters; case-version dedup required. |
| [NHANES](https://wwwn.cdc.gov/nchs/nhanes/search/datapage.aspx) | Population context | Survey weights required; never person-linked to FAERS. |
| [PubMed / NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25501/) | Literature support | Co-occurrence ≠ consensus. |
| [openFDA Drug Label API](https://open.fda.gov/apis/drug/label/) | Labeled-vs-novel enrichment | Text matching heuristic, not regulatory determination. |

## Methodology (summary)

ROR = (a/b)/(c/d) with delta-method 95% CI; PRR = [a/(a+b)]/[c/(c+d)]; 0.5 Haldane–Anscombe
continuity correction on zero cells; simplified empirical-Bayes shrinkage of log(O/E);
composite priority `0.30·D + 0.25·T + 0.20·S + 0.15·L + 0.10·P`.
Full derivations: [docs/methodology.md](docs/methodology.md).

## Deployment

### Live AWS deployment (current)
- S3 lakehouse `pharmasignal-data-<suffix>`, Glue DB `pharmasignal`, Athena workgroup with
  1 GiB/query guardrail, Lambda + HTTP API Gateway serving the FastAPI layer.
- **11 gold tables** registered in Glue/Athena; 999 drug-event pairs, 562 flagged.
- Serving stack: Next.js (Vercel) → FastAPI (Lambda) → gold Parquet on S3.

For setup: [docs/deployment.md](docs/deployment.md).
Cost breakdown: included in [docs/deployment.md](docs/deployment.md#cost).

## Roadmap

See [docs/roadmap.md](docs/roadmap.md) for the current implementation status and the
prioritized work items for scaling to whole-database FAERS, EBGM, RxNorm normalization,
deep PubMed/NHANES integration, and the Bedrock/Claude AI layer.

## License

MIT (educational use). FAERS, NHANES, and PubMed data are subject to their respective terms.
