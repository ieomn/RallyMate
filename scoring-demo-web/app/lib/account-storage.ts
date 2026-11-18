import { getAccountRequestState } from "./auth-client";

export function accountStorageKey(baseKey: string, userId: string | null = getAccountRequestState().userId): string {
  return `${baseKey}:account:${userId ? encodeURIComponent(userId) : "anonymous"}`;
}
