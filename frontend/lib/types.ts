export type PriorityLevel = "High" | "Moderate" | "Low";

export type SignalScore = {
  drug_name_normalized: string;
  adverse_event: string;
  drug_class: string;
  a_drug_event: number;
  ror: number;
  ror_ci_lower: number;
  ror_ci_upper: number;
  prr: number;
  chi_square: number;
  seriousness_rate: number;
  bayesian_shrunken_score: number;
  disproportionality_flag: boolean;
};

export type EmergingSignal = {
  drug_name_normalized: string;
  adverse_event: string;
  drug_class: string;
  current_count: number;
  trailing_baseline_count: number;
  percent_change: number | null;
  anomaly_score: number | null;
  seriousness_rate: number;
  priority_score: number;
  priority_level: PriorityLevel;
  literature_support_count: number;
  nhanes_context_available: boolean;
  current_quarter: string;
};

export type NhanesContext = {
  medication_name_normalized: string;
  drug_class: string;
  weighted_prevalence: number;
  unweighted_sample_count: number;
  median_age: number;
  female_percent: number;
  bmi_ge_30_percent: number;
  diabetes_percent: number;
  hba1c_median: number;
  small_n_flag: boolean;
  very_small_n_flag: boolean;
  survey_cycle: string;
  weight_variable_used: string;
};

export type LiteratureArticle = {
  drug_name_normalized: string;
  adverse_event: string;
  title: string;
  journal: string;
  publication_year: number;
  pmid: string;
  relevance_score: number;
  url: string;
  evidence_snippet: string;
};

export type PipelineHealth = {
  run_id: string;
  source: string;
  source_period: string;
  run_timestamp: string;
  status: "pass" | "warn" | "fail";
  rows_raw: number;
  rows_silver: number;
  rows_gold: number;
  failed_checks: number;
  warning_checks: number;
  estimated_cost_usd: number;
  notes: string;
};

export type QualityCheck = {
  table: string;
  check: string;
  category: string;
  status: "pass" | "warn" | "fail";
  detail: string;
};

export type InteractionSignal = {
  drug_a: string;
  drug_b: string;
  adverse_event: string;
  co_reports: number;
  pair_event_reports: number;
  ror_combination: number;
  ror_ci_lower: number;
  ror_ci_upper: number;
  prr_combination: number;
  chi_square: number;
  ror_drug_a: number | null;
  ror_drug_b: number | null;
  single_max_ror: number | null;
  comparable: boolean;
  interaction_ratio: number | null;
  interaction_flag: boolean;
};

export type SubgroupSignal = {
  drug_name_normalized: string;
  drug_class: string;
  adverse_event: string;
  subgroup_type: "sex" | "age";
  subgroup: string;
  stratum_reports: number;
  stratum_population: number;
  ror: number;
  ror_ci_lower: number;
  ror_ci_upper: number;
  prr: number;
  chi_square: number;
  overall_ror: number;
};

export type LabelStatus = "labeled" | "novel" | "unknown";

export type DrugLabelFlag = {
  drug_name_normalized: string;
  adverse_event: string;
  labeled_event: boolean;
  label_section: string | null;
  label_found: boolean;
  label_status: LabelStatus;
  novel_flag: boolean;
};

/** One page of the full signal_scores matrix (server-side pagination). */
export type SignalsPage = {
  total: number;
  offset: number;
  limit: number;
  rows: SignalScore[];
};

export type DashboardData = {
  generated_at: string;
  data_source: "demo" | "pipeline" | "s3" | "api";
  /** True totals over the FULL matrix — the table pages against /signals separately. */
  signal_total: number;
  flagged_total: number;
  novel_total: number;
  /** Bounded top-by-ROR sample for charts/dropdowns (NOT the whole matrix). */
  signal_sample: SignalScore[];
  emerging_signals: EmergingSignal[];
  nhanes_population_context: NhanesContext[];
  pubmed_evidence: LiteratureArticle[];
  pipeline_health: PipelineHealth[];
  data_quality_checks: QualityCheck[];
  /** Advanced marts — present on the cloud lakehouse; default to [] when absent. */
  interaction_signals?: InteractionSignal[];
  subgroup_signals?: SubgroupSignal[];
  drug_label_flags?: DrugLabelFlag[];
};

export type Filters = {
  drugClass: string;
  query: string;
  priority: PriorityLevel | "All";
  minReports: number;
  showFlaggedOnly: boolean;
  showNovelOnly: boolean;
};
