"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthProvider";
import { AccountFrame, AccountErrorMessage, readableAccountError } from "../auth/AccountFrame";
import { accountDestination, replaceAccountLocation, safeNextPath } from "../lib/auth-client";

export default function LoginPage() {
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has("password-changed")) setNotice("密码已更新，请使用新密码登录。");
    if (params.has("reset")) setNotice("请使用刚才保存的临时密码登录，并设置新密码。");
    if (!auth.loading && auth.user) replaceAccountLocation(accountDestination(auth.user, params.get("next")));
  }, [auth.user, auth.loading]);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      const user = await auth.login(email.trim(), password);
      setPassword("");
      replaceAccountLocation(accountDestination(user, safeNextPath(new URLSearchParams(window.location.search).get("next"))));
    } catch (cause) { setError(readableAccountError(cause)); }
    finally { setBusy(false); }
  }
  return <AccountFrame title="欢迎回来" description="登录后，继续查看你的训练记录与动作分析。">
    {notice && <p className="account-notice" role="status">{notice}</p>}
    <form className="account-form" onSubmit={submit}>
      <label htmlFor="login-email">邮箱<input disabled={auth.loading || busy} id="login-email" name="email" type="email" autoComplete="username" inputMode="email" maxLength={254} required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
      <label htmlFor="login-password">密码<input disabled={auth.loading || busy} id="login-password" name="password" type="password" autoComplete="current-password" maxLength={128} required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
      <AccountErrorMessage message={error ?? auth.error} />
      <button className="account-button account-primary" type="submit" disabled={busy || auth.loading}>{busy ? "正在登录…" : "登录训练空间"}<span aria-hidden="true">↗</span></button>
    </form>
    <p className="account-form-footer">还没有账户？<a href="/register">申请注册</a></p>
    <p className="account-hint">忘记密码时，请联系管理员重置。</p>
  </AccountFrame>;
}
