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

export type DashboardData = {
  generated_at: string;
  data_source: "demo" | "pipeline" | "s3" | "api";
  signal_scores: SignalScore[];
  emerging_signals: EmergingSignal[];
  nhanes_population_context: NhanesContext[];
  pubmed_evidence: LiteratureArticle[];
  pipeline_health: PipelineHealth[];
  data_quality_checks: QualityCheck[];
};

export type Filters = {
  drugClass: string;
  query: string;
  priority: PriorityLevel | "All";
  minReports: number;
  showFlaggedOnly: boolean;
};
