"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useAuth } from "../auth/AuthProvider";
import { AuthGuard } from "../auth/AuthGuard";
import { AccountFrame, AccountErrorMessage, accountStatusLabels, readableAccountError } from "../auth/AccountFrame";
import { AccountDialog } from "../auth/AccountDialog";
import { accountFetch, accountResponse, replaceAccountLocation, type AccountUser } from "../lib/auth-client";

type UserPage = { items: AccountUser[]; total: number; page: number; page_size: number };
type UserStats = { user_id: string; submitted_jobs: number; execution_attempts: number; deleted_at: string | null };
type Stats = { total: number; pending: number; users: UserStats[] };
type TemporaryPassword = { value: string; username: string; actorId: string; self: boolean };
type Confirmation = { action: "delete" | "reset"; user: AccountUser };

export default function AdminPage() {
  const auth = useAuth();
  const [temporary, setTemporary] = useState<TemporaryPassword | null>(null);
  const [finishingReset, setFinishingReset] = useState(false);
  const [copyState, setCopyState] = useState("");
  const visible = temporary && (auth.user?.id === temporary.actorId || (temporary.self && !auth.user)) ? temporary : null;
  if (temporary && auth.user && auth.user.id !== temporary.actorId) {
    setTemporary(null);
    setCopyState("");
  }
  useEffect(() => {
    const clear = () => { if (temporary) flushSync(() => { setTemporary(null); setCopyState(""); }); };
    const restored = (event: PageTransitionEvent) => { if (event.persisted) clear(); };
    window.addEventListener("pagehide", clear);
    window.addEventListener("pageshow", restored);
    return () => { window.removeEventListener("pagehide", clear); window.removeEventListener("pageshow", restored); };
  }, [temporary]);
  async function closeTemporary() {
    const self = temporary?.self;
    if (self) setFinishingReset(true);
    setTemporary(null); setCopyState("");
    if (self) {
      try { await auth.logout(); } catch { /* A self reset already revoked this session. */ }
      replaceAccountLocation("/login?reset=1");
    }
  }
  async function copyTemporary() {
    if (!visible) return;
    try { await navigator.clipboard.writeText(visible.value); setCopyState("已复制临时密码。"); }
    catch { setCopyState("请选中上方密码，手动复制。"); }
  }
  return <>
    {visible?.self || finishingReset ? <AccountFrame title="临时密码已生成" description="关闭此窗口后，使用临时密码重新登录。"><p className="account-notice">{finishingReset ? "正在前往登录页…" : "请先保存临时密码，再返回登录页。"}</p></AccountFrame> :
      <AuthGuard requireAdmin><AdminContent onTemporary={(value) => { setTemporary(value); setCopyState(""); }} /></AuthGuard>}
    {visible && <AccountDialog title={`${visible.username}的临时密码`} onClose={() => void closeTemporary()}>
      <p>这组临时密码只在当前窗口显示。请复制并妥善交付，用户登录后须设置新密码。</p>
      <output className="account-secret" aria-label="临时密码">{visible.value}</output>
      <p className="account-copy-state" role="status">{copyState || "关闭后将不再显示。"}</p>
      <div className="account-actions"><button className="account-button account-secondary" type="button" onClick={() => void copyTemporary()}>复制密码</button><button className="account-button account-primary" type="button" onClick={() => void closeTemporary()}>{visible.self ? "已保存，前往登录" : "关闭"}</button></div>
    </AccountDialog>}
  </>;
}

function AdminContent({ onTemporary }: { onTemporary: (value: TemporaryPassword) => void }) {
  const auth = useAuth();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<UserPage | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [confirmationError, setConfirmationError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const load = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    const ticket = ++sequence.current;
    setLoading(true); setError(null);
    const query = new URLSearchParams({ page: String(page), page_size: "20" });
    if (status) query.set("status", status);
    try {
      const [nextUsers, nextStats] = await Promise.all([
        accountFetch(`/api/admin/users?${query}`, { signal: controller.signal }).then((response) => accountResponse<UserPage>(response)),
        accountFetch("/api/admin/stats", { signal: controller.signal }).then((response) => accountResponse<Stats>(response)),
      ]);
      if (ticket === sequence.current) {
        if (!nextUsers.items.length && page > 1 && nextUsers.total <= (page - 1) * 20) { setPage(page - 1); return; }
        setResult(nextUsers); setStats(nextStats);
      }
    } catch (cause) {
      if (ticket === sequence.current && !(cause instanceof DOMException && cause.name === "AbortError")) setError(readableAccountError(cause));
    } finally { if (ticket === sequence.current) setLoading(false); }
  }, [page, status]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- A new filter/page starts a cancellable request with its own loading state.
    void load();
    return () => { sequence.current += 1; request.current?.abort(); };
  }, [load]);

  async function changeStatus(target: AccountUser, next: AccountUser["status"]) {
    setBusyId(target.id); setError(null);
    try {
      await accountResponse(await accountFetch(`/api/admin/users/${encodeURIComponent(target.id)}/status`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: next }) }));
      if (target.id === auth.user?.id && next !== "APPROVED") { await auth.refresh(); return; }
      await load();
    } catch (cause) { setError(readableAccountError(cause)); }
    finally { setBusyId(null); }
  }
  function confirm(action: Confirmation["action"], user: AccountUser) { setConfirmation({ action, user }); setConfirmationError(null); }
  async function confirmAction() {
    if (!confirmation || !auth.user) return;
    const { action, user } = confirmation;
    const actorId = auth.user.id;
    setBusyId(user.id); setConfirmationError(null);
    const releaseRefresh = action === "reset" && user.id === actorId ? auth.holdSessionRefresh() : () => undefined;
    try {
      if (action === "reset") {
        const payload = await accountResponse<{ user: AccountUser; temporary_password: string }>(await accountFetch(`/api/admin/users/${encodeURIComponent(user.id)}/reset-password`, { method: "POST" }));
        setConfirmation(null);
        onTemporary({ value: payload.temporary_password, username: user.username, actorId, self: user.id === actorId });
        // A self reset invalidates the cookie; keep its one-time display before any refresh.
        if (user.id !== actorId) await load();
      } else {
        await accountResponse(await accountFetch(`/api/admin/users/${encodeURIComponent(user.id)}`, { method: "DELETE" }));
        setConfirmation(null);
        if (user.id === actorId) { await auth.logout(); replaceAccountLocation("/login"); }
        else await load();
      }
    } catch (cause) { setConfirmationError(readableAccountError(cause)); }
    finally { releaseRefresh(); setBusyId(null); }
  }
  const counts = new Map(stats?.users.map((user) => [user.user_id, user]) ?? []);
  const submissions = stats?.users.reduce((sum, user) => sum + user.submitted_jobs, 0) ?? 0;
  const attempts = stats?.users.reduce((sum, user) => sum + user.execution_attempts, 0) ?? 0;
  const pages = Math.max(1, Math.ceil((result?.total ?? 0) / 20));

  return <AccountFrame title="账户与审核" wide>
    <dl className="account-stats">
      <div><dt>待审核</dt><dd>{stats?.pending ?? "—"}</dd></div>
      <div><dt>现有账户</dt><dd>{stats?.total ?? "—"}</dd></div>
      <div><dt>累计提交</dt><dd>{stats ? submissions : "—"}</dd></div>
      <div><dt>执行尝试</dt><dd>{stats ? attempts : "—"}</dd></div>
    </dl>
    <AccountErrorMessage message={error} />
    <div className="account-filter"><label htmlFor="admin-status">账户状态<select id="admin-status" value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}><option value="">全部状态</option>{Object.entries(accountStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><button className="account-button account-secondary" type="button" disabled={loading} onClick={() => void load()}>{loading ? "正在读取…" : "刷新列表"}</button></div>
    <div className="account-table-wrap" aria-busy={loading}><table className="account-table"><thead><tr><th scope="col">账户</th><th scope="col">角色 / 状态</th><th scope="col">申请理由</th><th scope="col">提交 / 执行尝试</th><th scope="col">操作</th></tr></thead><tbody>
      {result?.items.map((user) => <tr key={user.id}>
        <td><strong className="account-user-name">{user.username}{user.id === auth.user?.id ? "（你）" : ""}</strong><span className="account-user-email">{user.email}</span></td>
        <td><span>{user.role === "ADMIN" ? "管理员" : "球员"}</span><span className={`account-badge account-state-${user.status.toLowerCase()}`}>{accountStatusLabels[user.status]}</span>{user.must_change_password && <small className="account-user-email">下次登录须改密</small>}</td>
        <td><span className="account-mobile-label" aria-hidden="true">申请理由</span><p className="account-user-reason">{user.application_reason || "未填写"}</p></td>
        <td><span className="account-mobile-label" aria-hidden="true">提交 / 执行尝试</span>{counts.get(user.id)?.submitted_jobs ?? 0} / {counts.get(user.id)?.execution_attempts ?? 0}</td>
        <td><div className="account-row-actions">
          <button type="button" disabled={!!busyId || user.status === "APPROVED"} onClick={() => void changeStatus(user, "APPROVED")}>{user.status === "SUSPENDED" ? "恢复使用" : "通过"}</button>
          <button type="button" disabled={!!busyId || user.status === "REJECTED"} onClick={() => void changeStatus(user, "REJECTED")}>拒绝</button>
          <button type="button" disabled={!!busyId || user.status === "SUSPENDED"} onClick={() => void changeStatus(user, "SUSPENDED")}>停用</button>
          <button type="button" disabled={!!busyId} onClick={() => confirm("reset", user)}>重置密码</button>
          <button className="account-delete" type="button" disabled={!!busyId} onClick={() => confirm("delete", user)}>删除</button>
        </div></td>
      </tr>)}
    </tbody></table>{!loading && !result?.items.length && <div className="account-empty">当前筛选下没有账户。</div>}{loading && !result && <div className="account-empty" role="status">正在读取账户列表…</div>}</div>
    <p className="account-admin-note">执行尝试包含自动重试。已删除账户或记录的使用次数计入累计值。</p>
    <div className="account-pagination"><span>共 {result?.total ?? 0} 个账户 · 第 {page} / {pages} 页</span><button className="account-button account-secondary" type="button" disabled={loading || page <= 1} onClick={() => setPage(page - 1)}>上一页</button><button className="account-button account-secondary" type="button" disabled={loading || page >= pages} onClick={() => setPage(page + 1)}>下一页</button></div>
    {confirmation && <AccountDialog title={confirmation.action === "delete" ? "删除这个账户？" : "生成新的临时密码？"} onClose={() => { if (!busyId) { setConfirmation(null); setConfirmationError(null); } }}>
      <p>{confirmation.user.username} · {confirmation.user.email}</p>
      <p>{confirmation.action === "delete" ? "删除后，该用户不能再登录。历史任务归属和累计调用统计会保留。" : confirmation.user.id === auth.user?.id ? "此操作会撤销当前登录。请在下一步复制临时密码，再重新登录并设置新密码。" : "用户已有的登录会失效。生成的临时密码只显示一次，登录后须设置新密码。"}</p>
      <AccountErrorMessage message={confirmationError} />
      <div className="account-actions"><button className="account-button account-secondary" type="button" disabled={!!busyId} onClick={() => setConfirmation(null)}>取消</button><button className={`account-button ${confirmation.action === "delete" ? "account-danger" : "account-primary"}`} type="button" disabled={!!busyId} onClick={() => void confirmAction()}>{busyId ? "正在处理…" : confirmation.action === "delete" ? "确认删除" : "生成临时密码"}</button></div>
    </AccountDialog>}
  </AccountFrame>;
}
