"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ACCOUNT_AUTH_REQUIRED_EVENT, AccountError, accountFetch, accountResponse, invalidateAccountRequests, setAccountRequestState, type AccountUser } from "../lib/auth-client";
import { setMonitoringUser } from "../lib/monitoring";

type Registration = { email: string; password: string; username: string; application_reason?: string | null };
type Session = { user: AccountUser; csrf_token: string };
type AuthContextValue = {
  user: AccountUser | null; loading: boolean; error: string | null;
  refresh: () => Promise<AccountUser | null>;
  login: (email: string, password: string) => Promise<AccountUser>;
  register: (input: Registration) => Promise<AccountUser>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  holdSessionRefresh: () => () => void;
};
const AuthContext = createContext<AuthContextValue | null>(null);
const SYNC_KEY = "rallymate:auth-sync:v1";

function securityKey(user: AccountUser | null): string {
  return user ? `${user.id}:${user.role}:${user.status}:${user.must_change_password}` : "anonymous";
}
function validateSession(session: Session): Session {
  const user = session?.user;
  if (!user || typeof user.id !== "string" || !user.id || typeof user.username !== "string" || typeof user.email !== "string" ||
      !["ADMIN", "USER"].includes(user.role) || !["PENDING", "APPROVED", "REJECTED", "SUSPENDED"].includes(user.status) ||
      typeof user.must_change_password !== "boolean" || typeof session.csrf_token !== "string" || !session.csrf_token) {
    throw new AccountError("无法读取账户状态，请重试。", 0, "invalid_response");
  }
  return session;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AccountUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const current = useRef<AccountUser | null>(null);
  const operation = useRef(0);
  const refreshFlight = useRef<Promise<AccountUser | null> | null>(null);
  const refreshHolds = useRef(new Set<symbol>());
  const channel = useRef<BroadcastChannel | null>(null);

  const apply = useCallback((session: Session | null) => {
    if (securityKey(current.current) !== securityKey(session?.user ?? null)) invalidateAccountRequests();
    current.current = session?.user ?? null;
    setAccountRequestState({ userId: session?.user.id ?? null, csrfToken: session?.csrf_token ?? null });
    setMonitoringUser(session?.user.id ?? null);
    setUser(session?.user ?? null);
  }, []);
  const announce = useCallback(() => {
    const message = { nonce: typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`, at: Date.now() };
    channel.current?.postMessage(message);
    try { localStorage.setItem(SYNC_KEY, JSON.stringify(message)); } catch { /* Cross-tab messages also use BroadcastChannel. */ }
  }, []);
  const refresh = useCallback((): Promise<AccountUser | null> => {
    if (refreshHolds.current.size) return Promise.resolve(current.current);
    if (refreshFlight.current) return refreshFlight.current;
    const ticket = ++operation.current;
    const work = (async () => {
      try {
        const response = await accountFetch("/api/auth/me");
        if (response.status === 401) {
          await response.body?.cancel();
          if (ticket === operation.current) { apply(null); setError(null); }
          return null;
        }
        const session = validateSession(await accountResponse<Session>(response));
        if (ticket === operation.current) { apply(session); setError(null); }
        return session.user;
      } catch (cause) {
        if (ticket === operation.current && !(cause instanceof DOMException && cause.name === "AbortError")) setError(cause instanceof AccountError ? cause.message : "暂时无法连接账户服务，请重试。");
        return current.current;
      } finally {
        if (ticket === operation.current) setLoading(false);
      }
    })();
    refreshFlight.current = work;
    void work.finally(() => { if (refreshFlight.current === work) refreshFlight.current = null; });
    return work;
  }, [apply]);

  const holdSessionRefresh = useCallback(() => {
    const token = Symbol("session-refresh");
    refreshHolds.current.add(token);
    operation.current += 1;
    refreshFlight.current = null;
    invalidateAccountRequests();
    return () => { refreshHolds.current.delete(token); };
  }, []);

  useEffect(() => {
    void refresh();
    const changed = () => {
      refreshHolds.current.clear();
      operation.current += 1;
      refreshFlight.current = null;
      apply(null);
      setLoading(true);
      void refresh();
    };
    const storage = (event: StorageEvent) => { if (event.key === SYNC_KEY) changed(); };
    const pageshow = (event: PageTransitionEvent) => { if (event.persisted) changed(); };
    const focus = () => { if (document.visibilityState === "visible") void refresh(); };
    const required = () => { void refresh(); };
    if (typeof BroadcastChannel !== "undefined") {
      channel.current = new BroadcastChannel(SYNC_KEY);
      channel.current.onmessage = changed;
    }
    window.addEventListener("storage", storage);
    window.addEventListener("focus", focus);
    window.addEventListener("pageshow", pageshow);
    window.addEventListener(ACCOUNT_AUTH_REQUIRED_EVENT, required);
    document.addEventListener("visibilitychange", focus);
    return () => {
      channel.current?.close(); channel.current = null;
      window.removeEventListener("storage", storage);
      window.removeEventListener("focus", focus);
      window.removeEventListener("pageshow", pageshow);
      window.removeEventListener(ACCOUNT_AUTH_REQUIRED_EVENT, required);
      document.removeEventListener("visibilitychange", focus);
      operation.current += 1;
      refreshFlight.current = null;
      invalidateAccountRequests();
    };
  }, [apply, refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const ticket = ++operation.current;
    refreshFlight.current = null;
    invalidateAccountRequests();
    const session = validateSession(await accountResponse<Session>(await accountFetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }),
    })));
    if (ticket !== operation.current) throw new DOMException("账户状态已改变。", "AbortError");
    apply(session); setLoading(false); setError(null); announce();
    return session.user;
  }, [apply, announce]);
  const register = useCallback(async (input: Registration) => {
    const result = await accountResponse<{ user: AccountUser }>(await accountFetch("/api/auth/register", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
    }));
    return result.user;
  }, []);
  const logout = useCallback(async () => {
    operation.current += 1; refreshFlight.current = null;
    invalidateAccountRequests();
    try {
      const response = await accountFetch("/api/auth/logout", { method: "POST" });
      if (response.status !== 401) await accountResponse(response);
      else await response.body?.cancel();
      apply(null); setError(null); announce();
    } catch (cause) {
      setError(cause instanceof AccountError ? cause.message : "退出未完成，请检查连接后重试。");
      throw cause;
    }
  }, [apply, announce]);
  const changePassword = useCallback(async (currentPassword: string, newPassword: string) => {
    await accountResponse(await accountFetch("/api/auth/change-password", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) }));
    operation.current += 1; refreshFlight.current = null;
    apply(null); setError(null); announce();
  }, [apply, announce]);

  return <AuthContext.Provider value={{ user, loading, error, refresh, login, register, logout, changePassword, holdSessionRefresh }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
