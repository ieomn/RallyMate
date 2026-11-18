"use client";

import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { AccountFrame, AccountErrorMessage, readableAccountError } from "../auth/AccountFrame";
import { accountDestination, replaceAccountLocation } from "../lib/auth-client";

const messages = {
  PENDING: { title: "等待审核", description: "申请已提交。管理员通过申请后，即可进入训练空间。" },
  REJECTED: { title: "申请未通过", description: "如需了解原因或重新申请，请联系管理员。" },
  SUSPENDED: { title: "账户已停用", description: "如需恢复训练空间的访问权限，请联系管理员。" },
  APPROVED: { title: "审核已通过", description: "正在打开训练空间…" },
};
export default function AccountStatusPage() {
  const auth = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (auth.loading || (!auth.user && auth.error)) return;
    if (!auth.user) replaceAccountLocation("/login");
    else if (auth.user.must_change_password || auth.user.status === "APPROVED") replaceAccountLocation(accountDestination(auth.user));
  }, [auth.user, auth.loading, auth.error]);
  async function refresh() { setBusy(true); setError(null); try { await auth.refresh(); } finally { setBusy(false); } }
  async function logout() { try { await auth.logout(); replaceAccountLocation("/login"); } catch (cause) { setError(readableAccountError(cause)); } }
  const message = auth.user ? messages[auth.user.status] : null;
  return <AccountFrame title={message?.title ?? (auth.loading ? "正在读取审核状态" : "账户状态")} description={message?.description}>
    {auth.user && <div className="account-status-card"><h3>{auth.user.username}</h3><p>{auth.user.email}</p>{auth.user.application_reason && <div className="account-reason"><span>申请理由</span><p>{auth.user.application_reason}</p></div>}</div>}
    <AccountErrorMessage message={error ?? auth.error} />
    <div className="account-actions"><button className="account-button account-primary" type="button" disabled={busy || auth.loading} onClick={() => void refresh()}>{busy ? "正在刷新…" : "刷新审核状态"}</button><button className="account-button account-secondary" type="button" onClick={() => void logout()}>退出登录</button></div>
  </AccountFrame>;
}
