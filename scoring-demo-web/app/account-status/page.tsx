"use client";

import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { AccountFrame, AccountErrorMessage, readableAccountError } from "../auth/AccountFrame";
import { accountDestination, replaceAccountLocation } from "../lib/auth-client";

const messages = {
  PENDING: { title: "申请已提交，等待审核", description: "管理员审核通过后，你就可以上传视频并建立自己的训练记录。", label: "审核中", symbol: "◷" },
  REJECTED: { title: "本次注册申请未通过", description: "如需了解详情或重新申请，请联系管理员。", label: "未通过", symbol: "—" },
  SUSPENDED: { title: "账户已停用", description: "当前暂时无法进入训练空间。如需恢复使用，请联系管理员。", label: "已停用", symbol: "Ⅱ" },
  APPROVED: { title: "账户已通过审核", description: "你的训练空间已经准备好了。", label: "已通过", symbol: "✓" },
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
  const message = messages[auth.user?.status ?? "PENDING"];
  return <AccountFrame title={auth.loading ? "正在读取审核状态" : message.title} description={auth.loading ? undefined : message.description}>
    {auth.user && <div className="account-status-card"><div className={`account-status-symbol account-state-${auth.user.status.toLowerCase()}`} aria-hidden="true">{message.symbol}</div><span className={`account-badge account-state-${auth.user.status.toLowerCase()}`}>{message.label}</span><h3>{auth.user.username}</h3><p>{auth.user.email}</p>{auth.user.application_reason && <div className="account-reason"><span>你的申请理由</span><p>{auth.user.application_reason}</p></div>}</div>}
    <AccountErrorMessage message={error ?? auth.error} />
    <div className="account-actions"><button className="account-button account-primary" type="button" disabled={busy || auth.loading} onClick={() => void refresh()}>{busy ? "正在刷新…" : "刷新审核状态"}</button><button className="account-button account-secondary" type="button" onClick={() => void logout()}>退出登录</button></div>
  </AccountFrame>;
}
