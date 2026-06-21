"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  BookOpen,
  CheckCircle2,
  Database,
  Download,
  Filter,
  HeartPulse,
  LineChart,
  Pill,
  RefreshCw,
  Search,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  Users,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart as ReLineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { compactNumber, fullNumber, percent, ratio, titleCase } from "@/lib/format";
import type { DashboardData, EmergingSignal, Filters, PriorityLevel, SignalScore } from "@/lib/types";

const nav = [
  { id: "Overview", icon: Activity },
  { id: "Explorer", icon: Search },
  { id: "Profiles", icon: Pill },
  { id: "Emerging", icon: LineChart },
  { id: "Evidence", icon: BookOpen },
  { id: "NHANES", icon: Users },
  { id: "Health", icon: Database },
  { id: "Method", icon: Settings2 },
] as const;

type Section = (typeof nav)[number]["id"];

const priorityColors: Record<PriorityLevel, string> = {
  High: "#c2415d",
  Moderate: "#b7791f",
  Low: "#87938f",
};

const chartGrid = "#e6ecea";
const chartAxis = "#71807b";
const chartAccent = "#087f6f";
const chartBlue = "#2f6faa";
const chartGold = "#b7791f";

export function DashboardClient({ data }: { data: DashboardData }) {
  const [section, setSection] = useState<Section>("Overview");
  const [filters, setFilters] = useState<Filters>({
    drugClass: "All",
    query: "",
    priority: "All",
    minReports: 50,
    showFlaggedOnly: false,
  });
  const [selectedDrug, setSelectedDrug] = useState(data.signal_scores[0]?.drug_name_normalized ?? "");
  const [selectedEvent, setSelectedEvent] = useState(data.signal_scores[0]?.adverse_event ?? "");
  const [selectedSignal, setSelectedSignal] = useState<EmergingSignal | undefined>(
    [...data.emerging_signals].sort((a, b) => b.priority_score - a.priority_score)[0],
  );

  const classes = useMemo(
    () => ["All", ...Array.from(new Set(data.signal_scores.map((d) => d.drug_class))).sort()],
    [data.signal_scores],
  );

  const filteredSignals = useMemo(() => {
    const query = filters.query.trim().toUpperCase();
    return data.signal_scores
      .filter((row) => filters.drugClass === "All" || row.drug_class === filters.drugClass)
      .filter((row) => row.a_drug_event >= filters.minReports)
      .filter((row) => !filters.showFlaggedOnly || row.disproportionality_flag)
      .filter((row) => {
        if (!query) return true;
        return `${row.drug_name_normalized} ${row.adverse_event}`.includes(query);
      })
      .sort((a, b) => b.ror - a.ror);
  }, [data.signal_scores, filters]);

  const emerging = useMemo(() => {
    return data.emerging_signals
      .filter((row) => filters.priority === "All" || row.priority_level === filters.priority)
      .sort((a, b) => b.priority_score - a.priority_score);
  }, [data.emerging_signals, filters.priority]);

  const latest = data.pipeline_health[0];
  const highPriority = data.emerging_signals.filter((row) => row.priority_level === "High").length;
  const flagged = data.signal_scores.filter((row) => row.disproportionality_flag).length;
  const highestPriority = Math.round((data.emerging_signals[0]?.priority_score ?? 0) * 100);
  const drugs = Array.from(new Set(data.signal_scores.map((row) => row.drug_name_normalized))).sort();
  const events = Array.from(new Set(data.signal_scores.map((row) => row.adverse_event))).sort();
  const selectedDrugRows = data.signal_scores.filter((row) => row.drug_name_normalized === selectedDrug);
  const selectedEventRows = data.signal_scores.filter((row) => row.adverse_event === selectedEvent);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <HeartPulse size={22} aria-hidden />
          </div>
          <div>
            <p className="brand-title">PharmacoSignal</p>
            <p className="brand-subtitle">Research signal review</p>
          </div>
        </div>

        <nav className="nav-list" aria-label="Dashboard sections">
          {nav.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={`nav-item ${section === item.id ? "active" : ""}`}
                key={item.id}
                onClick={() => setSection(item.id)}
                title={item.id}
              >
                <Icon size={17} aria-hidden />
                <span>{item.id}</span>
              </button>
            );
          })}
        </nav>

        <div className="sidebar-note">
          Hypothesis-generating only. FAERS reports are spontaneous reports and do not prove
          causality, incidence, prevalence, or clinical risk.
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <div className="status-pill">
            {latest?.status === "pass" ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
            <span>
              {data.data_source.toUpperCase()} source · refreshed{" "}
              {latest ? new Date(latest.run_timestamp).toLocaleString() : "unknown"}
            </span>
          </div>
          <div className="hero-actions">
            <button className="button secondary" title="Refresh data">
              <RefreshCw size={16} />
              Refresh
            </button>
            <button className="button secondary" title="Export current view">
              <Download size={16} />
              Export
            </button>
          </div>
        </div>

        <Hero highestPriority={highestPriority} highPriority={highPriority} setSection={setSection} />

        <section className="kpi-grid" aria-label="Executive metrics">
          <MetricCard icon={<BarChart3 size={18} />} label="Drug-event pairs" value={fullNumber(data.signal_scores.length)} trend={`${flagged} flagged`} />
          <MetricCard icon={<AlertTriangle size={18} />} label="High priority" value={fullNumber(highPriority)} trend="Composite feed" />
          <MetricCard icon={<Database size={18} />} label="FAERS indexed" value={compactNumber(latest?.rows_raw ?? 0)} trend={latest?.source_period ?? "current"} />
          <MetricCard icon={<BookOpen size={18} />} label="PubMed articles" value={fullNumber(data.pubmed_evidence.length)} trend="Evidence snippets" />
        </section>

        <FiltersPanel classes={classes} filters={filters} setFilters={setFilters} />

        <AnimatePresence mode="wait">
          <motion.div
            key={section}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.2 }}
          >
            {section === "Overview" && <Overview data={data} filteredSignals={filteredSignals} emerging={emerging} />}
            {section === "Explorer" && <Explorer rows={filteredSignals} />}
            {section === "Profiles" && (
              <Profiles
                drugs={drugs}
                events={events}
                selectedDrug={selectedDrug}
                setSelectedDrug={setSelectedDrug}
                selectedEvent={selectedEvent}
                setSelectedEvent={setSelectedEvent}
                selectedDrugRows={selectedDrugRows}
                selectedEventRows={selectedEventRows}
                data={data}
              />
            )}
            {section === "Emerging" && (
              <Emerging rows={emerging} selectedSignal={selectedSignal} setSelectedSignal={setSelectedSignal} />
            )}
            {section === "Evidence" && <Evidence data={data} selectedSignal={selectedSignal} />}
            {section === "NHANES" && <Nhanes data={data} />}
            {section === "Health" && <Health data={data} />}
            {section === "Method" && <Methodology />}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}

function Hero({
  highestPriority,
  highPriority,
  setSection,
}: {
  highestPriority: number;
  highPriority: number;
  setSection: (section: Section) => void;
}) {
  return (
    <section className="hero">
      <div className="hero-copy">
        <p className="eyebrow">FDA adverse event signal workspace</p>
        <h1>Research-grade pharmacovigilance signal review.</h1>
        <p>
          Review disproportionality, trend anomalies, literature support, population context,
          and pipeline quality in a focused interface built for researchers and safety analysts.
        </p>
        <div className="hero-actions">
          <button className="button" onClick={() => setSection("Explorer")}>
            <Search size={16} />
            Explore signals
          </button>
          <button className="button secondary" onClick={() => setSection("Emerging")}>
            <Sparkles size={16} />
            Review emerging feed
          </button>
        </div>
      </div>
      <div className="hero-panel">
        <div className="risk-dial">
          <div className="dial-ring">
            <div className="dial-inner">
              <span className="dial-value">{highestPriority}</span>
              <span className="dial-label">max priority score</span>
            </div>
          </div>
        </div>
        <div className="metric-card">
          <div className="metric-label">
            <span>High-priority queue</span>
            <AlertTriangle size={16} />
          </div>
          <div className="metric-value">{highPriority}</div>
          <div className="metric-trend">Ranked by disproportionality, trend, seriousness, literature, and context.</div>
        </div>
      </div>
    </section>
  );
}

function MetricCard({ icon, label, value, trend }: { icon: React.ReactNode; label: string; value: string; trend: string }) {
  return (
    <div className="metric-card">
      <div className="metric-label">
        <span>{label}</span>
        {icon}
      </div>
      <div className="metric-value">{value}</div>
      <div className="metric-trend">{trend}</div>
    </div>
  );
}

function FiltersPanel({
  classes,
  filters,
  setFilters,
}: {
  classes: string[];
  filters: Filters;
  setFilters: React.Dispatch<React.SetStateAction<Filters>>;
}) {
  return (
    <section className="panel">
      <div className="filters">
        <div className="field">
          <label htmlFor="query">Signal search</label>
          <input
            id="query"
            value={filters.query}
            onChange={(event) => setFilters((prev) => ({ ...prev, query: event.target.value }))}
            placeholder="Drug or adverse event"
          />
        </div>
        <div className="field">
          <label htmlFor="class">Drug class</label>
          <select
            id="class"
            value={filters.drugClass}
            onChange={(event) => setFilters((prev) => ({ ...prev, drugClass: event.target.value }))}
          >
            {classes.map((drugClass) => (
              <option key={drugClass}>{drugClass}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="minReports">Minimum reports</label>
          <input
            id="minReports"
            type="number"
            min={0}
            value={filters.minReports}
            onChange={(event) => setFilters((prev) => ({ ...prev, minReports: Number(event.target.value) }))}
          />
        </div>
        <button
          className={`icon-button ${filters.showFlaggedOnly ? "active" : ""}`}
          onClick={() => setFilters((prev) => ({ ...prev, showFlaggedOnly: !prev.showFlaggedOnly }))}
          title="Toggle flagged signals"
        >
          <Filter size={18} />
        </button>
        <button
          className="icon-button"
          onClick={() =>
            setFilters({
              drugClass: "All",
              query: "",
              priority: "All",
              minReports: 50,
              showFlaggedOnly: false,
            })
          }
          title="Reset filters"
        >
          <SlidersHorizontal size={18} />
        </button>
      </div>
      <div className="segmented" aria-label="Priority filter">
        {(["All", "High", "Moderate", "Low"] as const).map((level) => (
          <button
            key={level}
            className={filters.priority === level ? "active" : ""}
            onClick={() => setFilters((prev) => ({ ...prev, priority: level }))}
          >
            {level}
          </button>
        ))}
      </div>
    </section>
  );
}

function Overview({
  data,
  filteredSignals,
  emerging,
}: {
  data: DashboardData;
  filteredSignals: SignalScore[];
  emerging: EmergingSignal[];
}) {
  const scatter = filteredSignals.map((row) => ({
    ...row,
    logRor: Math.log10(Math.max(row.ror, 0.01)),
  }));
  const topEmerging = emerging.slice(0, 6);

  return (
    <div className="workspace">
      <div className="panel">
        <PanelHeader
          title="Signal landscape"
          caption="x = log10 ROR, y = report count, bubble size = seriousness rate."
        />
        <ClientChart>
          <ResponsiveContainer>
            <ScatterChart margin={{ top: 18, right: 18, bottom: 18, left: 6 }}>
              <CartesianGrid stroke={chartGrid} />
              <XAxis dataKey="logRor" name="log10 ROR" stroke={chartAxis} />
              <YAxis dataKey="a_drug_event" name="Reports" stroke={chartAxis} />
              <ZAxis dataKey="seriousness_rate" range={[90, 520]} />
              <Tooltip content={<SignalTooltip />} />
              <Scatter data={scatter}>
                {scatter.map((entry) => (
                  <Cell key={`${entry.drug_name_normalized}-${entry.adverse_event}`} fill={entry.disproportionality_flag ? chartAccent : chartBlue} />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </ClientChart>
      </div>
      <div className="side-stack">
        <div className="panel">
          <PanelHeader title="Top emerging" caption="Composite queue with transparent drivers." />
          <div className="profile-list">
            {topEmerging.map((row) => (
              <SignalMini key={`${row.drug_name_normalized}-${row.adverse_event}`} row={row} />
            ))}
          </div>
        </div>
        <div className="panel">
          <PanelHeader title="Literature support" caption="Highest relevance PubMed retrievals." />
          <div className="profile-list">
            {data.pubmed_evidence.slice(0, 3).map((article) => (
              <div className="profile-row" key={article.pmid}>
                <div>
                  <strong>{article.title}</strong>
                  <p className="signal-meta">{article.journal} · PMID {article.pmid}</p>
                </div>
                <span className="badge neutral">{ratio(article.relevance_score)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Explorer({ rows }: { rows: SignalScore[] }) {
  return (
    <div className="panel">
      <PanelHeader title="Signal explorer" caption={`${fullNumber(rows.length)} drug-event pairs match the current filters.`} />
      <ClientChart>
        <ResponsiveContainer>
          <BarChart data={rows.slice(0, 16)} layout="vertical" margin={{ top: 8, right: 24, bottom: 8, left: 110 }}>
            <CartesianGrid stroke={chartGrid} horizontal={false} />
            <XAxis type="number" stroke={chartAxis} />
            <YAxis
              type="category"
              dataKey="adverse_event"
              width={120}
              stroke={chartAxis}
              tickFormatter={(value) => titleCase(String(value)).slice(0, 22)}
            />
            <Tooltip content={<SignalTooltip />} />
            <Bar dataKey="ror" radius={[0, 6, 6, 0]}>
              {rows.slice(0, 16).map((entry) => (
                <Cell key={`${entry.drug_name_normalized}-${entry.adverse_event}`} fill={entry.disproportionality_flag ? chartAccent : chartBlue} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </ClientChart>
      <SignalTable rows={rows} />
    </div>
  );
}

function Profiles(props: {
  drugs: string[];
  events: string[];
  selectedDrug: string;
  setSelectedDrug: (drug: string) => void;
  selectedEvent: string;
  setSelectedEvent: (event: string) => void;
  selectedDrugRows: SignalScore[];
  selectedEventRows: SignalScore[];
  data: DashboardData;
}) {
  const nhanes = props.data.nhanes_population_context.find((row) => row.medication_name_normalized === props.selectedDrug);
  const drugRows = [...props.selectedDrugRows].sort((a, b) => b.a_drug_event - a.a_drug_event);
  const eventRows = [...props.selectedEventRows].sort((a, b) => b.ror - a.ror);

  return (
    <div className="workspace">
      <div className="panel">
        <PanelHeader title="Drug profile" caption="Top adverse event reporting patterns and population context." />
        <div className="field" style={{ marginBottom: "1rem" }}>
          <label htmlFor="drug-profile">Drug</label>
          <select id="drug-profile" value={props.selectedDrug} onChange={(event) => props.setSelectedDrug(event.target.value)}>
            {props.drugs.map((drug) => (
              <option key={drug}>{drug}</option>
            ))}
          </select>
        </div>
        <ClientChart>
          <ResponsiveContainer>
            <BarChart data={drugRows.slice(0, 12)} layout="vertical" margin={{ top: 8, right: 24, bottom: 8, left: 128 }}>
              <CartesianGrid stroke={chartGrid} horizontal={false} />
              <XAxis type="number" stroke={chartAxis} />
              <YAxis type="category" dataKey="adverse_event" width={140} stroke={chartAxis} tickFormatter={(v) => titleCase(String(v)).slice(0, 24)} />
              <Tooltip content={<SignalTooltip />} />
              <Bar dataKey="a_drug_event" fill={chartGold} radius={[0, 6, 6, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ClientChart>
        {nhanes ? (
          <div className="method-grid">
            <ProfileStat label="Weighted prevalence" value={percent(nhanes.weighted_prevalence, 2)} />
            <ProfileStat label="Median age" value={String(nhanes.median_age)} />
            <ProfileStat label="BMI >= 30" value={`${nhanes.bmi_ge_30_percent.toFixed(0)}%`} />
          </div>
        ) : null}
      </div>

      <div className="panel">
        <PanelHeader title="Event profile" caption="Drugs ranked by ROR for the selected event." />
        <div className="field" style={{ marginBottom: "1rem" }}>
          <label htmlFor="event-profile">Adverse event</label>
          <select id="event-profile" value={props.selectedEvent} onChange={(event) => props.setSelectedEvent(event.target.value)}>
            {props.events.map((event) => (
              <option key={event}>{event}</option>
            ))}
          </select>
        </div>
        <div className="profile-list">
          {eventRows.slice(0, 8).map((row) => (
            <div className="profile-row" key={`${row.drug_name_normalized}-${row.adverse_event}`}>
              <div>
                <strong>{row.drug_name_normalized}</strong>
                <p className="signal-meta">{row.drug_class}</p>
              </div>
              <span className={row.disproportionality_flag ? "badge" : "badge neutral"}>ROR {ratio(row.ror)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Emerging({
  rows,
  selectedSignal,
  setSelectedSignal,
}: {
  rows: EmergingSignal[];
  selectedSignal: EmergingSignal | undefined;
  setSelectedSignal: (row: EmergingSignal) => void;
}) {
  return (
    <div className="workspace">
      <div className="panel">
        <PanelHeader title="Emerging signal feed" caption={`${fullNumber(rows.length)} signals in the current priority filter.`} />
        <ClientChart>
          <ResponsiveContainer>
            <ScatterChart margin={{ top: 18, right: 18, bottom: 18, left: 6 }}>
              <CartesianGrid stroke={chartGrid} />
              <XAxis dataKey="anomaly_score" name="Anomaly z" stroke={chartAxis} />
              <YAxis dataKey="seriousness_rate" name="Seriousness" stroke={chartAxis} tickFormatter={(v) => `${Number(v) * 100}%`} />
              <ZAxis dataKey="current_count" range={[100, 540]} />
              <Tooltip content={<EmergingTooltip />} />
              <Scatter data={rows}>
                {rows.map((entry) => (
                  <Cell key={`${entry.drug_name_normalized}-${entry.adverse_event}`} fill={priorityColors[entry.priority_level]} />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </ClientChart>
      </div>
      <div className="side-stack">
        {rows.map((row) => {
          const selected =
            selectedSignal?.drug_name_normalized === row.drug_name_normalized &&
            selectedSignal?.adverse_event === row.adverse_event;
          return (
            <button className={`card signal-card ${selected ? "selected" : ""}`} key={`${row.drug_name_normalized}-${row.adverse_event}`} onClick={() => setSelectedSignal(row)}>
              <SignalCardContent row={row} />
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Evidence({ data, selectedSignal }: { data: DashboardData; selectedSignal: EmergingSignal | undefined }) {
  const articles = data.pubmed_evidence.filter((article) => {
    if (!selectedSignal) return true;
    return article.drug_name_normalized === selectedSignal.drug_name_normalized && article.adverse_event === selectedSignal.adverse_event;
  });
  const visibleArticles = articles.length ? articles : data.pubmed_evidence;
  const byYear = visibleArticles.reduce<Record<number, number>>((acc, article) => {
    acc[article.publication_year] = (acc[article.publication_year] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="workspace">
      <div className="panel">
        <PanelHeader title="Literature evidence" caption="PubMed retrieval support is transparent and not treated as causal proof." />
        <ClientChart>
          <ResponsiveContainer>
            <ReLineChart data={Object.entries(byYear).map(([year, articles]) => ({ year, articles }))}>
              <CartesianGrid stroke={chartGrid} />
              <XAxis dataKey="year" stroke={chartAxis} />
              <YAxis stroke={chartAxis} />
              <Tooltip />
              <Line dataKey="articles" stroke={chartAccent} strokeWidth={3} dot={{ r: 5 }} />
            </ReLineChart>
          </ResponsiveContainer>
        </ClientChart>
      </div>
      <div className="panel">
        <PanelHeader title="Top articles" caption="Ranked by relevance score." />
        <div className="profile-list">
          {visibleArticles.map((article) => (
            <a className="profile-row" key={article.pmid} href={article.url} target="_blank" rel="noreferrer">
              <div>
                <strong>{article.title}</strong>
                <p className="signal-meta">{article.journal} · {article.publication_year} · PMID {article.pmid}</p>
              </div>
              <span className="badge neutral">{ratio(article.relevance_score)}</span>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

function Nhanes({ data }: { data: DashboardData }) {
  return (
    <div className="panel">
      <PanelHeader title="NHANES population context" caption="Aggregate medication-user context only. It is not linked to FAERS person-level reports." />
      <ClientChart>
        <ResponsiveContainer>
          <BarChart data={[...data.nhanes_population_context].sort((a, b) => b.weighted_prevalence - a.weighted_prevalence)} layout="vertical" margin={{ top: 8, right: 24, bottom: 8, left: 128 }}>
            <CartesianGrid stroke={chartGrid} horizontal={false} />
            <XAxis type="number" stroke={chartAxis} tickFormatter={(v) => `${Number(v) * 100}%`} />
            <YAxis type="category" dataKey="medication_name_normalized" width={140} stroke={chartAxis} />
            <Tooltip formatter={(value) => percent(Number(value), 2)} />
            <Bar dataKey="weighted_prevalence" fill={chartBlue} radius={[0, 6, 6, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </ClientChart>
      <div className="profile-list">
        {data.nhanes_population_context.map((row) => (
          <div className="profile-row" key={row.medication_name_normalized}>
            <div>
              <strong>{row.medication_name_normalized}</strong>
              <p className="signal-meta">
                n={row.unweighted_sample_count} · median age {row.median_age} · HbA1c {row.hba1c_median}
              </p>
            </div>
            <div className="progress" style={{ "--value": `${Math.min(row.weighted_prevalence * 900, 100)}%` } as React.CSSProperties}>
              <span />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Health({ data }: { data: DashboardData }) {
  const latest = data.pipeline_health[0];
  return (
    <div className="workspace">
      <div className="panel">
        <PanelHeader title="Pipeline health" caption="Operational trust, row counts, quality checks, and estimated run cost." />
        <section className="kpi-grid">
          <MetricCard icon={<CheckCircle2 size={18} />} label="Status" value={latest?.status.toUpperCase() ?? "UNKNOWN"} trend={latest?.source_period ?? ""} />
          <MetricCard icon={<Database size={18} />} label="Gold rows" value={compactNumber(latest?.rows_gold ?? 0)} trend="Modeled tables" />
          <MetricCard icon={<AlertTriangle size={18} />} label="Warnings" value={String(latest?.warning_checks ?? 0)} trend={`${latest?.failed_checks ?? 0} failures`} />
          <MetricCard icon={<Activity size={18} />} label="Run cost" value={`$${(latest?.estimated_cost_usd ?? 0).toFixed(2)}`} trend={latest?.source ?? "source"} />
        </section>
        <p className="panel-caption">{latest?.notes}</p>
      </div>
      <div className="panel">
        <PanelHeader title="Data-quality checks" caption="Latest validation signals." />
        <div className="profile-list">
          {data.data_quality_checks.map((check) => (
            <div className="profile-row" key={`${check.table}-${check.check}`}>
              <div>
                <strong>{check.check}</strong>
                <p className="signal-meta">{check.table} · {check.category} · {check.detail}</p>
              </div>
              <span className={check.status === "pass" ? "badge" : check.status === "warn" ? "badge warn" : "badge danger"}>
                {check.status}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Methodology() {
  return (
    <div className="method-grid">
      <div className="panel method-card">
        <h3>Disproportionality</h3>
        <p>ROR and PRR are computed from 2x2 reporting tables with confidence intervals and continuity correction for zero cells.</p>
      </div>
      <div className="panel method-card">
        <h3>Shrinkage</h3>
        <p>A simplified empirical-Bayes style score down-weights unstable low-count pairs for prioritization.</p>
      </div>
      <div className="panel method-card">
        <h3>Composite priority</h3>
        <p>Priority blends disproportionality, trend anomaly, seriousness, literature retrieval support, and NHANES context.</p>
      </div>
      <div className="panel method-card">
        <h3>Trend anomaly</h3>
        <p>Current-quarter counts are compared with trailing baselines using percent change and anomaly z-scores.</p>
      </div>
      <div className="panel method-card">
        <h3>Responsible use</h3>
        <p>Outputs are hypothesis-generating. FAERS has reporting bias and lacks an incidence denominator.</p>
      </div>
      <div className="panel method-card">
        <h3>Sources</h3>
        <p>openFDA FAERS, FDA quarterly extracts, NHANES, and NCBI E-utilities.</p>
      </div>
    </div>
  );
}

function PanelHeader({ title, caption }: { title: string; caption: string }) {
  return (
    <div className="panel-header">
      <div>
        <h2 className="panel-title">{title}</h2>
        <p className="panel-caption">{caption}</p>
      </div>
    </div>
  );
}

function ClientChart({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  return <div className="chart-wrap">{mounted ? children : null}</div>;
}

function SignalTable({ rows }: { rows: SignalScore[] }) {
  return (
    <div className="signal-table">
      <table>
        <thead>
          <tr>
            <th>Drug</th>
            <th>Event</th>
            <th>Class</th>
            <th>Reports</th>
            <th>ROR</th>
            <th>CI low</th>
            <th>PRR</th>
            <th>Serious</th>
            <th>Flag</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 80).map((row) => (
            <tr key={`${row.drug_name_normalized}-${row.adverse_event}`}>
              <td>{row.drug_name_normalized}</td>
              <td>{titleCase(row.adverse_event)}</td>
              <td>{row.drug_class}</td>
              <td>{fullNumber(row.a_drug_event)}</td>
              <td>{ratio(row.ror)}</td>
              <td>{ratio(row.ror_ci_lower)}</td>
              <td>{ratio(row.prr)}</td>
              <td>{percent(row.seriousness_rate)}</td>
              <td><span className={row.disproportionality_flag ? "badge" : "badge neutral"}>{row.disproportionality_flag ? "yes" : "no"}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SignalMini({ row }: { row: EmergingSignal }) {
  return (
    <div className="profile-row">
      <div>
        <strong>{row.drug_name_normalized} / {titleCase(row.adverse_event)}</strong>
        <p className="signal-meta">{row.drug_class}</p>
      </div>
      <span className={row.priority_level === "High" ? "badge danger" : row.priority_level === "Moderate" ? "badge warn" : "badge neutral"}>
        {row.priority_level}
      </span>
    </div>
  );
}

function SignalCardContent({ row }: { row: EmergingSignal }) {
  return (
    <>
      <div className="signal-card-top">
        <div>
          <p className="signal-title">{row.drug_name_normalized} / {titleCase(row.adverse_event)}</p>
          <p className="signal-meta">{row.drug_class}</p>
        </div>
        <span className={row.priority_level === "High" ? "badge danger" : row.priority_level === "Moderate" ? "badge warn" : "badge neutral"}>
          {row.priority_level}
        </span>
      </div>
      <div className="mini-grid">
        <div className="mini-stat">
          <span>Priority</span>
          <strong>{ratio(row.priority_score)}</strong>
        </div>
        <div className="mini-stat">
          <span>Latest</span>
          <strong>{fullNumber(row.current_count)}</strong>
        </div>
        <div className="mini-stat">
          <span>Change</span>
          <strong>{percent(row.percent_change)}</strong>
        </div>
      </div>
    </>
  );
}

function ProfileStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-card">
      <div className="metric-label"><span>{label}</span></div>
      <div className="metric-value">{value}</div>
    </div>
  );
}

function SignalTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: SignalScore }> }) {
  if (!active || !payload?.[0]) return null;
  const row = payload[0].payload;
  return (
    <div className="metric-card">
      <strong>{row.drug_name_normalized} / {titleCase(row.adverse_event)}</strong>
      <p className="signal-meta">Reports {fullNumber(row.a_drug_event)} · ROR {ratio(row.ror)} · Serious {percent(row.seriousness_rate)}</p>
    </div>
  );
}

function EmergingTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: EmergingSignal }> }) {
  if (!active || !payload?.[0]) return null;
  const row = payload[0].payload;
  return (
    <div className="metric-card">
      <strong>{row.drug_name_normalized} / {titleCase(row.adverse_event)}</strong>
      <p className="signal-meta">Priority {ratio(row.priority_score)} · z {ratio(row.anomaly_score)} · Latest {row.current_count}</p>
    </div>
  );
}
