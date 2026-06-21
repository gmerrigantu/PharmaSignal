# PharmaSignal — Data Dictionary

Raw FAERS source terms are **always preserved**; normalized fields are added, never
overwritten (§6.4). Below are the gold tables consumed by the dashboard. Silver table
schemas mirror requirements §6.2.

## gold_drug_event_counts
| Column | Type | Description |
|---|---|---|
| drug_name_normalized | string | Canonical drug name. |
| drug_class | string | Class key from `drugs_of_interest.yml`. |
| adverse_event | string | MedDRA preferred term (reaction). |
| scoring_window_start / _end | date | Scoring period bounds. |
| report_count | int | Reports with this drug+event (= `a`). |
| serious_count | int | Of those, flagged `serious:1`. |
| distinct_case_count | int | Distinct cases (report-level in API mode). |

## gold_signal_scores
| Column | Type | Description |
|---|---|---|
| drug_name_normalized, drug_class, adverse_event | string | Pair identity. |
| a_drug_event, b_drug_other_events, c_other_drugs_event, d_other_drugs_other_events | int | 2×2 cells. |
| prr, ror | double | Disproportionality point estimates. |
| ror_ci_lower, ror_ci_upper | double | 95% CI for ROR. |
| chi_square | double | Yates-corrected χ². |
| expected_count | double | E[a] under independence. |
| bayesian_shrunken_score | double | Simplified empirical-Bayes shrinkage (log scale). |
| seriousness_rate | double | serious_count / a. |
| min_count_flag | bool | a below minimum-reports threshold. |
| disproportionality_flag | bool | Passes the configurable signal rule. |
| continuity_correction | bool | 0.5 correction applied. |

## gold_emerging_signals
| Column | Type | Description |
|---|---|---|
| drug_name_normalized, drug_class, adverse_event | string | Pair identity. |
| current_quarter | string | Most recent scoring quarter. |
| current_count | int | Reports in current quarter. |
| trailing_baseline_count | double | Mean of trailing baseline quarters. |
| percent_change | double | vs baseline. |
| anomaly_score | double | Z-score vs baseline. |
| poisson_anomaly_score | double | P(X ≥ current | baseline rate). |
| seriousness_rate | double | Serious fraction. |
| literature_support_count | int | PubMed articles retrieved. |
| nhanes_context_available | bool | Population context exists for the class. |
| priority_score | double | Composite (see methodology §7). |
| priority_level | string | High / Moderate / Low. |

## gold_drug_label_flags
Labeled-vs-novel status per drug-event pair, from the openFDA Drug Label API. A
text-matching heuristic (British→American spelling aware), **not** a regulatory claim —
absence of a match is not proof of absence from labeling.
| Column | Type | Description |
|---|---|---|
| drug_name_normalized, adverse_event | string | Pair identity. |
| labeled_event | bool | Event appears in a label safety section. |
| label_section | string | Most-severe matching section (boxed_warning > contraindications > warnings_and_cautions > warnings > adverse_reactions > precautions), else null. |
| label_found | bool | A label was retrieved for the drug at all. |
| label_status | string | `labeled` / `novel` / `unknown`. |
| novel_flag | bool | label_found AND NOT labeled_event — the review-worthy case. |

## gold_subgroup_signals
Disproportionality recomputed within demographic strata (age band, sex).
| Column | Type | Description |
|---|---|---|
| drug_name_normalized, drug_class, adverse_event | string | Pair identity. |
| subgroup_type | string | `sex` or `age`. |
| subgroup | string | `male`/`female` or `0-17`/`18-64`/`65+`. |
| stratum_reports | int | `a` within the stratum. |
| stratum_population | int | Total reports in the stratum (the 2×2 denominator). |
| ror, ror_ci_lower, ror_ci_upper, prr, chi_square | double | Within-stratum disproportionality. |
| overall_ror | double | ROR in the full population (for comparison). |

## gold_interaction_signals
Co-reported drug-pair (interaction) reporting signals.
| Column | Type | Description |
|---|---|---|
| drug_a, drug_b | string | The co-reported drug pair. |
| adverse_event | string | Reaction term. |
| co_reports | int | Reports listing both drugs. |
| pair_event_reports | int | Reports with both drugs **and** the event. |
| ror_combination, ror_ci_lower, ror_ci_upper, prr_combination, chi_square | double | Disproportionality among both-drug reports. |
| ror_drug_a, ror_drug_b, single_max_ror | double | Single-agent RORs for comparison. |
| interaction_ratio | double | combination ROR ÷ stronger single-drug ROR. |
| interaction_flag | bool | ratio ≥ 2, lower CI > 1, sufficient reports — candidate interaction. |

## gold_nhanes_population_context
| Column | Type | Description |
|---|---|---|
| survey_cycle | string | e.g. 2021-2023. |
| drug_class, medication_name_normalized | string | Aligned to FAERS normalization. |
| weighted_prevalence | double | Survey-weighted use proportion. |
| estimated_users | double | Weighted population count (context, not denominator). |
| unweighted_sample_count | int | Reliability indicator. |
| median_age, female_percent, bmi_ge_30_percent, diabetes_percent, hba1c_median | double | User profile. |
| weight_variable_used | string | Which NHANES weight was applied. |
| small_n_flag, very_small_n_flag | bool | Instability flags (< 30 / < 10). |

## gold_pubmed_evidence
| Column | Type | Description |
|---|---|---|
| drug_name_normalized, adverse_event | string | Signal identity. |
| pmid, title, journal, publication_year | — | Citation metadata. |
| relevance_score | double | Transparent keyword/title score [0,1]. |
| evidence_snippet | string | Title/abstract excerpt. |
| mentions_drug, mentions_event, adverse_context | bool | Match components. |
| url | string | PubMed link. |

## gold_pipeline_health
| Column | Type | Description |
|---|---|---|
| run_id, run_timestamp | — | Run identity. |
| source, source_period, status | string | Provenance + outcome. |
| rows_raw, rows_silver, rows_gold | int | Stage row counts. |
| failed_checks, warning_checks | int | Data-quality summary. |
| duration_seconds, estimated_cost_usd | double | Operational metrics. |
| git_commit, notes | string | Lineage. |
