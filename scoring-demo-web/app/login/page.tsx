"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "../auth/AuthProvider";
import { AccountFrame, AccountErrorMessage, readableAccountError } from "../auth/AccountFrame";
import { accountDestination, accountStepHref, replaceAccountLocation } from "../lib/auth-client";

export default function LoginPage() {
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const params = useSearchParams();
  const next = params?.get("next");
  const notice = params?.has("reset") ? "请使用刚才保存的临时密码登录，并设置新密码。"
    : params?.has("password-changed") ? "密码已更新，请使用新密码登录。" : null;
  useEffect(() => {
    if (!auth.loading && auth.user) replaceAccountLocation(accountDestination(auth.user, next));
  }, [auth.user, auth.loading, next]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (auth.loading || auth.user || busy) return;
    setBusy(true); setError(null);
    try {
      await auth.login(email.trim(), password);
      setPassword("");
    } catch (cause) { setError(readableAccountError(cause)); }
    finally { setBusy(false); }
  }
  return <AccountFrame entry title="登录">
    {notice && <p className="account-notice" role="status">{notice}</p>}
    <form className="account-form" onSubmit={submit}>
      <label htmlFor="login-email">邮箱<input disabled={auth.loading || busy} id="login-email" name="email" type="email" autoComplete="username" inputMode="email" autoCapitalize="none" spellCheck={false} maxLength={254} required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
      <label htmlFor="login-password">密码<input disabled={auth.loading || busy} id="login-password" name="password" type="password" autoComplete="current-password" maxLength={128} required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
      <AccountErrorMessage message={error ?? auth.error} />
      <button className="account-button account-primary" type="submit" disabled={busy || auth.loading || !!auth.user}>{busy ? "正在登录…" : "登录"}</button>
    </form>
    <p className="account-form-footer">还没有账号？<a href={accountStepHref("/register", next)}>注册</a></p>
    <p className="account-hint">忘记密码时，请联系管理员重置。</p>
  </AccountFrame>;
}
