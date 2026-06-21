"use client";

import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { signedPercent } from "@/lib/format";

/* ------------------------------------------------------------------ Panel */
export function Panel({
  title,
  caption,
  action,
  flush,
  className = "",
  children,
}: {
  title?: string;
  caption?: string;
  action?: ReactNode;
  flush?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`panel ${flush ? "flush" : ""} ${className}`.trim()}>
      {(title || action) && (
        <div className="panel-header" style={flush ? { padding: "1.1rem 1.15rem 0" } : undefined}>
          <div>
            {title && <h2 className="panel-title">{title}</h2>}
            {caption && <p className="panel-caption">{caption}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

/* ------------------------------------------------------------------ Stat */
export function Stat({
  icon: Icon,
  label,
  value,
  foot,
  delta,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  foot?: string;
  /** fractional change, e.g. 0.38 → +38%. Up = worse (red) by convention here. */
  delta?: number | null;
}) {
  return (
    <div className="stat">
      <div className="stat-top">
        <span className="stat-icon">
          <Icon size={15} aria-hidden />
        </span>
        <span className="stat-label">{label}</span>
      </div>
      <div className="stat-value">{value}</div>
      {(foot || delta != null) && (
        <div className="stat-foot">
          {delta != null && (
            <span className={`delta ${delta >= 0 ? "up" : "down"}`}>
              {delta >= 0 ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}
              {signedPercent(delta)}
            </span>
          )}
          {foot && <span>{foot}</span>}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ Badge */
export type BadgeTone =
  | "accent"
  | "high"
  | "moderate"
  | "low"
  | "novel"
  | "neutral"
  | "pass"
  | "warn"
  | "fail";

export function Badge({
  tone = "neutral",
  dot,
  children,
}: {
  tone?: BadgeTone;
  dot?: boolean;
  children: ReactNode;
}) {
  return (
    <span className={`badge ${tone}`}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ Empty */
export function Empty({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <div className="empty">
      <Icon size={26} aria-hidden />
      <p>{children}</p>
    </div>
  );
}
