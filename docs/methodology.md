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

In API mode the cells are assembled from openFDA marginal counts:
`a = count(drug ∧ event)`, `drug_total = count(drug)`, `event_total = count(event)`,
`N = count(all)`, then `b = drug_total − a`, `c = event_total − a`, `d = N − a − b − c`
(clamped at 0). Implemented in `Contingency.from_totals`.

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

Deliberately named *simplified empirical Bayes shrinkage* — **not** EBGM/MGPS — to
avoid implying regulatory equivalence.

## 6. Trend / anomaly (emerging signals)

Per pair we compute quarterly counts and compare the current quarter to a trailing
baseline of `trend_baseline_quarters` (default 4):

- **Percent change** `(current − mean)/mean`
- **Z-score** `(current − mean)/std`
- **EWMA** (α = 0.5) to smooth noisy series
- **Poisson anomaly** `P(X ≥ current | baseline mean)` — probability the current
  count is that high under the baseline rate.

## 7. Composite priority score

```
priority = 0.30·D + 0.25·T + 0.20·S + 0.15·L + 0.10·P
```

D = normalized disproportionality (shrinkage), T = normalized trend anomaly,
S = seriousness rate, L = literature support, P = population-context score — each in
[0, 1]. Weights live in `config/signal_thresholds.yml`. **Components are always shown;
the composite never hides them.** Levels: High ≥ 0.75, Moderate ≥ 0.50, else Low.

## 8. NHANES population context (§10)

NHANES supplies **aggregate** population context: survey-weighted medication-use
prevalence, estimated users, and demographic/clinical profiles (age, sex, BMI, HbA1c,
diabetes). Key rules:

- Use the documented survey weight (MEC weight `WTMEC2YR` when merging exam/lab data).
- Always display the **unweighted sample count**; flag small-n (< 30) and very-small-n
  (< 10) estimates as unstable.
- **Never** join NHANES participants to FAERS reports — only aggregate context joins
  to drug/class outputs.
- The optional *population-context reporting index* (FAERS volume ÷ NHANES exposure)
  is **not** an incidence rate and is labeled as such.

## 9. PubMed literature support (§11)

Transparent, keyword/title-based relevance scoring (BM25/embeddings are a documented
upgrade path). The literature-support score and `None/Weak/Moderate/Strong` labels
describe **retrieval support**, not clinical evidence strength. PMIDs and article
links are always shown.

## Recommended dashboard wording

> PharmaSignal analyzes spontaneous adverse event reports from FAERS/openFDA. It
> identifies reporting patterns and statistical disproportionality for drug-event
> pairs. These outputs are hypothesis-generating only. They do not establish
> causality, clinical risk, incidence, prevalence, or regulatory conclusions. NHANES
> estimates are aggregate population context and are not linked to FAERS reports at
> the person level.
