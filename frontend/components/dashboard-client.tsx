"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  Activity,
  BookOpen,
  Database,
  Download,
  Filter,
  Info,
  LineChart,
  Moon,
  Pill,
  RotateCw,
  Search,
  Settings2,
  ShieldPlus,
  Sparkles,
  Sun,
  Users,
  Workflow,
} from "lucide-react";
import {
  Emerging,
  Evidence,
  Explorer,
  Health,
  Interactions,
  Method,
  Nhanes,
  Overview,
  Profiles,
} from "@/components/views";
import { absoluteTime, downloadCsv, relativeTime } from "@/lib/format";
import { useTheme } from "@/lib/theme";
import type { DashboardData, DrugLabelFlag, EmergingSignal, Filters, PriorityLevel } from "@/lib/types";

type Section =
  | "Overview"
  | "Explorer"
  | "Profiles"
  | "Interactions"
  | "Emerging"
  | "Evidence"
  | "NHANES"
  | "Health"
  | "Method";

const navGroups: { label: string; items: { id: Section; icon: typeof Activity }[] }[] = [
  {
    label: "Signals",
    items: [
      { id: "Overview", icon: Activity },
      { id: "Explorer", icon: Search },
      { id: "Profiles", icon: Pill },
      { id: "Interactions", icon: Workflow },
    ],
  },
  {
    label: "Monitoring",
    items: [
      { id: "Emerging", icon: LineChart },
      { id: "Evidence", icon: BookOpen },
    ],
  },
  {
    label: "Context & Ops",
    items: [
      { id: "NHANES", icon: Users },
      { id: "Health", icon: Database },
      { id: "Method", icon: Settings2 },
    ],
  },
];

const sectionMeta: Record<Section, { eyebrow: string; title: string; sub: string }> = {
  Overview: { eyebrow: "Surveillance", title: "Signal overview", sub: "Portfolio-level view of disproportionality, emerging risk, and supporting literature." },
  Explorer: { eyebrow: "Analysis", title: "Signal explorer", sub: "Inspect and rank every drug–event pair with full disproportionality statistics." },
  Profiles: { eyebrow: "Analysis", title: "Profiles", sub: "Per-drug and per-event reporting patterns, population context, and subgroup breakdowns." },
  Interactions: { eyebrow: "Analysis", title: "Drug interactions", sub: "Co-reported drug pairs whose combined reporting exceeds either agent alone — candidate interaction signals." },
  Emerging: { eyebrow: "Monitoring", title: "Emerging signals", sub: "Composite-prioritized signals with trend, seriousness, and literature drivers." },
  Evidence: { eyebrow: "Monitoring", title: "Literature evidence", sub: "PubMed retrievals supporting selected signals — retrieval, not causation." },
  NHANES: { eyebrow: "Context", title: "Population context", sub: "NHANES-weighted characteristics of medication users for interpretive context." },
  Health: { eyebrow: "Operations", title: "Pipeline health", sub: "Provenance, throughput, and data-quality validation for the lakehouse." },
  Method: { eyebrow: "Reference", title: "Methodology", sub: "How signals are computed, scored, and responsibly interpreted." },
};

export function DashboardClient({ data }: { data: DashboardData }) {
  const router = useRouter();
  const { theme, toggle } = useTheme();
  const [section, setSection] = useState<Section>("Overview");
  const [filters, setFilters] = useState<Filters>({
    drugClass: "All",
    query: "",
    priority: "All",
    minReports: 0,
    showFlaggedOnly: false,
    showNovelOnly: false,
  });
  const [selectedDrug, setSelectedDrug] = useState(data.signal_scores[0]?.drug_name_normalized ?? "");
  const [selectedEvent, setSelectedEvent] = useState(data.signal_scores[0]?.adverse_event ?? "");
  const [selectedSignal, setSelectedSignal] = useState<EmergingSignal | undefined>(
    [...data.emerging_signals].sort((a, b) => b.priority_score - a.priority_score)[0],
  );
  const [showAllEvidence, setShowAllEvidence] = useState(false);

  const classes = useMemo(
    () => ["All", ...Array.from(new Set(data.signal_scores.map((d) => d.drug_class))).sort()],
    [data.signal_scores],
  );
  const drugs = useMemo(
    () => Array.from(new Set(data.signal_scores.map((r) => r.drug_name_normalized))).sort(),
    [data.signal_scores],
  );
  const events = useMemo(
    () => Array.from(new Set(data.signal_scores.map((r) => r.adverse_event))).sort(),
    [data.signal_scores],
  );

  const labelMap = useMemo(() => {
    const m = new Map<string, DrugLabelFlag>();
    (data.drug_label_flags ?? []).forEach((f) =>
      m.set(`${f.drug_name_normalized}::${f.adverse_event}`, f),
    );
    return m;
  }, [data.drug_label_flags]);
  const hasLabels = labelMap.size > 0;

  const filteredSignals = useMemo(() => {
    const q = filters.query.trim().toUpperCase();
    return data.signal_scores
      .filter((r) => filters.drugClass === "All" || r.drug_class === filters.drugClass)
      .filter((r) => r.a_drug_event >= filters.minReports)
      .filter((r) => !filters.showFlaggedOnly || r.disproportionality_flag)
      .filter((r) => !filters.showNovelOnly || labelMap.get(`${r.drug_name_normalized}::${r.adverse_event}`)?.novel_flag)
      .filter((r) => !q || `${r.drug_name_normalized} ${r.adverse_event}`.includes(q))
      .sort((a, b) => b.ror - a.ror);
  }, [data.signal_scores, filters, labelMap]);

  const emerging = useMemo(
    () =>
      data.emerging_signals
        .filter((r) => filters.priority === "All" || r.priority_level === filters.priority)
        .sort((a, b) => b.priority_score - a.priority_score),
    [data.emerging_signals, filters.priority],
  );

  const evidenceArticles = useMemo(() => {
    if (showAllEvidence || !selectedSignal) return data.pubmed_evidence;
    const m = data.pubmed_evidence.filter(
      (a) =>
        a.drug_name_normalized === selectedSignal.drug_name_normalized &&
        a.adverse_event === selectedSignal.adverse_event,
    );
    return m.length ? m : data.pubmed_evidence;
  }, [data.pubmed_evidence, selectedSignal, showAllEvidence]);

  const isLive = data.data_source !== "demo";
  const showSignalFilters = section === "Explorer" || section === "Overview";
  const showPriorityFilter = section === "Emerging";
  const meta = sectionMeta[section];

  const handleExport = () => {
    const stamp = new Date().toISOString().slice(0, 10);
    const map: Partial<Record<Section, [string, Record<string, unknown>[]]>> = {
      Overview: [`signals-${stamp}.csv`, filteredSignals],
      Explorer: [`signals-${stamp}.csv`, filteredSignals],
      Interactions: [`interactions-${stamp}.csv`, data.interaction_signals ?? []],
      Emerging: [`emerging-${stamp}.csv`, emerging],
      Evidence: [`evidence-${stamp}.csv`, evidenceArticles],
      NHANES: [`nhanes-${stamp}.csv`, data.nhanes_population_context],
      Health: [`quality-checks-${stamp}.csv`, data.data_quality_checks],
    };
    const target = map[section];
    if (target) downloadCsv(target[0], target[1]);
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <ShieldPlus size={18} aria-hidden />
          </div>
          <div>
            <p className="brand-title">PharmaSignal</p>
            <p className="brand-subtitle">Metabolic · GLP-1</p>
          </div>
        </div>

        <div className="nav-scroll">
          {navGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              <nav className="nav-list" aria-label={group.label}>
                {group.items.map((item) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.id}
                      className={`nav-item ${section === item.id ? "active" : ""}`}
                      onClick={() => setSection(item.id)}
                    >
                      <Icon size={16} aria-hidden />
                      <span>{item.id}</span>
                    </button>
                  );
                })}
              </nav>
            </div>
          ))}
        </div>

        <p className="sidebar-note">
          <Info size={13} aria-hidden />
          Hypothesis-generating signals. FAERS reports do not establish causality or incidence.
        </p>

        <div className="user-card">
          <div className="user-avatar">GM</div>
          <div className="user-meta">
            <p className="user-name">Grant Merrigan</p>
            <p className="user-role">Safety researcher</p>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="commandbar">
          <div className="cb-search">
            <Search size={16} />
            <input
              value={filters.query}
              onChange={(e) => setFilters((p) => ({ ...p, query: e.target.value }))}
              placeholder="Search drugs or adverse events…"
              aria-label="Search signals"
            />
          </div>
          <div className="cb-spacer" />
          <div className="cb-actions">
            <span
              className={`source-pill ${isLive ? "live" : "demo"}`}
              title={`Generated ${absoluteTime(data.generated_at)}`}
            >
              <span className="dot" />
              {isLive ? "Live" : "Demo"} data
              <span className="sep">·</span>
              {relativeTime(data.generated_at)}
            </span>
            <button className="icon-button" onClick={toggle} title="Toggle theme" aria-label="Toggle theme">
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <button className="icon-button" onClick={() => router.refresh()} title="Refresh data" aria-label="Refresh">
              <RotateCw size={16} />
            </button>
            <button className="button secondary" onClick={handleExport}>
              <Download size={15} />
              Export
            </button>
          </div>
        </header>

        <div className="view">
          <div className="view-head">
            <div>
              <p className="eyebrow">{meta.eyebrow}</p>
              <h1 className="view-title">{meta.title}</h1>
              <p className="view-sub">{meta.sub}</p>
            </div>
          </div>

          {(showSignalFilters || showPriorityFilter) && (
            <div className="toolbar">
              {showSignalFilters && (
                <>
                  <div className="field grow">
                    <label htmlFor="f-class">Drug class</label>
                    <select
                      id="f-class"
                      value={filters.drugClass}
                      onChange={(e) => setFilters((p) => ({ ...p, drugClass: e.target.value }))}
                    >
                      {classes.map((c) => (
                        <option key={c}>{c}</option>
                      ))}
                    </select>
                  </div>
                  <div className="field compact">
                    <label htmlFor="f-min">Min reports</label>
                    <input
                      id="f-min"
                      type="number"
                      min={0}
                      value={filters.minReports}
                      onChange={(e) => setFilters((p) => ({ ...p, minReports: Number(e.target.value) }))}
                    />
                  </div>
                  <button
                    className={`toggle-pill ${filters.showFlaggedOnly ? "active" : ""}`}
                    onClick={() => setFilters((p) => ({ ...p, showFlaggedOnly: !p.showFlaggedOnly }))}
                  >
                    <Filter size={15} />
                    Flagged only
                  </button>
                  {hasLabels && (
                    <button
                      className={`toggle-pill ${filters.showNovelOnly ? "active" : ""}`}
                      onClick={() => setFilters((p) => ({ ...p, showNovelOnly: !p.showNovelOnly }))}
                    >
                      <Sparkles size={15} />
                      Novel only
                    </button>
                  )}
                </>
              )}
              {showPriorityFilter && (
                <div className="field">
                  <label>Priority</label>
                  <div className="segmented">
                    {(["All", "High", "Moderate", "Low"] as const).map((lvl) => (
                      <button
                        key={lvl}
                        className={filters.priority === lvl ? "active" : ""}
                        onClick={() => setFilters((p) => ({ ...p, priority: lvl as PriorityLevel | "All" }))}
                      >
                        {lvl}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          <AnimatePresence mode="wait">
            <motion.div
              key={section}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.16 }}
              style={{ display: "grid", gap: "1.1rem" }}
            >
              {section === "Overview" && (
                <Overview
                  data={data}
                  filteredSignals={filteredSignals}
                  emerging={emerging}
                  labelMap={labelMap}
                  onSelectSignal={(s) => { setSelectedSignal(s); setShowAllEvidence(false); setSection("Evidence"); }}
                  onGoNovel={() => { setFilters((p) => ({ ...p, showNovelOnly: true, showFlaggedOnly: false })); setSection("Explorer"); }}
                />
              )}
              {section === "Explorer" && <Explorer rows={filteredSignals} labelMap={labelMap} />}
              {section === "Profiles" && (
                <Profiles
                  data={data}
                  drugs={drugs}
                  events={events}
                  selectedDrug={selectedDrug}
                  selectedEvent={selectedEvent}
                  setSelectedDrug={setSelectedDrug}
                  setSelectedEvent={setSelectedEvent}
                />
              )}
              {section === "Interactions" && <Interactions rows={data.interaction_signals ?? []} />}
              {section === "Emerging" && <Emerging rows={emerging} selected={selectedSignal} onSelect={(s) => { setSelectedSignal(s); setShowAllEvidence(false); }} />}
              {section === "Evidence" && (
                <Evidence
                  articles={evidenceArticles}
                  selected={selectedSignal}
                  showAll={showAllEvidence}
                  totalCount={data.pubmed_evidence.length}
                  onToggleAll={() => setShowAllEvidence((v) => !v)}
                />
              )}
              {section === "NHANES" && <Nhanes rows={data.nhanes_population_context} />}
              {section === "Health" && <Health data={data} />}
              {section === "Method" && <Method />}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>
    </div>
  );
}
