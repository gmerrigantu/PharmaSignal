# PharmaSignal — Limitations & Responsible Use

PharmaSignal is an **educational / portfolio** analytics platform. It is **not**
clinical advice, regulatory guidance, medical diagnosis, causal inference, or
incidence estimation.

## FAERS / openFDA
- A **spontaneous reporting system**: subject to underreporting, duplicate reports,
  reporting bias, stimulated reporting, missing data, and — critically — **no
  denominator**. Counts are *reports*, not patients or incidence.
- Disproportionality (ROR/PRR) measures **reporting association**, not causation. A
  drug listed in a report did not necessarily cause the reaction.
- Small counts inflate ratios; we mitigate with minimum-count thresholds, confidence
  intervals, and shrinkage — but residual noise remains.

## NHANES
- Cross-sectional survey; medication use is self-reported/reconciled at collection.
- Requires survey design variables and appropriate weights; full complex-survey
  variance (strata/PSU CIs) is documented but not computed in the MVP, so we avoid
  overstating precision.
- Newer drugs (e.g. tirzepatide) may have very small unweighted samples → estimates
  flagged unstable; fall back to drug-class or condition-proxy context.
- **Never** linked to FAERS at the person level.

## PubMed
- Citation/abstract availability varies; co-occurrence is **not** proof or clinical
  consensus. Relevance scores describe retrieval support only.

## Drug labels (optional)
- Labeling text is heterogeneous; absence of parsed text is not evidence of absence
  from official labeling without validation.

## Responsible communication rules (enforced in the UI)
| Risk | Mitigation in product |
|---|---|
| Causal misinterpretation | "reporting association", "signal", "hypothesis-generating" language; no causal claims. |
| Incidence misinterpretation | FAERS counts never called incidence; NHANES-normalized metric labeled "population-context reporting index". |
| Small-count noise | Minimum thresholds, CIs, shrinkage; unweighted NHANES counts shown. |
| Overconfident literature | Presented as retrieval support; PMIDs/links shown. |
| Sensationalism | Calm priority labels and caveats on every page. |

## Privacy & security
- Public datasets only; no PHI ingested; no re-identification attempts; no misleading
  small-cell drilldowns.
- Least-privilege cloud permissions; secrets via env vars / secret stores, never
  committed; public dashboard is read-only. See `infrastructure/CLOUD_SETUP.md`.
