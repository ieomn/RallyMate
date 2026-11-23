"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "../auth/AuthProvider";
import { AccountFrame, AccountErrorMessage, passwordIsStrong, readableAccountError } from "../auth/AccountFrame";
import { AccountError, accountDestination, accountStepHref, replaceAccountLocation, safeNextPath } from "../lib/auth-client";

export default function RegisterPage() {
  const auth = useAuth();
  const next = useSearchParams()?.get("next");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const submitting = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const disabled = auth.loading || busy || !!auth.user;
  useEffect(() => {
    if (!auth.loading && auth.user) replaceAccountLocation(accountDestination(auth.user, safeNextPath(next, "/?view=overview")));
  }, [auth.user, auth.loading, next]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (disabled || submitting.current) return;
    setError(null);
    if (!username.trim()) { setError("请输入昵称。"); return; }
    if (!passwordIsStrong(password)) { setError("密码需为 12–128 个字符，同时包含字母和数字。"); return; }
    submitting.current = true;
    setBusy(true);
    try {
      await auth.register({ email: email.trim(), username: username.trim(), password });
      setPassword("");
    } catch (cause) {
      const uncertain = cause instanceof TypeError || (cause instanceof AccountError && cause.code === "invalid_response");
      if (uncertain) {
        if (!await auth.refresh()) setError("未收到注册结果。请检查网络，也可以用刚才的邮箱和密码登录。");
      } else { setError(readableAccountError(cause)); }
    } finally { submitting.current = false; setBusy(false); }
  }
  return <AccountFrame entry title="注册">
    <form className="account-form" onSubmit={submit}>
      <label htmlFor="register-username">昵称<input disabled={disabled} id="register-username" name="username" autoComplete="nickname" maxLength={80} required value={username} onChange={(event) => setUsername(event.target.value)} /></label>
      <label htmlFor="register-email">邮箱<input disabled={disabled} id="register-email" name="email" type="email" autoComplete="username" inputMode="email" autoCapitalize="none" spellCheck={false} maxLength={254} required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
      <label htmlFor="register-password">密码<input disabled={disabled} id="register-password" name="password" type="password" autoComplete="new-password" minLength={12} maxLength={128} required aria-describedby="password-rule" value={password} onChange={(event) => setPassword(event.target.value)} /><small id="password-rule">12–128 个字符，同时包含字母和数字。</small></label>
      <AccountErrorMessage message={error ?? auth.error} />
      <button className="account-button account-primary" type="submit" disabled={disabled}>{busy ? "正在注册…" : "注册"}</button>
    </form>
    <p className="account-form-footer">已有账号？<a href={accountStepHref("/login", next)}>登录</a></p>
  </AccountFrame>;
}
