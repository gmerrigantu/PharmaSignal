"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowUpDown,
  BarChart3,
  BookOpen,
  Database,
  ExternalLink,
  FlaskConical,
  Inbox,
  Info,
  Layers,
  ListFilter,
  Microscope,
  ShieldAlert,
  Sparkles,
  TrendingUp,
  Users,
  Workflow,
} from "lucide-react";
import {
  Dumbbell,
  EmergingScatter,
  EvidenceLine,
  ForestPlot,
  InteractionScatter,
  NhanesBars,
  ProfileBars,
  RorBars,
  SignalScatter,
} from "@/components/charts";
import { Badge, Empty, Panel, Stat, type BadgeTone } from "@/components/ui";
import { fetchSignalsPage } from "@/lib/api";
import {
  compactNumber,
  fullNumber,
  percent,
  ratio,
  signedPercent,
  titleCase,
} from "@/lib/format";
import type {
  DashboardData,
  DrugLabelFlag,
  EmergingSignal,
  Filters,
  InteractionSignal,
  LiteratureArticle,
  NhanesContext,
  PriorityLevel,
  QualityCheck,
  SignalScore,
  SignalsPage,
  SubgroupSignal,
} from "@/lib/types";

export type LabelMap = Map<string, DrugLabelFlag>;

const priorityTone: Record<PriorityLevel, BadgeTone> = {
  High: "high",
  Moderate: "moderate",
  Low: "low",
};
const statusTone: Record<QualityCheck["status"], BadgeTone> = {
  pass: "pass",
  warn: "warn",
  fail: "fail",
};

const pair = (r: { drug_name_normalized: string; adverse_event: string }) =>
  `${r.drug_name_normalized}::${r.adverse_event}`;

function LabelBadge({ flag }: { flag?: DrugLabelFlag }) {
  if (!flag || flag.label_status === "unknown") {
    return <span style={{ color: "var(--text-subtle)" }}>—</span>;
  }
  if (flag.novel_flag) return <Badge tone="novel" dot>Novel</Badge>;
  return <Badge tone="neutral">On-label</Badge>;
}

/* ====================================================================== OVERVIEW */
export function Overview({
  data,
  filteredSignals,
  emerging,
  labelMap,
  onSelectSignal,
  onGoNovel,
}: {
  data: DashboardData;
  filteredSignals: SignalScore[];
  emerging: EmergingSignal[];
  labelMap: LabelMap;
  onSelectSignal: (s: EmergingSignal) => void;
  onGoNovel: () => void;
}) {
  const flagged = data.flagged_total;
  const high = data.emerging_signals.filter((r) => r.priority_level === "High").length;
  const latest = data.pipeline_health[0];
  const topMover = [...data.emerging_signals].sort(
    (a, b) => (b.percent_change ?? 0) - (a.percent_change ?? 0),
  )[0];

  const labelFlags = data.drug_label_flags ?? [];
  const counts = { labeled: 0, novel: 0, unknown: 0 };
  labelFlags.forEach((f) => (counts[f.label_status] += 1));
  const labelTotal = labelFlags.length || 1;
  // Novel-flagged signals shown in the label section, drawn from the bounded sample.
  const novelFlagged = data.signal_sample
    .filter((s) => labelMap.get(pair(s))?.novel_flag)
    .sort((a, b) => b.ror - a.ror);

  return (
    <>
      <section className="kpi-grid">
        <Stat
          icon={Layers}
          label="Drug–event pairs"
          value={fullNumber(data.signal_total)}
          foot={`${fullNumber(flagged)} disproportionate`}
        />
        <Stat
          icon={ShieldAlert}
          label="High-priority signals"
          value={fullNumber(high)}
          foot="composite ranking"
        />
        <Stat
          icon={TrendingUp}
          label="Largest QoQ rise"
          value={topMover ? titleCase(topMover.drug_name_normalized) : "—"}
          delta={topMover?.percent_change ?? null}
          foot={topMover ? titleCase(topMover.adverse_event) : undefined}
        />
        <Stat
          icon={Database}
          label="FAERS reports indexed"
          value={compactNumber(latest?.rows_raw ?? 0)}
          foot={latest?.source_period ?? "current"}
        />
        <Stat
          icon={BookOpen}
          label="Literature retrievals"
          value={fullNumber(data.pubmed_evidence.length)}
          foot="PubMed evidence"
        />
        {labelFlags.length > 0 && (
          <Stat
            icon={Sparkles}
            label="Novel signals"
            value={fullNumber(counts.novel)}
            foot="not on FDA label"
          />
        )}
      </section>

      <div className="grid-2">
        <Panel
          title="Signal landscape"
          caption="Disproportionality vs. report volume. Bubble size = seriousness rate; teal = flagged."
        >
          {filteredSignals.length ? (
            <SignalScatter rows={filteredSignals} />
          ) : (
            <Empty icon={Inbox}>No signals match the current filters.</Empty>
          )}
        </Panel>
        <div className="side-stack">
          <Panel title="Priority queue" caption="Top composite-ranked emerging signals.">
            <div className="rows">
              {emerging.slice(0, 6).map((row) => (
                <button className="row link" key={pair(row)} onClick={() => onSelectSignal(row)}>
                  <div>
                    <p className="row-title">
                      {titleCase(row.drug_name_normalized)} · {titleCase(row.adverse_event)}
                    </p>
                    <p className="row-meta">
                      {row.drug_class} · {signedPercent(row.percent_change)} QoQ
                    </p>
                  </div>
                  <Badge tone={priorityTone[row.priority_level]} dot>
                    {row.priority_level}
                  </Badge>
                </button>
              ))}
            </div>
          </Panel>
          <Panel title="Latest literature" caption="Highest-relevance PubMed retrievals.">
            <div className="rows">
              {data.pubmed_evidence.slice(0, 3).map((a) => (
                <a className="row link" key={a.pmid} href={a.url} target="_blank" rel="noreferrer">
                  <div>
                    <p className="row-title">{a.title}</p>
                    <p className="row-meta">
                      {a.journal} · {a.publication_year}
                    </p>
                  </div>
                  <Badge tone="accent">{ratio(a.relevance_score)}</Badge>
                </a>
              ))}
            </div>
          </Panel>
        </div>
      </div>

      {labelFlags.length > 0 && (
        <Panel
          title="Label status"
          caption="Whether each disproportionate signal already appears in FDA labeling. Novel = a reported signal not found in the current label — the highest-interest case."
          action={
            novelFlagged.length ? (
              <button className="button ghost" onClick={onGoNovel}>
                Review novel
              </button>
            ) : undefined
          }
        >
          <div className="grid-2">
            <div>
              <div className="statusbar" role="img" aria-label="Label status distribution">
                <i className="labeled" style={{ width: `${(counts.labeled / labelTotal) * 100}%` }} />
                <i className="novel" style={{ width: `${(counts.novel / labelTotal) * 100}%` }} />
                <i className="unknown" style={{ width: `${(counts.unknown / labelTotal) * 100}%` }} />
              </div>
              <div className="status-legend">
                <span><i className="labeled" /> On-label <b>{counts.labeled}</b></span>
                <span><i className="novel" /> Novel <b>{counts.novel}</b></span>
                {counts.unknown > 0 && <span><i className="unknown" /> No label <b>{counts.unknown}</b></span>}
              </div>
            </div>
            <div className="rows">
              {novelFlagged.slice(0, 4).map((s) => (
                <div className="row" key={pair(s)}>
                  <div>
                    <p className="row-title">
                      {titleCase(s.drug_name_normalized)} · {titleCase(s.adverse_event)}
                    </p>
                    <p className="row-meta">{s.drug_class} · {fullNumber(s.a_drug_event)} reports</p>
                  </div>
                  <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
                    <Badge tone="accent">ROR {ratio(s.ror)}</Badge>
                    <Badge tone="novel" dot>Novel</Badge>
                  </div>
                </div>
              ))}
              {!novelFlagged.length && <Empty icon={Inbox}>No novel signals detected.</Empty>}
            </div>
          </div>
        </Panel>
      )}
    </>
  );
}

/* ====================================================================== EXPLORER */
type SortKey = "a_drug_event" | "ror" | "prr" | "chi_square" | "bayesian_shrunken_score" | "seriousness_rate";

const PAGE_SIZE = 50;

export function Explorer({ filters, labelMap }: { filters: Filters; labelMap: LabelMap }) {
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({
    key: "ror",
    dir: "desc",
  });
  const [page, setPage] = useState<SignalsPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const hasLabels = labelMap.size > 0;

  // Filters/sort changing returns us to the first page.
  useEffect(() => {
    setOffset(0);
  }, [filters.drugClass, filters.minReports, filters.showFlaggedOnly, filters.query, sort.key, sort.dir]);

  // Fetch the current page server-side (the full matrix is never loaded in the browser).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchSignalsPage({
      drug_class: filters.drugClass !== "All" ? filters.drugClass : undefined,
      min_reports: filters.minReports || undefined,
      flagged_only: filters.showFlaggedOnly || undefined,
      q: filters.query.trim() || undefined,
      sort: sort.key,
      desc: sort.dir === "desc",
      offset,
      limit: PAGE_SIZE,
    })
      .then((p) => !cancelled && setPage(p))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Failed to load signals"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [filters.drugClass, filters.minReports, filters.showFlaggedOnly, filters.query, sort.key, sort.dir, offset]);

  const toggle = (key: SortKey) =>
    setSort((p) => (p.key === key ? { key, dir: p.dir === "desc" ? "asc" : "desc" } : { key, dir: "desc" }));

  const Th = ({ k, children }: { k: SortKey; children: React.ReactNode }) => (
    <th className="sortable right" onClick={() => toggle(k)}>
      <span className="th-inner">
        {children}
        <ArrowUpDown size={11} opacity={sort.key === k ? 1 : 0.35} />
      </span>
    </th>
  );

  const total = page?.total ?? 0;
  // "Novel only" is a label-mart filter (client-side); apply it to the fetched page.
  const rows = (page?.rows ?? []).filter(
    (r) => !filters.showNovelOnly || labelMap.get(pair(r))?.novel_flag,
  );
  const pageStart = total ? offset + 1 : 0;
  const pageEnd = offset + (page?.rows.length ?? 0);

  return (
    <Panel
      title="Signal explorer"
      caption={`${fullNumber(total)} drug–event pairs match the current filters. Click a column to sort; page through every result below.`}
    >
      {error ? (
        <Empty icon={Inbox}>Couldn’t load signals: {error}</Empty>
      ) : total === 0 && !loading ? (
        <Empty icon={Inbox}>No drug–event pairs match the current filters.</Empty>
      ) : (
        <>
          <RorBars rows={rows} />
          <div className="table-wrap" style={{ marginTop: "1rem", opacity: loading ? 0.55 : 1 }}>
            <table>
              <thead>
                <tr>
                  <th>Drug</th>
                  <th>Adverse event</th>
                  <th>Class</th>
                  <Th k="a_drug_event">Reports</Th>
                  <Th k="ror">ROR (95% CI)</Th>
                  <Th k="prr">PRR</Th>
                  <Th k="chi_square">χ²</Th>
                  <Th k="bayesian_shrunken_score">EB score</Th>
                  <Th k="seriousness_rate">Serious</Th>
                  <th>Signal</th>
                  {hasLabels && <th>Label</th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={pair(r)}>
                    <td className="drug-cell">{titleCase(r.drug_name_normalized)}</td>
                    <td>{titleCase(r.adverse_event)}</td>
                    <td style={{ color: "var(--text-muted)" }}>{r.drug_class}</td>
                    <td className="num">{fullNumber(r.a_drug_event)}</td>
                    <td className="num">
                      <CiCell value={r.ror} lo={r.ror_ci_lower} hi={r.ror_ci_upper} />
                    </td>
                    <td className="num">{ratio(r.prr)}</td>
                    <td className="num">{ratio(r.chi_square)}</td>
                    <td className="num">{ratio(r.bayesian_shrunken_score)}</td>
                    <td className="num">{percent(r.seriousness_rate)}</td>
                    <td>
                      <Badge tone={r.disproportionality_flag ? "accent" : "neutral"} dot>
                        {r.disproportionality_flag ? "Flagged" : "—"}
                      </Badge>
                    </td>
                    {hasLabels && (
                      <td>
                        <LabelBadge flag={labelMap.get(pair(r))} />
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginTop: "0.9rem",
              gap: "0.75rem",
            }}
          >
            <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>
              {loading
                ? "Loading…"
                : `Showing ${fullNumber(pageStart)}–${fullNumber(pageEnd)} of ${fullNumber(total)}`}
            </span>
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button
                className="button secondary"
                disabled={offset <= 0 || loading}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
              >
                Previous
              </button>
              <button
                className="button secondary"
                disabled={loading || pageEnd >= total}
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </Panel>
  );
}

function CiCell({ value, lo, hi }: { value: number; lo: number; hi: number }) {
  // position the CI band on a 0..max(hi,10) track for a quick visual read
  const max = Math.max(hi, 10);
  const left = (lo / max) * 100;
  const width = Math.max(((hi - lo) / max) * 100, 3);
  return (
    <span className="ci-bar">
      <span>{ratio(value)}</span>
      <span className="ci-track" title={`95% CI ${ratio(lo)}–${ratio(hi)}`}>
        <span className="ci-fill" style={{ left: `${left}%`, width: `${width}%` }} />
      </span>
      <span style={{ color: "var(--text-subtle)", fontSize: "0.72rem" }}>
        {ratio(lo)}–{ratio(hi)}
      </span>
    </span>
  );
}

/* ====================================================================== PROFILES */
export function Profiles({
  data,
  drugs,
  events,
  selectedDrug,
  selectedEvent,
  setSelectedDrug,
  setSelectedEvent,
}: {
  data: DashboardData;
  drugs: string[];
  events: string[];
  selectedDrug: string;
  selectedEvent: string;
  setSelectedDrug: (d: string) => void;
  setSelectedEvent: (e: string) => void;
}) {
  const drugRows = data.signal_sample
    .filter((r) => r.drug_name_normalized === selectedDrug)
    .sort((a, b) => b.a_drug_event - a.a_drug_event);
  const eventRows = data.signal_sample
    .filter((r) => r.adverse_event === selectedEvent)
    .sort((a, b) => b.ror - a.ror);
  const nhanes = data.nhanes_population_context.find((r) => r.medication_name_normalized === selectedDrug);
  const allSubgroups = data.subgroup_signals ?? [];
  const subgroups = allSubgroups.filter(
    (s) => s.drug_name_normalized === selectedDrug && s.adverse_event === selectedEvent,
  );

  return (
    <>
    <div className="grid-2 even">
      <Panel
        title="Drug profile"
        caption="Most-reported adverse events and population context for the selected drug."
        action={
          <select
            value={selectedDrug}
            onChange={(e) => setSelectedDrug(e.target.value)}
            style={{ height: "2.1rem", borderRadius: "var(--r-sm)", border: "1px solid var(--border)", background: "var(--surface-2)", padding: "0 0.6rem" }}
          >
            {drugs.map((d) => (
              <option key={d} value={d}>
                {titleCase(d)}
              </option>
            ))}
          </select>
        }
      >
        {drugRows.length ? <ProfileBars rows={drugRows} /> : <Empty icon={Inbox}>No data.</Empty>}
        {nhanes && (
          <div className="kpi-grid" style={{ marginTop: "1rem" }}>
            <Stat icon={Users} label="Weighted prevalence" value={percent(nhanes.weighted_prevalence, 2)} foot={`NHANES ${nhanes.survey_cycle}`} />
            <Stat icon={Microscope} label="Median age" value={`${nhanes.median_age}`} foot={`${nhanes.female_percent}% female`} />
            <Stat icon={FlaskConical} label="Diabetes" value={percent(nhanes.diabetes_percent / 100, 0)} foot={`HbA1c ${nhanes.hba1c_median}`} />
          </div>
        )}
      </Panel>

      <Panel
        title="Adverse-event profile"
        caption="Drugs ranked by reporting odds ratio for the selected event."
        action={
          <select
            value={selectedEvent}
            onChange={(e) => setSelectedEvent(e.target.value)}
            style={{ height: "2.1rem", borderRadius: "var(--r-sm)", border: "1px solid var(--border)", background: "var(--surface-2)", padding: "0 0.6rem" }}
          >
            {events.map((ev) => (
              <option key={ev} value={ev}>
                {titleCase(ev)}
              </option>
            ))}
          </select>
        }
      >
        <div className="rows">
          {eventRows.slice(0, 9).map((r) => (
            <div className="row" key={pair(r)}>
              <div>
                <p className="row-title">{titleCase(r.drug_name_normalized)}</p>
                <p className="row-meta">
                  {r.drug_class} · {fullNumber(r.a_drug_event)} reports
                </p>
              </div>
              <Badge tone={r.disproportionality_flag ? "accent" : "neutral"}>ROR {ratio(r.ror)}</Badge>
            </div>
          ))}
          {!eventRows.length && <Empty icon={Inbox}>No data.</Empty>}
        </div>
      </Panel>
    </div>

    {allSubgroups.length > 0 && (
      <Panel
        title="Subgroup analysis"
        caption={`Disproportionality recomputed within demographic strata for ${titleCase(selectedDrug)} · ${titleCase(selectedEvent)}, against the overall ROR. Wide intervals reflect small strata.`}
      >
        {subgroups.length ? (
          <ForestPlot rows={subgroups} />
        ) : (
          <Empty icon={Inbox}>
            No subgroup breakdown for {titleCase(selectedDrug)} · {titleCase(selectedEvent)}. Try a flagged
            high-volume pair such as Semaglutide · Nausea.
          </Empty>
        )}
      </Panel>
    )}
    </>
  );
}

/* ====================================================================== EMERGING */
export function Emerging({
  rows,
  selected,
  onSelect,
}: {
  rows: EmergingSignal[];
  selected?: EmergingSignal;
  onSelect: (s: EmergingSignal) => void;
}) {
  return (
    <div className="grid-2">
      <Panel
        title="Emerging-signal map"
        caption="Trend anomaly vs. seriousness. Bubble size = current-quarter count; color = priority."
      >
        {rows.length ? <EmergingScatter rows={rows} /> : <Empty icon={Inbox}>No emerging signals.</Empty>}
      </Panel>
      <div className="side-stack">
        {rows.map((row) => {
          const isSel =
            selected?.drug_name_normalized === row.drug_name_normalized &&
            selected?.adverse_event === row.adverse_event;
          return (
            <button className={`signal-card ${isSel ? "selected" : ""}`} key={pair(row)} onClick={() => onSelect(row)}>
              <div className="signal-card-top">
                <div>
                  <p className="signal-title">
                    {titleCase(row.drug_name_normalized)} · {titleCase(row.adverse_event)}
                  </p>
                  <p className="row-meta">
                    {row.drug_class} · {row.current_quarter}
                  </p>
                </div>
                <Badge tone={priorityTone[row.priority_level]} dot>
                  {row.priority_level}
                </Badge>
              </div>
              <div className="mini-grid">
                <div className="mini-stat">
                  <span>Priority</span>
                  <strong>{ratio(row.priority_score)}</strong>
                </div>
                <div className="mini-stat">
                  <span>Anomaly</span>
                  <strong>{ratio(row.anomaly_score)}σ</strong>
                </div>
                <div className="mini-stat">
                  <span>Literature</span>
                  <strong>{row.literature_support_count}</strong>
                </div>
              </div>
              <div className="spark-trend">
                <div className="bars">
                  <i className="b" style={{ height: barH(row.trailing_baseline_count, row) }} />
                  <i className="c" style={{ height: barH(row.current_count, row) }} />
                </div>
                <span>
                  {fullNumber(row.trailing_baseline_count)} → <b style={{ color: "var(--text)" }}>{fullNumber(row.current_count)}</b>{" "}
                  ({signedPercent(row.percent_change)})
                </span>
              </div>
            </button>
          );
        })}
        {!rows.length && (
          <Panel>
            <Empty icon={Inbox}>No emerging signals in this filter.</Empty>
          </Panel>
        )}
      </div>
    </div>
  );
}

function barH(v: number, row: EmergingSignal): string {
  const max = Math.max(row.current_count, row.trailing_baseline_count, 1);
  return `${Math.max((v / max) * 100, 8)}%`;
}

/* ====================================================================== EVIDENCE */
export function Evidence({
  articles,
  selected,
  showAll,
  totalCount,
  onToggleAll,
}: {
  articles: LiteratureArticle[];
  selected?: EmergingSignal;
  showAll: boolean;
  totalCount: number;
  onToggleAll: () => void;
}) {
  const scoped = Boolean(selected) && !showAll;
  return (
    <div className="grid-2">
      <Panel
        title="Literature evidence"
        caption={
          scoped
            ? `Filtered to ${titleCase(selected!.drug_name_normalized)} · ${titleCase(selected!.adverse_event)}. Retrieval support is not causal proof.`
            : "PubMed retrieval support across all signals. Retrieval is not causal proof."
        }
        action={
          selected ? (
            <button className="button secondary" onClick={onToggleAll}>
              <ListFilter size={15} />
              {scoped ? `Show all (${totalCount})` : "Focus selected"}
            </button>
          ) : undefined
        }
      >
        {articles.length ? <EvidenceLine articles={articles} /> : <Empty icon={Inbox}>No matching articles.</Empty>}
        <div className="rows" style={{ marginTop: "0.6rem" }}>
          {articles.map((a) => (
            <a className="row link" key={a.pmid} href={a.url} target="_blank" rel="noreferrer" style={{ alignItems: "flex-start" }}>
              <div>
                <p className="row-title">
                  {a.title} <ExternalLink size={12} style={{ opacity: 0.5, verticalAlign: "middle" }} />
                </p>
                <p className="row-meta">
                  {a.journal} · {a.publication_year} · PMID {a.pmid}
                </p>
                {a.evidence_snippet && <p className="row-snippet">“{a.evidence_snippet}”</p>}
              </div>
              <Badge tone="accent">{ratio(a.relevance_score)}</Badge>
            </a>
          ))}
        </div>
      </Panel>
      <div className="side-stack">
        <Panel title="Retrieval summary">
          <div className="kpi-grid">
            <Stat icon={BookOpen} label="Articles" value={fullNumber(articles.length)} />
            <Stat
              icon={Sparkles}
              label="Mean relevance"
              value={articles.length ? ratio(articles.reduce((s, a) => s + a.relevance_score, 0) / articles.length) : "—"}
            />
          </div>
        </Panel>
      </div>
    </div>
  );
}

/* ====================================================================== NHANES */
export function Nhanes({ rows }: { rows: NhanesContext[] }) {
  return (
    <Panel
      title="NHANES population context"
      caption="Aggregate medication-user characteristics from NHANES survey weights. Not linked to FAERS person-level reports."
    >
      {rows.length ? (
        <>
          <NhanesBars rows={rows} />
          <div className="table-wrap" style={{ marginTop: "1rem" }}>
            <table style={{ minWidth: "48rem" }}>
              <thead>
                <tr>
                  <th>Medication</th>
                  <th>Class</th>
                  <th className="right"><span className="th-inner">Prevalence</span></th>
                  <th className="right"><span className="th-inner">Sample n</span></th>
                  <th className="right"><span className="th-inner">Median age</span></th>
                  <th className="right"><span className="th-inner">Female</span></th>
                  <th className="right"><span className="th-inner">BMI ≥ 30</span></th>
                  <th className="right"><span className="th-inner">Diabetes</span></th>
                  <th>Stability</th>
                </tr>
              </thead>
              <tbody>
                {[...rows]
                  .sort((a, b) => b.weighted_prevalence - a.weighted_prevalence)
                  .map((r) => (
                    <tr key={r.medication_name_normalized}>
                      <td className="drug-cell">{titleCase(r.medication_name_normalized)}</td>
                      <td style={{ color: "var(--text-muted)" }}>{r.drug_class}</td>
                      <td className="num">{percent(r.weighted_prevalence, 2)}</td>
                      <td className="num">{fullNumber(r.unweighted_sample_count)}</td>
                      <td className="num">{r.median_age}</td>
                      <td className="num">{r.female_percent}%</td>
                      <td className="num">{r.bmi_ge_30_percent}%</td>
                      <td className="num">{r.diabetes_percent}%</td>
                      <td>
                        {r.very_small_n_flag ? (
                          <Badge tone="fail">Very small n</Badge>
                        ) : r.small_n_flag ? (
                          <Badge tone="warn">Small n</Badge>
                        ) : (
                          <Badge tone="pass">Stable</Badge>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <Empty icon={Inbox}>No NHANES context available.</Empty>
      )}
    </Panel>
  );
}

/* ====================================================================== HEALTH */
export function Health({ data }: { data: DashboardData }) {
  const latest = data.pipeline_health[0];
  return (
    <div className="grid-2">
      <Panel title="Pipeline run" caption="Operational provenance and throughput for the latest gold build.">
        <section className="kpi-grid">
          <Stat icon={Database} label="Status" value={latest?.status.toUpperCase() ?? "—"} foot={latest?.source_period ?? ""} />
          <Stat icon={Layers} label="Gold rows" value={compactNumber(latest?.rows_gold ?? 0)} foot={`${compactNumber(latest?.rows_silver ?? 0)} silver`} />
          <Stat icon={BarChart3} label="Raw FAERS rows" value={compactNumber(latest?.rows_raw ?? 0)} foot={latest?.source ?? ""} />
          <Stat icon={AlertTriangle} label="Checks" value={`${latest?.warning_checks ?? 0} warn`} foot={`${latest?.failed_checks ?? 0} failed`} />
        </section>
        {latest?.notes && (
          <div className="disclaimer" style={{ marginTop: "1rem" }}>
            <AlertTriangle size={15} />
            <span>{latest.notes}</span>
          </div>
        )}
      </Panel>
      <Panel title="Data-quality checks" caption="Latest validation results across the lakehouse.">
        <div className="rows">
          {data.data_quality_checks.map((c) => (
            <div className="row" key={`${c.table}-${c.check}`}>
              <div>
                <p className="row-title">{titleCase(c.check)}</p>
                <p className="row-meta">
                  {c.table} · {c.category} · {c.detail}
                </p>
              </div>
              <Badge tone={statusTone[c.status]} dot>
                {c.status}
              </Badge>
            </div>
          ))}
          {!data.data_quality_checks.length && <Empty icon={Inbox}>No checks reported.</Empty>}
        </div>
      </Panel>
    </div>
  );
}

/* ====================================================================== INTERACTIONS */
export function Interactions({ rows }: { rows: InteractionSignal[] }) {
  const sorted = [...rows].sort((a, b) => (b.interaction_ratio ?? 0) - (a.interaction_ratio ?? 0));
  const flaggedCount = rows.filter((r) => r.interaction_flag).length;
  const max = Math.max(1, ...rows.flatMap((r) => [r.single_max_ror ?? 0, r.ror_combination]));
  const strongest = sorted[0];

  return (
    <>
      <div className="grid-2">
        <Panel
          title="Interaction synergy map"
          caption="Combination ROR vs. the strongest single-agent ROR. Points above the dashed line (y = x) report more strongly together than either drug alone — candidate interactions."
        >
          {rows.length ? (
            <InteractionScatter rows={rows} />
          ) : (
            <Empty icon={Inbox}>No co-reported drug-pair signals are available.</Empty>
          )}
        </Panel>
        <div className="side-stack">
          <Panel title="Summary" caption="Co-reported drug pairs in the current domain.">
            <div className="kpi-grid">
              <Stat icon={Workflow} label="Pairs analyzed" value={fullNumber(rows.length)} foot="co-reported ≥ threshold" />
              <Stat icon={AlertTriangle} label="Flagged interactions" value={fullNumber(flaggedCount)} foot="ratio ≥ 2× & CI > 1" />
              <Stat
                icon={TrendingUp}
                label="Strongest"
                value={strongest ? `${ratio(strongest.interaction_ratio)}×` : "—"}
                foot={strongest ? `${titleCase(strongest.drug_a)} + ${titleCase(strongest.drug_b)}` : undefined}
              />
            </div>
            <div className="disclaimer" style={{ marginTop: "1rem" }}>
              <Info size={15} />
              <span>
                An interaction <em>reporting</em> signal is not proof of a pharmacological interaction. Co-reporting
                is subject to confounding by indication and co-prescription patterns.
              </span>
            </div>
          </Panel>
        </div>
      </div>

      <Panel
        title="Ranked interactions"
        caption="Sorted by interaction ratio (combination ROR ÷ strongest single-agent ROR). The bar shows the jump from single-agent to combination reporting."
      >
        {sorted.length ? (
          sorted.map((r) => (
            <div className="int-row" key={`${r.drug_a}-${r.drug_b}-${r.adverse_event}`}>
              <div>
                <p className="int-pair">
                  {titleCase(r.drug_a)}
                  <span className="plus">+</span>
                  {titleCase(r.drug_b)}
                </p>
                <p className="int-meta">
                  {titleCase(r.adverse_event)} · {fullNumber(r.pair_event_reports)} co-event reports ·{" "}
                  {fullNumber(r.co_reports)} co-reports
                </p>
              </div>
              <div>
                <Dumbbell low={r.single_max_ror ?? 0} high={r.ror_combination} max={max} />
                <div className="db-scale">
                  <span>single {ratio(r.single_max_ror)}×</span>
                  <span>combo {ratio(r.ror_combination)}×</span>
                </div>
              </div>
              <div className="int-end">
                <div className="int-ratio">
                  {ratio(r.interaction_ratio)}×<span>interaction</span>
                </div>
                {r.interaction_flag ? <Badge tone="high" dot>Flagged</Badge> : <Badge tone="neutral">—</Badge>}
              </div>
            </div>
          ))
        ) : (
          <Empty icon={Inbox}>No interaction signals to rank.</Empty>
        )}
      </Panel>
    </>
  );
}

/* ====================================================================== METHOD */
const methods: { icon: typeof Layers; title: string; body: string }[] = [
  { icon: BarChart3, title: "Disproportionality", body: "ROR and PRR are computed from 2×2 reporting tables with 95% confidence intervals and continuity correction for zero cells." },
  { icon: Microscope, title: "Bayesian shrinkage", body: "An empirical-Bayes-style score down-weights unstable low-count pairs, reducing false positives in prioritization." },
  { icon: ShieldAlert, title: "Composite priority", body: "Priority blends disproportionality, trend anomaly, seriousness, literature retrieval support, and NHANES context." },
  { icon: TrendingUp, title: "Trend anomaly", body: "Current-quarter counts are compared against trailing baselines using percent change and anomaly z-scores." },
  { icon: AlertTriangle, title: "Responsible use", body: "Outputs are hypothesis-generating. FAERS has reporting bias and lacks an incidence denominator; signals are not causal." },
  { icon: Database, title: "Sources", body: "openFDA FAERS, FDA quarterly extracts, NHANES survey cycles, and NCBI E-utilities (PubMed)." },
];

export function Method() {
  return (
    <div className="method-grid">
      {methods.map((m) => (
        <Panel key={m.title} className="method-card">
          <h3>
            <span className="stat-icon">
              <m.icon size={14} />
            </span>
            {m.title}
          </h3>
          <p>{m.body}</p>
        </Panel>
      ))}
    </div>
  );
}
