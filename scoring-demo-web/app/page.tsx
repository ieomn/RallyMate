import { redirect } from "next/navigation";
import ScoreLab from "./ScoreLab";
import registry from "./data/metric-cards.json";

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  // Keep explicit workspace and report links; the public entry opens marketing.
  if (!params.view && !params.job) redirect("/welcome");

  return (
    <ScoreLab
      registryVersion={registry.registryVersion}
      sources={registry.sources}
    />
  );
}
