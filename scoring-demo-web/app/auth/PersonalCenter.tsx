"use client";

import { usePathname } from "next/navigation";
import Image from "next/image";
import { useId, useRef, useState } from "react";
import { useAuth } from "./AuthProvider";
import { AccountErrorMessage, accountStatusLabels, readableAccountError } from "./AccountFrame";
import { replaceAccountLocation, type AccountUser } from "../lib/auth-client";
import { workspaceHref } from "../lib/workspace-navigation";
import { playPressFeedback } from "../lib/press-feedback";

export default function PersonalCenter() {
  const path = usePathname();
  const { user, logout } = useAuth();
  const pathname = path?.replace(/\/$/, "") || "/";
  if (!user || !path || !["/", "/welcome", "/annotate", "/admin", "/change-password", "/account-status"].includes(pathname)) return null;
  return <ProfileDock key={user.id} user={user} logout={logout} pathname={pathname} />;
}

function ProfileDock({ user, logout, pathname }: { user: AccountUser; logout: () => Promise<void>; pathname: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const dialogId = useId();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canTrain = user.status === "APPROVED" && !user.must_change_password;

  function showProfile() {
    setError(null);
    dialog.current?.showModal();
    setOpen(true);
  }

  async function signOut() {
    setBusy(true);
    setError(null);
    try {
      await logout();
      replaceAccountLocation("/login");
    } catch (cause) {
      setError(readableAccountError(cause));
      setBusy(false);
    }
  }

  return <div className={`profile-dock${pathname === "/" ? " profile-dock-workspace" : pathname === "/welcome" ? " profile-dock-public" : ""}`} onClickCapture={playPressFeedback}>
    <button ref={trigger} className="profile-trigger" type="button" aria-haspopup="dialog" aria-controls={dialogId}
      aria-expanded={open} aria-label={`打开个人中心，${user.username}`} onClick={showProfile}>
      <Image src="/player-portrait.jpg" width={40} height={40} alt="" unoptimized />
      <span className="profile-trigger-text"><strong>{user.username}</strong><small>个人中心</small></span>
    </button>
    <dialog ref={dialog} id={dialogId} aria-labelledby={titleId} className="profile-sheet"
      onClose={() => { setOpen(false); trigger.current?.focus({ preventScroll: true }); }}
      onPointerDown={event => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.current?.close();
      }}>
      <div className="profile-sheet-header">
        <h2 id={titleId}>个人中心</h2>
        <button className="account-icon-button" aria-label="关闭个人中心" type="button" onClick={() => dialog.current?.close()}>×</button>
      </div>
      <div className="profile-identity">
        <Image src="/player-portrait.jpg" alt="" width={72} height={72} unoptimized />
        <div><h3>{user.username}</h3><p>{user.email}</p></div>
      </div>
      <dl className="profile-facts">
        <div><dt>身份</dt><dd>{user.role === "ADMIN" ? "管理员" : "球员"}</dd></div>
        <div><dt>账户状态</dt><dd className={`account-state-${user.status.toLowerCase()}`}>{accountStatusLabels[user.status]}</dd></div>
      </dl>
      <nav className="profile-actions" aria-label="个人中心操作">
        {canTrain && <a href={workspaceHref("sessions")}>我的训练记录</a>}
        {canTrain && user.role === "ADMIN" && <a href="/admin">账户与审核</a>}
        <a href="/change-password">{user.must_change_password ? "设置新密码" : "修改密码"}</a>
      </nav>
      <AccountErrorMessage message={error} />
      <button className="profile-signout" type="button" disabled={busy} onClick={() => void signOut()}>{busy ? "正在退出…" : "退出登录"}</button>
    </dialog>
  </div>;
}
