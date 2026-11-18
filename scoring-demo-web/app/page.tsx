import { redirect } from "next/navigation";
import ScoreLab from "./ScoreLab";
import AuthGuard from "./auth/AuthGuard";

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  if (params.view === "guide") redirect("/?view=overview");
  // Keep explicit workspace and report links; the public entry opens marketing.
  if (!params.view && !params.job) redirect("/welcome");

  return <AuthGuard><ScoreLab /></AuthGuard>;
}
