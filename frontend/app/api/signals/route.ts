import { NextRequest, NextResponse } from "next/server";

import { getSignals, isLiveBackend, type SignalsQuery } from "@/lib/api";
import { demoData } from "@/lib/demo-data";
import type { SignalScore, SignalsPage } from "@/lib/types";

// Same-origin proxy for the paginated /signals endpoint. Client components fetch this so
// the browser never needs the API URL, and demo mode still works offline. The full matrix
// is paged here — never shipped whole. Always dynamic (depends on query params).
export const dynamic = "force-dynamic";

function parseQuery(sp: URLSearchParams): SignalsQuery {
  const num = (k: string) => (sp.get(k) != null ? Number(sp.get(k)) : undefined);
  return {
    drug: sp.get("drug") ?? undefined,
    event: sp.get("event") ?? undefined,
    drug_class: sp.get("drug_class") ?? undefined,
    flagged_only: sp.get("flagged_only") === "true" || undefined,
    min_reports: num("min_reports"),
    q: sp.get("q") ?? undefined,
    sort: sp.get("sort") ?? undefined,
    desc: sp.get("desc") != null ? sp.get("desc") === "true" : undefined,
    offset: num("offset"),
    limit: num("limit"),
  };
}

/** Demo fallback: filter/sort/paginate the bundled sample in-process. */
function demoPage(p: SignalsQuery): SignalsPage {
  const q = (p.q ?? "").trim().toUpperCase();
  const sortKey = (p.sort ?? "ror") as keyof SignalScore;
  const desc = p.desc ?? true;
  const filtered = demoData.signal_sample
    .filter((r) => !p.drug_class || p.drug_class === "All" || r.drug_class === p.drug_class)
    .filter((r) => !p.min_reports || r.a_drug_event >= p.min_reports)
    .filter((r) => !p.flagged_only || r.disproportionality_flag)
    .filter((r) => !q || `${r.drug_name_normalized} ${r.adverse_event}`.toUpperCase().includes(q))
    .sort((a, b) => {
      const av = Number(a[sortKey] ?? 0);
      const bv = Number(b[sortKey] ?? 0);
      return desc ? bv - av : av - bv;
    });
  const offset = Math.max(0, p.offset ?? 0);
  const limit = Math.min(Math.max(1, p.limit ?? 100), 1000);
  return { total: filtered.length, offset, limit, rows: filtered.slice(offset, offset + limit) };
}

export async function GET(req: NextRequest) {
  const params = parseQuery(req.nextUrl.searchParams);
  if (isLiveBackend) {
    try {
      return NextResponse.json(await getSignals(params));
    } catch (error) {
      console.error("/api/signals falling back to demo:", error);
    }
  }
  return NextResponse.json(demoPage(params));
}
