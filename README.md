# PharmaSignal

> A cloud-native pharmacovigilance, population-context, and biomedical-literature
> intelligence platform that detects, prioritizes, and contextualizes emerging drug
> safety signals from **FDA FAERS / openFDA**, **NHANES**, and **PubMed** — built on a
> medallion lakehouse with statistical signal detection, NLP, and an interactive
> Streamlit dashboard. Initial domain: **metabolic / GLP-1 therapies**.

⚠️ **Educational / portfolio project — not clinical advice.** FAERS signals are
*hypothesis-generating reporting associations*, not causality, incidence, or
prevalence. See [docs/limitations.md](docs/limitations.md).

---

## Quickstart (offline, no cloud, no API keys)

```bash
make install        # or: FDA-AE/bin/pip install -r requirements.txt
make demo           # generate the bundled synthetic gold dataset
make dashboard      # launch Streamlit -> http://localhost:8501
make test           # 24 unit tests for the formulas + data contracts
```

The dashboard ships with a deterministic demo dataset so reviewers can explore it
with **zero credentials**. To use live data, run the pipeline:

```bash
export OPENFDA_API_KEY=...    # optional, raises rate limit
make pipeline                 # openFDA -> data/gold/*.parquet (Phases 1-3)
make nhanes                   # NHANES population context (downloads XPT)
make pubmed                   # PubMed evidence for top signals
```

## What it does

| Engine | Output | Where |
|---|---|---|
| **Signal detection** | ROR, PRR, 95% CIs, χ², simplified empirical-Bayes shrinkage, disproportionality flags | `modeling/signal_scores.py` |
| **Trend / anomaly** | Quarterly trend, z-score, EWMA, Poisson anomaly, composite priority score | `modeling/`, `pipeline/build_gold.py` |
| **Population context** | NHANES survey-weighted medication prevalence + demographic/clinical profiles | `nhanes/ingest.py` |
| **Literature evidence** | PubMed retrieval + transparent relevance & support scoring | `pubmed/` |
| **Dashboard** | 9 pages: Overview, Signal Explorer, Drug/Event Profile, Emerging Signals, Literature, NHANES, Pipeline Health, Methodology | `dashboard/` |

## Architecture

Medallion lakehouse — **bronze** (immutable raw) → **silver** (cleaned, normalized) →
**gold** (analytics marts). Runs locally on DuckDB + Parquet; the same design maps to
S3 + Athena or Databricks + Delta. The dashboard reads only precomputed gold tables.
Full diagram: [docs/architecture.md](docs/architecture.md).

```
src/pharmasignal/
  ingestion/   openfda.py · faers_quarterly.py
  transforms/  normalize.py
  modeling/    signal_scores.py        # pure, unit-tested statistics
  nhanes/      ingest.py
  pubmed/      eutils.py · relevance.py · build_evidence.py
  quality/     checks.py
  serving/     lakehouse.py            # Parquet write + DuckDB query
  pipeline/    build_gold.py · generate_demo.py
config/        drugs_of_interest.yml · signal_thresholds.yml · faers_quarters.yml · ...
dashboard/     app.py + pages/
docs/          architecture · methodology · data_dictionary · limitations · cost_estimate
infrastructure/ terraform/ + CLOUD_SETUP.md
tests/         24 tests
```

## Data sources & caveats
| Source | Use | Caveat |
|---|---|---|
| [openFDA Drug Event API](https://open.fda.gov/apis/drug/event/) | Prototyping, on-demand counts | Spontaneous reports; no denominator. |
| [FAERS Quarterly Files](https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html) | Production batch ingestion | Schema drift across quarters. |
| [NHANES](https://wwwn.cdc.gov/nchs/nhanes/search/datapage.aspx) | Population context | Survey weights required; never person-linked to FAERS. |
| [PubMed / NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25501/) | Literature support | Co-occurrence ≠ consensus. |

## Methodology (summary)
ROR = (a/b)/(c/d) with delta-method 95% CI; PRR = [a/(a+b)]/[c/(c+d)]; 0.5 continuity
correction on zero cells; shrinkage of log(observed/expected); composite priority
`0.30·D + 0.25·T + 0.20·S + 0.15·L + 0.10·P`. Full derivations and responsible-use
language: [docs/methodology.md](docs/methodology.md).

## Cloud deployment
Recommended MVP: local/GitHub-Actions compute → S3 gold tables → Streamlit Community
Cloud (≈ $0–1/month). Step-by-step: [infrastructure/CLOUD_SETUP.md](infrastructure/CLOUD_SETUP.md).
Costs: [docs/cost_estimate.md](docs/cost_estimate.md).

## Project status
See [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for what's implemented vs. the
phased roadmap and acceptance criteria, and
[docs/future_enhancements.md](docs/future_enhancements.md) for a backlog of additional
data sources and features (drug-label flags, RxNorm, subgroup signals, EBGM, drug-drug
interactions, FastAPI serving, and more).

## Resume bullets
- Built a cloud-native pharmacovigilance lakehouse integrating FDA FAERS, NHANES, and
  PubMed with bronze/silver/gold modeling, data-quality checks, and dashboard-ready
  gold marts (Parquet/DuckDB, S3/Athena-ready, IaC via Terraform).
- Developed drug-safety signal detection using ROR, PRR, confidence intervals,
  empirical-Bayes shrinkage, and time-series anomaly detection over adverse-event data.
- Contextualized FAERS signals with NHANES survey-weighted medication-exposure
  estimates and population clinical profiles (age, sex, BMI, HbA1c, diabetes).
- Integrated PubMed literature retrieval + relevance scoring to link high-priority
  signals to biomedical citations, with responsible, non-causal communication.

## License
MIT (educational use). FAERS, NHANES, and PubMed data are subject to their respective
terms.
