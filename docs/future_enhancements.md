# PharmaSignal — Future Enhancements & Ideas

A backlog of additional data sources and features that build on the current platform
(FAERS/openFDA + NHANES + PubMed, medallion lakehouse, ROR/PRR signal detection,
Streamlit dashboard, S3/Athena serving). Each entry notes **why** it matters, **how**
it plugs into the existing architecture, and a rough **effort/impact** read.

Legend — Effort: 🟢 low · 🟡 medium · 🔴 high. Impact is analytical value.

---

## New data sources

### 1. openFDA Drug Label API — "labeled vs. novel" flag ✅ IMPLEMENTED
**Why.** The single highest-value enrichment. For each flagged drug-event pair, check
whether the adverse event already appears in the official labeling (boxed warning,
warnings, adverse reactions, contraindications). This splits signals into **already
known** vs. **potentially novel** — the novel ones are what a pharmacovigilance analyst
actually cares about.
**Implemented as.** `ingestion/drug_label.py` (Drug Label API client + British→American
spelling-aware matcher), `pipeline/build_label_flags.py` → `gold_drug_label_flags`
(`labeled_event`, `label_section`, `label_status` = labeled/novel/unknown, `novel_flag`).
Surfaced on the Signal Explorer page as a **Label** column + status filter; registered in
Glue/Athena. `make labels`. Real run: 424 labeled / 575 novel across 999 pairs; the real
`semaglutide → optic ischaemic neuropathy` (NAION) signal correctly reads **novel**.
**Effort 🟢 · Impact ⭐⭐⭐.** Listed as an optional source in the spec (§3.2).

### 2. RxNorm (NLM) — real drug-name normalization
**Why.** Normalization is currently a curated YAML dictionary. RxNorm maps brand /
generic / ingredient variants to stable concept identifiers (RxCUI), capturing messy
FAERS spellings we didn't hand-list and enabling true ingredient-level rollups.
**How.** Upgrade `transforms/normalize.py` to a tiered resolver: exact dictionary →
RxNorm API → fuzzy → manual, persisting `normalization_method` + `confidence` (the
tiers the spec already asks for in §8.1). Cache RxNorm lookups in bronze.
**Effort 🟡 · Impact ⭐⭐.**

### 3. WHO VigiAccess / EMA EudraVigilance — cross-database corroboration
**Why.** A signal present in **both** FDA FAERS and the European database is far stronger
than one in either alone. Even public summary counts give a "corroborated in EU" flag.
**How.** New ingestion module producing `gold_cross_database_corroboration`; add a
`corroborated_eu` boolean that feeds the composite priority score.
**Effort 🔴 (scraping/limited APIs) · Impact ⭐⭐⭐.**

### 4. DailyMed / FDA Orange Book — structured labels + approval dates
**Why.** Drug **approval/launch dates** are genuinely useful for the trend engine: a
report spike right after launch is *stimulated reporting*, not a safety change.
Annotating trend lines with launch dates avoids that false-alarm class.
**How.** Add `approval_date` to the drug dimension; annotate trend charts; optionally
suppress/flag anomaly scores within N quarters of launch.
**Effort 🟡 · Impact ⭐⭐.**

### 5. ClinicalTrials.gov — active-investigation evidence
**Why.** Complements PubMed: for a drug-event pair, are there active/completed trials
studying that adverse event? Adds an "under active investigation" dimension.
**How.** New `pubmed/`-style module hitting the ClinicalTrials.gov API; surface trial
counts + links on the Literature Evidence page alongside citations.
**Effort 🟡 · Impact ⭐⭐.**

---

## New features

### 6. Drug–drug interaction / polypharmacy signals ✅ IMPLEMENTED
**Why.** FAERS reports list *multiple* drugs per case; we currently score each drug
independently. Detecting **co-reported drug pairs** associated with an event beyond what
either drug explains alone is a legitimate advanced pharmacovigilance method — and a
strong differentiator from a generic dashboard.
**Implemented as.** `pipeline/build_interactions.py` → `gold_interaction_signals`: for
each co-reported drug pair it computes the event's ROR among reports listing **both**
drugs and compares it to each single-agent ROR (`interaction_ratio` = combo ÷ stronger
single), flagging when the combination materially exceeds both. New **Drug Interactions**
dashboard page (combo-vs-single scatter). `make interactions`.
**Effort 🔴 · Impact ⭐⭐⭐.**

### 7. Demographic subgroup signal detection ✅ IMPLEMENTED
**Why.** "This signal is concentrated in women over 65" is exactly the kind of clinical
insight that makes a dashboard credible.
**Implemented as.** `pipeline/build_subgroups.py` → `gold_subgroup_signals`: recomputes
ROR/PRR within sex (M/F) and age-band (0–17 / 18–64 / 65+) strata for the strongest base
signals, with strata marginals cached to bound API calls. New **Subgroup Signals**
dashboard page comparing each stratum's ROR to the overall. `make subgroups`.
**Effort 🟡 · Impact ⭐⭐⭐.**

### 8. Proper Empirical Bayes (MGPS / EBGM)
**Why.** We have *simplified* shrinkage today. Implementing the industry-standard
gamma-Poisson EBGM lets the project honestly claim the regulatory-grade method, as a
clean, self-contained implementation. Explicitly flagged in the spec (§9.4) as
the "advanced modeling" upgrade.
**How.** New estimator in `modeling/`; add `ebgm`, `eb05` (5th percentile) columns to
`gold_signal_scores`; offer EBGM as an alternate ranking in the dashboard.
**Effort 🔴 · Impact ⭐⭐⭐.**

### 9. Alerting / change-detection between pipeline runs
**Why.** Turns a static dashboard into a living monitoring system: "5 signals newly
crossed the priority threshold this quarter."
**How.** Persist each run's gold tables (versioned by run date in S3) and **diff** them;
generate a digest from `gold_emerging_signals`; optionally email/Slack via the existing
GitHub Actions schedule.
**Effort 🟡 · Impact ⭐⭐.**

### 10. FastAPI serving layer
**Why.** The `/signals`, `/drugs/{drug}`, `/events/{event}`, `/evidence`, `/emerging`,
`/nhanes/context`, `/health` endpoints from §13.1. Turns "a Streamlit app" into "a data
product with an API" — a meaningfully stronger engineering story.
**How.** New `src/pharmasignal/serving/api.py` (FastAPI) reading the same gold tables;
containerize and deploy to ECS Fargate / App Runner alongside the dashboard.
**Effort 🟡 · Impact ⭐⭐.**

---

## Suggested prioritization (effort-to-impact)

1. **#1 Drug Label "labeled vs. novel" flag** — 🟢 small, immediately makes every signal
   interpretable; slots straight into `gold_signal_scores` + Signal Explorer.
2. **#7 Age/sex subgroup signals** — data is already in `silver_reports`; high leverage.
3. **#6 Drug-interaction signals** — the genuine differentiator that demonstrates real
   pharmacovigilance understanding.
4. **#8 EBGM** — for the strongest applied-statistics story when time allows.

> All of these are additive: they extend existing silver/gold tables, dashboard pages,
> or the composite priority score (§9.6) without reworking the medallion architecture.
