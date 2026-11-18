import { redirect } from "next/navigation";
import ScoreLab from "./ScoreLab";
import AuthGuard from "./auth/AuthGuard";
import { rootEntryRedirect } from "./lib/workspace-navigation";

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const destination = rootEntryRedirect(params.view, params.job);
  if (destination) redirect(destination);

  return <AuthGuard><ScoreLab /></AuthGuard>;
}
