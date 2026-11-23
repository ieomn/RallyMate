"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "../auth/AuthProvider";
import { AccountFrame, AccountErrorMessage, readableAccountError } from "../auth/AccountFrame";
import { accountDestination, accountStepHref, replaceAccountLocation } from "../lib/auth-client";

const messages = {
  PENDING: { title: "账号待审核", description: "这个账号仍在原审核队列中。请联系管理员开通训练空间。" },
  REJECTED: { title: "账号未获开通", description: "请联系管理员了解原因。" },
  SUSPENDED: { title: "账户已停用", description: "如需恢复训练空间的访问权限，请联系管理员。" },
  APPROVED: { title: "账号已开通", description: "正在进入训练空间…" },
};
export default function AccountStatusPage() {
  const auth = useAuth();
  const next = useSearchParams()?.get("next");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (auth.loading || (!auth.user && auth.error)) return;
    if (!auth.user) replaceAccountLocation(accountStepHref("/login", next));
    else if (auth.user.must_change_password || auth.user.status === "APPROVED") replaceAccountLocation(accountDestination(auth.user, next));
  }, [auth.user, auth.loading, auth.error, next]);
  async function refresh() { setBusy(true); setError(null); try { await auth.refresh(); } finally { setBusy(false); } }
  async function logout() { try { await auth.logout(); replaceAccountLocation("/login"); } catch (cause) { setError(readableAccountError(cause)); } }
  const message = auth.user ? messages[auth.user.status] : null;
  return <AccountFrame title={message?.title ?? (auth.loading ? "正在读取账号状态" : "账号状态")} description={message?.description}>
    {auth.user && <div className="account-status-card"><h3>{auth.user.username}</h3><p>{auth.user.email}</p>{auth.user.application_reason && <div className="account-reason"><span>注册时的备注</span><p>{auth.user.application_reason}</p></div>}</div>}
    <AccountErrorMessage message={error ?? auth.error} />
    <div className="account-actions"><button className="account-button account-primary" type="button" disabled={busy || auth.loading} onClick={() => void refresh()}>{busy ? "正在刷新…" : "刷新状态"}</button><button className="account-button account-secondary" type="button" onClick={() => void logout()}>退出登录</button></div>
  </AccountFrame>;
}
