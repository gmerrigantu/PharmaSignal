# PharmaSignal — Methodology

> **Responsible-use framing.** Every metric below is a *signal-prioritization* metric
> computed from spontaneous adverse-event reports. They are **hypothesis-generating
> only** and do not establish causality, clinical risk, incidence, or prevalence.

## 1. Reporting contingency table

For each drug-event pair, over a chosen window and (optionally) drug role:

|  | Event of interest | Other events |
|---|---|---|
| **Drug of interest** | a | b |
| **Other drugs** | c | d |

**API mode** (`build_gold.py`): cells assembled from openFDA marginal counts:
`a = count(drug ∧ event)`, `drug_total = count(drug)`, `event_total = count(event)`,
`N = count(all)`, then `b = drug_total − a`, `c = event_total − a`, `d = N − a − b − c`
(clamped at 0). Implemented in `Contingency.from_totals`.

**Bulk SQL mode** (`build_gold_bulk.py`): cells derived from three `GROUP BY` aggregations
over the silver FAERS tables — per-drug totals, per-event totals, and co-occurrence counts.
All statistics are then applied vectorized over the full drug × event DataFrame via the same
functions in `modeling/signal_scores.py` and `pipeline/scoring.py`.

## 2. Reporting Odds Ratio (ROR)

```
ROR = (a/b) / (c/d)
SE(log ROR) = sqrt(1/a + 1/b + 1/c + 1/d)
95% CI = exp( log ROR ± 1.96 · SE )
```

If **any** cell is zero, a Haldane–Anscombe **0.5 continuity correction** is added to
all four cells before computing ROR/PRR (`Contingency.corrected`).

## 3. Proportional Reporting Ratio (PRR)

```
PRR = [a/(a+b)] / [c/(c+d)]
```

PRR is shown alongside ROR for interpretability. A Yates-corrected χ² accompanies it.

## 4. Disproportionality flag (configurable)

From `config/signal_thresholds.yml` (defaults shown):

```
a ≥ 3  AND  ROR 95% lower CI > 1.0  AND  PRR ≥ 2.0  AND  χ² ≥ 4.0
```

A flag is a **prioritization rule**, never a clinical conclusion.

## 5. Simplified empirical-Bayes shrinkage

Low-count pairs inflate ROR/PRR. We shrink the log observed-to-expected ratio:

```
E[a] = (drug_total · event_total) / N
OE   = a / E[a]
score = w · log(OE),   w = a / (a + 0.5)
```

Deliberately named *simplified empirical-Bayes shrinkage* — **not** EBGM/MGPS — to
avoid implying regulatory equivalence. Upgrading to full gamma-Poisson MGPS (EBGM / EB05)
is the top P1 roadmap item; see [roadmap.md](roadmap.md).

## 6. Trend / anomaly (emerging signals)

Per pair we compute quarterly counts and compare the current quarter to a trailing
baseline of `trend_baseline_quarters` (default 4):

- **Percent change** `(current − mean)/mean`
- **Z-score** `(current − mean)/std`
- **EWMA** (α = 0.5) to smooth noisy series
- **Poisson anomaly** `P(X ≥ current | baseline mean)` — probability the current
  count is that high under the baseline rate.

In API mode, trend counts come from per-quarter openFDA `count` calls.
In bulk SQL mode, trend counts are derived from a single `GROUP BY faers_quarter`
aggregation over the partitioned silver tables.

## 7. Composite priority score

```
priority = 0.30·D + 0.25·T + 0.20·S + 0.15·L + 0.10·P
```

D = normalized disproportionality (shrinkage), T = normalized trend anomaly,
S = seriousness rate, L = literature support, P = population-context score — each in
[0, 1]. Weights live in `config/signal_thresholds.yml`. **Components are always shown;
the composite never hides them.** Levels: High ≥ 0.75, Moderate ≥ 0.50, else Low.

The full 5-component priority requires the enrichment step (`make enrich` /
`pipeline/enrich_signals.py`), which joins `pubmed_support_summary` and
`nhanes_population_context` back onto `emerging_signals`.

## 8. Labeled vs. novel

For each flagged drug-event pair, `build_label_flags.py` queries the openFDA Drug Label
API and checks whether the adverse event appears in a safety section (boxed warning,
contraindications, warnings_and_cautions, warnings, adverse_reactions, precautions) using
a British→American spelling-aware text matcher. Signals are classified as `labeled`,
`novel`, or `unknown`. **Novel signals are the primary review focus.** This is a text
heuristic, not a regulatory determination — absence of a match is not proof of absence.

## 9. Subgroup signals

`build_subgroups.py` recomputes ROR/PRR within demographic strata (sex: male/female;
age band: 0–17, 18–64, 65+) for the top base signals. Strata marginals are cached so
the computation remains bounded. Results are compared to the overall ROR to surface
demographic concentration.

## 10. Drug-drug interaction signals

`build_interactions.py` identifies co-reported drug pairs where the combination's ROR
for a given event materially exceeds each single-drug's ROR. The `interaction_ratio` is
combo ROR ÷ stronger single-drug ROR; flagged when ratio ≥ 2, lower CI > 1, and
sufficient reports exist. A real single-agent baseline must exist for the pair to be scored.

## 11. NHANES population context

NHANES supplies **aggregate** population context: survey-weighted medication-use prevalence,
estimated users, and demographic/clinical profiles (age, sex, BMI, HbA1c, diabetes).

Key rules:
- Use the documented survey weight (MEC weight `WTMEC2YR` when merging exam/lab data).
- Always display the **unweighted sample count**; flag small-n (< 30) and very-small-n
  (< 10) estimates as unstable.
- **Never** join NHANES participants to FAERS reports — only aggregate context joins
  to drug/class outputs.
- The optional *population-context reporting index* (FAERS volume ÷ NHANES exposure)
  is **not** an incidence rate and is labeled as such.
- Complex-survey variance (strata/PSU CIs) is documented in the roadmap but not yet
  computed — current estimates are point estimates only.

## 12. PubMed literature support

Transparent, keyword/title-based relevance scoring with British→American spelling
normalization. The literature-support score and `None/Weak/Moderate/Strong` labels
describe **retrieval support**, not clinical evidence strength. PMIDs and article links
are always shown. Upgrading to embedding-based semantic search is a P2 roadmap item.

---

## Recommended dashboard wording

> PharmaSignal analyzes spontaneous adverse event reports from FAERS/openFDA. It
> identifies reporting patterns and statistical disproportionality for drug-event
> pairs. These outputs are hypothesis-generating only. They do not establish
> causality, clinical risk, incidence, prevalence, or regulatory conclusions. NHANES
> estimates are aggregate population context and are not linked to FAERS reports at
> the person level.
