"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";

export function AccountDialog({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  return <dialog ref={dialog} className="account-dialog" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <div className="account-dialog-heading"><h2 id={titleId}>{title}</h2><button type="button" className="account-icon-button" aria-label="关闭对话框" onClick={onClose}>×</button></div>
    {children}
  </dialog>;
}
