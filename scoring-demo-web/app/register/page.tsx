"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthProvider";
import { AccountFrame, AccountErrorMessage, passwordIsStrong, readableAccountError } from "../auth/AccountFrame";
import { accountDestination, replaceAccountLocation } from "../lib/auth-client";

export default function RegisterPage() {
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (!auth.loading && auth.user) replaceAccountLocation(accountDestination(auth.user)); }, [auth.user, auth.loading]);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(null);
    if (!passwordIsStrong(password)) { setError("密码须为12至128个字符，并包含字母和数字。"); return; }
    if (!username.trim()) { setError("请输入显示名称。"); return; }
    setBusy(true);
    try {
      await auth.register({ email: email.trim(), username: username.trim(), password, application_reason: reason.trim() || null });
      setCreated(true);
      const user = await auth.login(email.trim(), password);
      replaceAccountLocation(accountDestination(user));
    } catch (cause) { setError(readableAccountError(cause)); }
    finally { setBusy(false); setPassword(""); }
  }
  return <AccountFrame title="开始记录你的练习" description="提交注册申请后，管理员会审核你的账户。">
    {created ? <div className="account-status-card"><h3>注册申请已提交</h3><p>账户已创建，请登录查看审核进度。</p><AccountErrorMessage message={error} /><a className="account-button account-primary" href="/login">前往登录</a></div> : <form className="account-form" onSubmit={submit}>
      <label htmlFor="register-email">邮箱<input disabled={auth.loading || busy} id="register-email" name="email" type="email" autoComplete="username" maxLength={254} required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
      <label htmlFor="register-username">显示名称<input disabled={auth.loading || busy} id="register-username" name="username" autoComplete="nickname" maxLength={80} required value={username} onChange={(event) => setUsername(event.target.value)} /></label>
      <label htmlFor="register-password">密码<input disabled={auth.loading || busy} id="register-password" name="password" type="password" autoComplete="new-password" minLength={12} maxLength={128} required aria-describedby="password-rule" value={password} onChange={(event) => setPassword(event.target.value)} /><small id="password-rule">12至128个字符，包含字母和数字。</small></label>
      <label htmlFor="register-reason">申请理由<span className="account-optional">选填</span><textarea disabled={auth.loading || busy} id="register-reason" name="application_reason" maxLength={2000} rows={3} placeholder="例如：记录每周的正手训练，复盘动作与击球表现。" value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <AccountErrorMessage message={error ?? auth.error} />
      <button className="account-button account-primary" type="submit" disabled={busy || auth.loading}>{busy ? "正在提交…" : "提交注册申请"}<span aria-hidden="true">↗</span></button>
    </form>}
    <p className="account-form-footer">已有账户？<a href="/login">登录</a></p>
  </AccountFrame>;
}
