"use client";

import { Fragment, useEffect, type ReactNode } from "react";
import { accountDestination, accountStepHref, replaceAccountLocation } from "../lib/auth-client";
import { workspaceHref } from "../lib/workspace-navigation";
import { useAuth } from "./AuthProvider";

export function AuthGuard({ children, requireAdmin = false }: { children: ReactNode; requireAdmin?: boolean }) {
  const { user, loading, error, refresh } = useAuth();
  useEffect(() => {
    if (loading || (error && !user)) return;
    const next = `${window.location.pathname}${window.location.search}`;
    if (!user) replaceAccountLocation(accountStepHref("/login", next));
    else if (user.must_change_password || user.status !== "APPROVED") replaceAccountLocation(accountDestination(user, next));
  }, [user, loading, error]);
  if (loading) return <div className="account-gate" role="status">正在读取账户状态…</div>;
  if (!user && error) return <div className="account-gate" role="alert"><p>{error}</p><button type="button" onClick={() => void refresh()}>重试</button></div>;
  if (!user || user.must_change_password || user.status !== "APPROVED") return <div className="account-gate" role="status">正在前往账户页面…</div>;
  if (requireAdmin && user.role !== "ADMIN") return <div className="account-gate"><h1>此页面仅向管理员开放</h1><a href={workspaceHref("overview")}>返回训练空间</a></div>;
  return <Fragment key={user.id}>{children}</Fragment>;
}

export default AuthGuard;
