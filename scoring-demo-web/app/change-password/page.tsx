"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "../auth/AuthProvider";
import { AccountFrame, AccountErrorMessage, passwordIsStrong, readableAccountError } from "../auth/AccountFrame";
import { accountStepHref, replaceAccountLocation } from "../lib/auth-client";

export default function ChangePasswordPage() {
  const auth = useAuth();
  const next = useSearchParams()?.get("next");
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (!auth.loading && !auth.user && !auth.error && !busy && !completed) replaceAccountLocation(accountStepHref("/login", next)); }, [auth.loading, auth.user, auth.error, busy, completed, next]);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(null);
    if (!passwordIsStrong(password)) { setError("新密码须为12至128个字符，并包含字母和数字。"); return; }
    if (password !== confirmation) { setError("两次输入的新密码不一致。"); return; }
    setBusy(true);
    try { await auth.changePassword(current, password); setCompleted(true); setCurrent(""); setPassword(""); setConfirmation(""); replaceAccountLocation(accountStepHref("/login?password-changed=1", next)); }
    catch (cause) { setError(readableAccountError(cause)); }
    finally { setBusy(false); }
  }
  return <AccountFrame title={auth.user?.must_change_password ? "设置新密码" : "修改密码"} description={auth.user?.must_change_password ? "输入管理员提供的临时密码，再设置新密码。" : "更新后，请在使用中的设备上重新登录。"}>
    <form className="account-form" onSubmit={submit}>
      <label htmlFor="current-password">{auth.user?.must_change_password ? "临时密码" : "当前密码"}<input disabled={auth.loading || busy} id="current-password" type="password" autoComplete="current-password" required maxLength={128} value={current} onChange={(event) => setCurrent(event.target.value)} /></label>
      <label htmlFor="new-password">新密码<input disabled={auth.loading || busy} id="new-password" type="password" autoComplete="new-password" aria-describedby="new-password-rule" required minLength={12} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} /><small id="new-password-rule">12 至 128 个字符，包含字母和数字。</small></label>
      <label htmlFor="confirm-password">再次输入新密码<input disabled={auth.loading || busy} id="confirm-password" type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>
      <AccountErrorMessage message={error ?? auth.error} />
      <button className="account-button account-primary" type="submit" disabled={busy || auth.loading || !auth.user}>{busy ? "正在更新…" : "保存新密码并重新登录"}</button>
    </form>
    {auth.user && !auth.user.must_change_password && <a className="account-text-button" href={auth.user.status === "APPROVED" ? "/?view=overview" : "/account-status"}>返回{auth.user.status === "APPROVED" ? "训练空间" : "账户状态"} ↗</a>}
  </AccountFrame>;
}
