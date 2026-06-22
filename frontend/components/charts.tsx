"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { compactNumber, fullNumber, percent, ratio, titleCase } from "@/lib/format";
import { useChartPalette } from "@/lib/theme";
import type {
  EmergingSignal,
  InteractionSignal,
  LiteratureArticle,
  NhanesContext,
  SignalScore,
  SubgroupSignal,
} from "@/lib/types";

/** Recharts measures 0×0 during SSR; gate on mount so it sizes correctly. */
function ClientChart({ children, short }: { children: ReactNode; short?: boolean }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return (
    <div className={`chart-wrap ${short ? "short" : ""}`.trim()}>
      {mounted ? <ResponsiveContainer>{children as React.ReactElement}</ResponsiveContainer> : null}
    </div>
  );
}

const key = (r: { drug_name_normalized: string; adverse_event: string }) =>
  `${r.drug_name_normalized}::${r.adverse_event}`;

/* ----------------------------------------------------------- tooltips */
function TooltipShell({ title, sub, rows }: { title: string; sub?: string; rows: [string, string][] }) {
  return (
    <div className="chart-tooltip">
      <div className="tt-title">{title}</div>
      {sub && <div className="tt-row" style={{ marginTop: 2 }}><span>{sub}</span></div>}
      {rows.map(([k, v]) => (
        <div className="tt-row" key={k}>
          <span>{k}</span>
          <b>{v}</b>
        </div>
      ))}
    </div>
  );
}

function SignalTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: SignalScore }> }) {
  if (!active || !payload?.[0]) return null;
  const r = payload[0].payload;
  return (
    <TooltipShell
      title={`${titleCase(r.drug_name_normalized)} · ${titleCase(r.adverse_event)}`}
      sub={r.drug_class}
      rows={[
        ["Reports", fullNumber(r.a_drug_event)],
        ["ROR", `${ratio(r.ror)} (${ratio(r.ror_ci_lower)}–${ratio(r.ror_ci_upper)})`],
        ["PRR", ratio(r.prr)],
        ["Serious", percent(r.seriousness_rate)],
      ]}
    />
  );
}

function EmergingTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: EmergingSignal }> }) {
  if (!active || !payload?.[0]) return null;
  const r = payload[0].payload;
  return (
    <TooltipShell
      title={`${titleCase(r.drug_name_normalized)} · ${titleCase(r.adverse_event)}`}
      sub={`${r.priority_level} priority`}
      rows={[
        ["Priority", ratio(r.priority_score)],
        ["Anomaly z", ratio(r.anomaly_score)],
        ["Current", fullNumber(r.current_count)],
        ["Serious", percent(r.seriousness_rate)],
      ]}
    />
  );
}

/* ----------------------------------------------------------- pan + zoom for scatter charts */
type Domain = [number, number];
type Extent = { x: Domain; y: Domain };
// Approx plotting-area insets (chart margins + Y-axis width / X-axis label band), used to
// map cursor pixels to data coordinates. Small errors only nudge the zoom anchor slightly.
const PLOT_INSET = { left: 56, right: 20, top: 16, bottom: 44 };
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Scroll-to-zoom (anchored at the cursor) + drag-to-pan over a Recharts number/number chart.
 *  Returns axis domains to feed XAxis/YAxis (with allowDataOverflow) plus DOM handlers. */
function usePanZoom(ext: Extent) {
  const ref = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<Extent | null>(null);
  const drag = useRef<{ px: number; py: number; dom: Extent } | null>(null);
  const raf = useRef<number | null>(null);
  const pending = useRef<Extent | null>(null);

  const x = view ? view.x : ext.x;
  const y = view ? view.y : ext.y;

  // Batch domain updates to one per animation frame so panning thousands of points stays smooth.
  const apply = (v: Extent) => {
    pending.current = v;
    if (raf.current == null) {
      raf.current = requestAnimationFrame(() => {
        raf.current = null;
        if (pending.current) setView(pending.current);
      });
    }
  };
  useEffect(() => () => { if (raf.current != null) cancelAnimationFrame(raf.current); }, []);

  const plot = () => {
    const r = ref.current!.getBoundingClientRect();
    return {
      l: r.left + PLOT_INSET.left, t: r.top + PLOT_INSET.top,
      w: Math.max(1, r.width - PLOT_INSET.left - PLOT_INSET.right),
      h: Math.max(1, r.height - PLOT_INSET.top - PLOT_INSET.bottom),
    };
  };

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const pr = plot();
      const cx = view ? view.x : ext.x;
      const cy = view ? view.y : ext.y;
      const fx = clamp((e.clientX - pr.l) / pr.w, 0, 1);
      const fy = clamp((e.clientY - pr.t) / pr.h, 0, 1);
      const ax = cx[0] + fx * (cx[1] - cx[0]);          // data x under cursor
      const ay = cy[1] - fy * (cy[1] - cy[0]);          // data y under cursor (screen y inverted)
      const k = e.deltaY < 0 ? 0.85 : 1 / 0.85;         // wheel up = zoom in
      const fullX = ext.x[1] - ext.x[0] || 1;
      const fullY = ext.y[1] - ext.y[0] || 1;
      const xs = clamp((cx[1] - cx[0]) * k, fullX / 500, fullX);
      const ys = clamp((cy[1] - cy[0]) * k, fullY / 500, fullY);
      apply({ x: [ax - fx * xs, ax + (1 - fx) * xs], y: [ay - (1 - fy) * ys, ay + fy * ys] });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [view, ext]);

  const onPointerDown = (e: React.PointerEvent) => {
    drag.current = { px: e.clientX, py: e.clientY, dom: { x, y } };
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const pr = plot();
    const dx = ((e.clientX - d.px) / pr.w) * (d.dom.x[1] - d.dom.x[0]);
    const dy = ((e.clientY - d.py) / pr.h) * (d.dom.y[1] - d.dom.y[0]);
    apply({ x: [d.dom.x[0] - dx, d.dom.x[1] - dx], y: [d.dom.y[0] + dy, d.dom.y[1] + dy] });
  };
  const onPointerUp = () => { drag.current = null; };

  return { ref, xDomain: x, yDomain: y, zoomed: view != null, reset: () => setView(null),
           onPointerDown, onPointerMove, onPointerUp };
}

/* ----------------------------------------------------------- Overview: volcano-style scatter */
export function SignalScatter({ rows }: { rows: SignalScore[] }) {
  const p = useChartPalette();
  const data = useMemo(
    () => rows.map((r) => ({ ...r, logRor: Math.log10(Math.max(r.ror, 0.01)) })),
    [rows],
  );
  const ext = useMemo<Extent>(() => {
    if (!data.length) return { x: [-1, 1], y: [0, 1] };
    const xs = data.map((d) => d.logRor);
    const ys = data.map((d) => d.a_drug_event);
    const xmin = Math.min(...xs), xmax = Math.max(...xs), ymax = Math.max(...ys);
    const xpad = (xmax - xmin || 1) * 0.05, ypad = (ymax || 1) * 0.05;
    return { x: [xmin - xpad, xmax + xpad], y: [0, ymax + ypad] };
  }, [data]);
  const pz = usePanZoom(ext);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <div
      className="chart-wrap"
      ref={pz.ref}
      style={{ position: "relative", touchAction: "none", cursor: "grab" }}
      onPointerDown={pz.onPointerDown}
      onPointerMove={pz.onPointerMove}
      onPointerUp={pz.onPointerUp}
      onPointerLeave={pz.onPointerUp}
    >
      <span style={{ position: "absolute", top: 6, left: 10, fontSize: 10, opacity: 0.55, pointerEvents: "none", zIndex: 2 }}>
        scroll to zoom · drag to pan
      </span>
      {pz.zoomed && (
        <button
          type="button"
          onClick={pz.reset}
          style={{ position: "absolute", top: 4, right: 6, zIndex: 2, fontSize: 11, padding: "2px 8px",
                   borderRadius: 6, cursor: "pointer", border: `1px solid ${p.grid}`, background: "transparent", color: p.axis }}
        >
          Reset view
        </button>
      )}
      {mounted && (
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 16, right: 20, bottom: 28, left: 8 }}>
            <CartesianGrid stroke={p.grid} strokeDasharray="2 4" />
            <XAxis
              type="number"
              dataKey="logRor"
              domain={pz.zoomed ? pz.xDomain : undefined}
              allowDataOverflow={pz.zoomed}
              stroke={p.axis}
              tickLine={false}
              axisLine={{ stroke: p.grid }}
              tickFormatter={(v) => `${Math.pow(10, Number(v)).toFixed(1)}×`}
              label={{ value: "Reporting odds ratio (log scale)", position: "insideBottom", offset: -14, fill: p.axis, fontSize: 11 }}
            />
            <YAxis
              type="number"
              dataKey="a_drug_event"
              domain={pz.zoomed ? pz.yDomain : undefined}
              allowDataOverflow={pz.zoomed}
              stroke={p.axis}
              tickLine={false}
              axisLine={{ stroke: p.grid }}
              tickFormatter={(v) => compactNumber(Number(v))}
              width={48}
            />
            <ZAxis dataKey="seriousness_rate" range={[60, 460]} />
            <ReferenceLine x={0} stroke={p.muted} strokeDasharray="3 3" />
            <Tooltip content={<SignalTooltip />} cursor={{ strokeDasharray: "3 3", stroke: p.muted }} />
            <Scatter data={data} isAnimationActive={false}>
              {data.map((e) => (
                <Cell key={key(e)} fill={e.disproportionality_flag ? p.flagged : p.blue} fillOpacity={0.78} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

/* ----------------------------------------------------------- horizontal ROR bars */
export function RorBars({ rows }: { rows: SignalScore[] }) {
  const p = useChartPalette();
  const data = rows.slice(0, 14).map((r) => ({
    ...r,
    rowKey: key(r),
    label: titleCase(r.adverse_event),
  }));
  return (
    <ClientChart>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 28, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={p.grid} strokeDasharray="2 4" horizontal={false} />
        <XAxis type="number" stroke={p.axis} tickLine={false} axisLine={{ stroke: p.grid }} />
        <YAxis
          type="category"
          dataKey="rowKey"
          width={150}
          stroke={p.axis}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => {
            const row = data.find((d) => d.rowKey === v);
            return row ? `${titleCase(row.drug_name_normalized).slice(0, 8)} · ${row.label.slice(0, 16)}` : "";
          }}
        />
        <Tooltip content={<SignalTooltip />} cursor={{ fill: p.grid, fillOpacity: 0.4 }} />
        <Bar dataKey="ror" radius={[0, 4, 4, 0]} isAnimationActive={false} barSize={16}>
          {data.map((e) => (
            <Cell key={e.rowKey} fill={e.disproportionality_flag ? p.flagged : p.blue} />
          ))}
        </Bar>
      </BarChart>
    </ClientChart>
  );
}

/* ----------------------------------------------------------- drug profile: report counts */
export function ProfileBars({ rows }: { rows: SignalScore[] }) {
  const p = useChartPalette();
  const data = rows.slice(0, 12).map((r) => ({ ...r, rowKey: key(r), label: titleCase(r.adverse_event) }));
  return (
    <ClientChart short>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 28, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={p.grid} strokeDasharray="2 4" horizontal={false} />
        <XAxis type="number" stroke={p.axis} tickLine={false} axisLine={{ stroke: p.grid }} tickFormatter={(v) => compactNumber(Number(v))} />
        <YAxis
          type="category"
          dataKey="rowKey"
          width={150}
          stroke={p.axis}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => data.find((d) => d.rowKey === v)?.label.slice(0, 22) ?? ""}
        />
        <Tooltip content={<SignalTooltip />} cursor={{ fill: p.grid, fillOpacity: 0.4 }} />
        <Bar dataKey="a_drug_event" fill={p.gold} radius={[0, 4, 4, 0]} isAnimationActive={false} barSize={15} />
      </BarChart>
    </ClientChart>
  );
}

/* ----------------------------------------------------------- emerging scatter */
export function EmergingScatter({ rows }: { rows: EmergingSignal[] }) {
  const p = useChartPalette();
  const tone: Record<string, string> = { High: p.high, Moderate: p.moderate, Low: p.low };
  return (
    <ClientChart>
      <ScatterChart margin={{ top: 16, right: 20, bottom: 28, left: 8 }}>
        <CartesianGrid stroke={p.grid} strokeDasharray="2 4" />
        <XAxis
          type="number"
          dataKey="anomaly_score"
          stroke={p.axis}
          tickLine={false}
          axisLine={{ stroke: p.grid }}
          tickFormatter={(v) => `${Number(v).toFixed(1)}σ`}
          label={{ value: "Trend anomaly (z-score)", position: "insideBottom", offset: -14, fill: p.axis, fontSize: 11 }}
        />
        <YAxis
          type="number"
          dataKey="seriousness_rate"
          stroke={p.axis}
          tickLine={false}
          axisLine={{ stroke: p.grid }}
          tickFormatter={(v) => percent(Number(v))}
          width={48}
        />
        <ZAxis dataKey="current_count" range={[80, 480]} />
        <Tooltip content={<EmergingTooltip />} cursor={{ strokeDasharray: "3 3", stroke: p.muted }} />
        <Scatter data={rows} isAnimationActive={false}>
          {rows.map((e) => (
            <Cell key={key(e)} fill={tone[e.priority_level]} fillOpacity={0.8} />
          ))}
        </Scatter>
      </ScatterChart>
    </ClientChart>
  );
}

/* ----------------------------------------------------------- evidence over time */
export function EvidenceLine({ articles }: { articles: LiteratureArticle[] }) {
  const p = useChartPalette();
  const byYear = articles.reduce<Record<number, number>>((acc, a) => {
    acc[a.publication_year] = (acc[a.publication_year] ?? 0) + 1;
    return acc;
  }, {});
  const data = Object.entries(byYear)
    .map(([year, count]) => ({ year, count }))
    .sort((a, b) => Number(a.year) - Number(b.year));
  return (
    <ClientChart short>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
        <CartesianGrid stroke={p.grid} strokeDasharray="2 4" />
        <XAxis dataKey="year" stroke={p.axis} tickLine={false} axisLine={{ stroke: p.grid }} />
        <YAxis stroke={p.axis} tickLine={false} axisLine={false} allowDecimals={false} width={28} />
        <Tooltip
          contentStyle={{ background: p.tooltipBg, border: `1px solid ${p.grid}`, borderRadius: 7, fontSize: 12 }}
          labelStyle={{ color: p.axis }}
        />
        <Line dataKey="count" name="Articles" stroke={p.accent} strokeWidth={2.5} dot={{ r: 4, fill: p.accent }} isAnimationActive={false} />
      </LineChart>
    </ClientChart>
  );
}

/* ----------------------------------------------------------- interactions: synergy scatter */
function InteractionTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: InteractionSignal }> }) {
  if (!active || !payload?.[0]) return null;
  const r = payload[0].payload;
  return (
    <TooltipShell
      title={`${titleCase(r.drug_a)} + ${titleCase(r.drug_b)}`}
      sub={titleCase(r.adverse_event)}
      rows={[
        ["Combination ROR", `${ratio(r.ror_combination)} (${ratio(r.ror_ci_lower)}–${ratio(r.ror_ci_upper)})`],
        ["Strongest single", ratio(r.single_max_ror)],
        ["Interaction ×", ratio(r.interaction_ratio)],
        ["Co-reports", fullNumber(r.co_reports)],
      ]}
    />
  );
}

export function InteractionScatter({ rows }: { rows: InteractionSignal[] }) {
  const p = useChartPalette();
  const data = rows.filter((r) => r.single_max_ror != null);
  const max = Math.ceil(Math.max(1, ...data.flatMap((r) => [r.single_max_ror ?? 0, r.ror_combination])) * 1.1);
  return (
    <ClientChart>
      <ScatterChart margin={{ top: 16, right: 22, bottom: 28, left: 8 }}>
        <CartesianGrid stroke={p.grid} strokeDasharray="2 4" />
        <XAxis
          type="number"
          dataKey="single_max_ror"
          domain={[0, max]}
          stroke={p.axis}
          tickLine={false}
          axisLine={{ stroke: p.grid }}
          tickFormatter={(v) => `${v}×`}
          label={{ value: "Strongest single-agent ROR", position: "insideBottom", offset: -14, fill: p.axis, fontSize: 11 }}
        />
        <YAxis
          type="number"
          dataKey="ror_combination"
          domain={[0, max]}
          stroke={p.axis}
          tickLine={false}
          axisLine={{ stroke: p.grid }}
          tickFormatter={(v) => `${v}×`}
          width={44}
        />
        <ZAxis dataKey="pair_event_reports" range={[70, 430]} />
        {/* y = x : combination equals the stronger single drug → no interaction */}
        <ReferenceLine
          segment={[{ x: 0, y: 0 }, { x: max, y: max }]}
          stroke={p.muted}
          strokeDasharray="5 4"
          ifOverflow="extendDomain"
          label={{ value: "no interaction (y = x)", position: "insideTopRight", fill: p.axis, fontSize: 10 }}
        />
        <Tooltip content={<InteractionTooltip />} cursor={{ strokeDasharray: "3 3", stroke: p.muted }} />
        <Scatter data={data} isAnimationActive={false}>
          {data.map((e) => (
            <Cell key={`${e.drug_a}-${e.drug_b}-${e.adverse_event}`} fill={e.interaction_flag ? p.high : p.blue} fillOpacity={0.82} />
          ))}
        </Scatter>
      </ScatterChart>
    </ClientChart>
  );
}

/* dumbbell: visual jump from strongest single ROR to the combination ROR */
export function Dumbbell({ low, high, max }: { low: number; high: number; max: number }) {
  const s = (v: number) => `${Math.min(Math.max((v / max) * 100, 0), 100)}%`;
  const synergy = high > low;
  return (
    <div className="dumbbell" title={`${ratio(low)}× → ${ratio(high)}×`}>
      <span className={`db-bar ${synergy ? "up" : ""}`} style={{ left: s(Math.min(low, high)), width: `calc(${s(Math.abs(high - low))})` }} />
      <span className="db-dot low" style={{ left: s(low) }} />
      <span className={`db-dot high ${synergy ? "up" : ""}`} style={{ left: s(high) }} />
    </div>
  );
}

/* ----------------------------------------------------------- subgroups: forest plot */
export function ForestPlot({ rows }: { rows: SubgroupSignal[] }) {
  if (!rows.length) return null;
  const overall = rows[0].overall_ror;
  const lo = Math.min(1, ...rows.map((r) => r.ror_ci_lower));
  const hi = Math.max(overall, ...rows.map((r) => r.ror_ci_upper));
  const min = Math.max(0, lo * 0.9);
  const max = hi * 1.05;
  const scale = (v: number) => `${((v - min) / (max - min)) * 100}%`;

  const types = Array.from(new Set(rows.map((r) => r.subgroup_type)));
  const ticks = niceTicks(min, max, 4);

  return (
    <div className="forest">
      <div className="forest-head">
        <span className="forest-label" />
        <div className="forest-track axis">
          {ticks.map((t) => (
            <span key={t} className="forest-tick" style={{ left: scale(t) }}>
              {t}×
            </span>
          ))}
        </div>
        <span className="forest-num head">ROR (95% CI)</span>
      </div>

      {types.map((type) => (
        <div className="forest-group" key={type}>
          <p className="forest-group-label">{type === "sex" ? "By sex" : "By age band"}</p>
          {rows
            .filter((r) => r.subgroup_type === type)
            .map((r) => {
              const sig = r.ror_ci_lower > 1;
              const elevated = r.ror > overall * 1.15;
              return (
                <div className="forest-row" key={`${type}-${r.subgroup}`}>
                  <span className="forest-label">{titleCase(r.subgroup)}</span>
                  <div className="forest-track">
                    <span className="forest-null" style={{ left: scale(1) }} />
                    <span className="forest-overall" style={{ left: scale(overall) }} />
                    <span
                      className="forest-whisker"
                      style={{ left: scale(r.ror_ci_lower), width: `calc(${scale(r.ror_ci_upper)} - ${scale(r.ror_ci_lower)})` }}
                    />
                    <span className={`forest-dot ${sig ? "sig" : ""} ${elevated ? "elevated" : ""}`} style={{ left: scale(r.ror) }} />
                  </div>
                  <span className="forest-num">
                    {ratio(r.ror)} <i>({ratio(r.ror_ci_lower)}–{ratio(r.ror_ci_upper)})</i>
                  </span>
                </div>
              );
            })}
        </div>
      ))}

      <div className="forest-legend">
        <span><i className="lg-overall" /> overall ROR {ratio(overall)}×</span>
        <span><i className="lg-null" /> no effect (1×)</span>
        <span><i className="lg-dot elevated" /> elevated vs overall</span>
      </div>
    </div>
  );
}

function niceTicks(min: number, max: number, count: number): number[] {
  const step = Math.max(1, Math.round((max - min) / count));
  const out: number[] = [];
  for (let v = Math.ceil(min); v <= max; v += step) out.push(v);
  return out;
}

/* ----------------------------------------------------------- nhanes prevalence */
export function NhanesBars({ rows }: { rows: NhanesContext[] }) {
  const p = useChartPalette();
  const data = [...rows].sort((a, b) => b.weighted_prevalence - a.weighted_prevalence);
  return (
    <ClientChart short>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 28, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={p.grid} strokeDasharray="2 4" horizontal={false} />
        <XAxis type="number" stroke={p.axis} tickLine={false} axisLine={{ stroke: p.grid }} tickFormatter={(v) => percent(Number(v))} />
        <YAxis
          type="category"
          dataKey="medication_name_normalized"
          width={140}
          stroke={p.axis}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => titleCase(String(v))}
        />
        <Tooltip
          formatter={(v) => percent(Number(v), 2)}
          contentStyle={{ background: p.tooltipBg, border: `1px solid ${p.grid}`, borderRadius: 7, fontSize: 12 }}
          labelStyle={{ color: p.axis }}
          cursor={{ fill: p.grid, fillOpacity: 0.4 }}
        />
        <Bar dataKey="weighted_prevalence" fill={p.blue} radius={[0, 4, 4, 0]} isAnimationActive={false} barSize={16} />
      </BarChart>
    </ClientChart>
  );
}
