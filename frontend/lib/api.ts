import { demoData } from "./demo-data";
import type { DashboardData } from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_PHARMASIGNAL_API_BASE_URL?.replace(/\/$/, "");

export async function getDashboardData(): Promise<DashboardData> {
  if (!API_BASE_URL) {
    return demoData;
  }

  try {
    const response = await fetch(`${API_BASE_URL}/dashboard/summary`, {
      headers: { accept: "application/json" },
      next: { revalidate: 300 },
    });

    if (!response.ok) {
      throw new Error(`AWS API returned ${response.status}`);
    }

    return (await response.json()) as DashboardData;
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
