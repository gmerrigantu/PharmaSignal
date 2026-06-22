import { demoData } from "./demo-data";
import type {
  DashboardData,
  DrugFacet,
  EmergingSignal,
  EventFacet,
  LiteratureArticle,
  NhanesContext,
  PriorityLevel,
  SignalScore,
  SignalsPage,
} from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_PHARMASIGNAL_API_BASE_URL?.replace(/\/$/, "");

/** True when an AWS API base URL is configured; otherwise the app serves demo data. */
export const isLiveBackend = Boolean(API_BASE_URL);

/**
 * Fetch JSON from the PharmaSignal API. Server-rendered (and ISR-cached) so AWS is
 * never called from the browser — the Vercel server hits API Gateway, which holds no
 * client-visible credentials. `revalidate` matches the API's gold-refresh cadence.
 */
async function apiGet<T>(path: string, revalidate = 300): Promise<T> {
  if (!API_BASE_URL) {
    throw new Error("NEXT_PUBLIC_PHARMASIGNAL_API_BASE_URL is not set");
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { accept: "application/json" },
    next: { revalidate },
  });
  if (!response.ok) {
    throw new Error(`PharmaSignal API ${path} returned ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function getDashboardData(): Promise<DashboardData> {
  if (!API_BASE_URL) {
    return demoData;
  }

  try {
    return await apiGet<DashboardData>("/dashboard/summary");
  } catch (error) {
    console.error("Falling back to demo data:", error);
    return {
      ...demoData,
      data_source: "demo",
      pipeline_health: [
        {
          ...demoData.pipeline_health[0],
          status: "warn",
          notes: `AWS API unavailable. Showing demo data. ${error instanceof Error ? error.message : ""}`,
        },
      ],
    };
  }
}

// --- Optional resource endpoints (server-side filtering, for detail views) ------- //

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
};

export type SignalsQuery = {
  drug?: string;
  event?: string;
  drug_class?: string;
  flagged_only?: boolean;
  min_reports?: number;
  q?: string;
  sort?: string;
  desc?: boolean;
  offset?: number;
  limit?: number;
};

/** Server-side: one page of the full matrix from the backend API (used by the route handler). */
export const getSignals = (params: SignalsQuery = {}) =>
  apiGet<SignalsPage>(`/signals${qs(params)}`, 0);

/** Client-side: fetch a page from our same-origin route handler (which proxies the API
 *  or paginates demo data). Safe to call from "use client" components. */
export async function fetchSignalsPage(params: SignalsQuery = {}): Promise<SignalsPage> {
  const res = await fetch(`/api/signals${qs(params)}`, { headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`/api/signals returned ${res.status}`);
  return (await res.json()) as SignalsPage;
}

// --- Facets (full distinct-value lists for pickers) ------------------------------ //

export type FacetKind = "drug-classes" | "drugs" | "events";

/** Server-side: distinct-value facet from the backend API (used by the route handler). */
export const getFacet = <T>(kind: FacetKind) => apiGet<T>(`/facets/${kind}`, 0);

/** Client-side: fetch a facet list from our same-origin route handler. */
export async function fetchDrugClasses(): Promise<string[]> {
  const res = await fetch("/api/facets/drug-classes", { headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`/api/facets/drug-classes returned ${res.status}`);
  return (await res.json()) as string[];
}

export async function fetchDrugFacets(): Promise<DrugFacet[]> {
  const res = await fetch("/api/facets/drugs", { headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`/api/facets/drugs returned ${res.status}`);
  return (await res.json()) as DrugFacet[];
}

export async function fetchEventFacets(): Promise<EventFacet[]> {
  const res = await fetch("/api/facets/events", { headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`/api/facets/events returned ${res.status}`);
  return (await res.json()) as EventFacet[];
}

export const getEmerging = (params: { priority?: PriorityLevel; limit?: number } = {}) =>
  apiGet<EmergingSignal[]>(`/emerging${qs(params)}`);

export const getNhanes = () => apiGet<NhanesContext[]>("/nhanes");

export const getEvidence = (params: { drug?: string; event?: string } = {}) =>
  apiGet<LiteratureArticle[]>(`/evidence${qs(params)}`);

export type DrugProfile = {
  drug_name_normalized: string;
  data_source: DashboardData["data_source"];
  signals: SignalScore[];
  emerging: EmergingSignal[];
  nhanes: NhanesContext[];
  evidence: LiteratureArticle[];
};

export const getDrugProfile = (drug: string) =>
  apiGet<DrugProfile>(`/drugs/${encodeURIComponent(drug)}`);
