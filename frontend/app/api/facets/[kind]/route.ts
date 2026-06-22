import { NextResponse } from "next/server";

import { getFacet, isLiveBackend, type FacetKind } from "@/lib/api";
import { demoData } from "@/lib/demo-data";

// Same-origin proxy for distinct-value facets (drug classes, drugs, events). Backed by
// precomputed marts on the API; falls back to deriving from the bundled demo sample.
export const dynamic = "force-dynamic";

const KINDS: FacetKind[] = ["drug-classes", "drugs", "events"];

function demoFacet(kind: FacetKind): unknown {
  const rows = demoData.signal_sample;
  if (kind === "drug-classes") {
    return Array.from(new Set(rows.map((r) => r.drug_class))).sort();
  }
  if (kind === "drugs") {
    const byDrug = new Map<string, { drug_class: string; report_count: number }>();
    for (const r of rows) {
      const cur = byDrug.get(r.drug_name_normalized);
      if (!cur || r.a_drug_event > cur.report_count) {
        byDrug.set(r.drug_name_normalized, { drug_class: r.drug_class, report_count: r.a_drug_event });
      }
    }
    return Array.from(byDrug, ([drug_name_normalized, v]) => ({ drug_name_normalized, ...v }))
      .sort((a, b) => b.report_count - a.report_count);
  }
  // events
  const byEvent = new Map<string, number>();
  for (const r of rows) byEvent.set(r.adverse_event, Math.max(byEvent.get(r.adverse_event) ?? 0, r.a_drug_event));
  return Array.from(byEvent, ([adverse_event, report_count]) => ({ adverse_event, report_count }))
    .sort((a, b) => b.report_count - a.report_count);
}

export async function GET(_req: Request, { params }: { params: Promise<{ kind: string }> }) {
  const { kind } = await params;
  if (!KINDS.includes(kind as FacetKind)) {
    return NextResponse.json({ error: `unknown facet '${kind}'` }, { status: 404 });
  }
  const k = kind as FacetKind;
  if (isLiveBackend) {
    try {
      return NextResponse.json(await getFacet(k));
    } catch (error) {
      console.error(`/api/facets/${k} falling back to demo:`, error);
    }
  }
  return NextResponse.json(demoFacet(k));
}
