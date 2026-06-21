import { DashboardClient } from "@/components/dashboard-client";
import { getDashboardData } from "@/lib/api";

export default async function Home() {
  const data = await getDashboardData();
  return <DashboardClient data={data} />;
}
