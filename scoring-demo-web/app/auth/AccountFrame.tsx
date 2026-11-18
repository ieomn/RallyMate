import type { ReactNode } from "react";
import Image from "next/image";
import type { AccountUser } from "../lib/auth-client";

export const accountStatusLabels: Record<AccountUser["status"], string> = {
  APPROVED: "已通过", PENDING: "待审核", REJECTED: "未通过", SUSPENDED: "已停用",
};

export function AccountFrame({ title, description, children, wide = false }: { title: string; description?: string; children: ReactNode; wide?: boolean }) {
  return <main className={`account-app${wide ? " account-app-wide" : ""}`}>
    <header className="account-header">
      <a className="account-brand" href="/welcome"><Image src="/tennis-mark.svg" alt="" width={30} height={30} unoptimized />RallyMate</a>
      <a href={wide ? "/?view=overview" : "/welcome"}>{wide ? "返回训练空间" : "返回首页"}</a>
    </header>
    <div className="account-layout">
      <section className="account-panel" aria-labelledby="account-heading">
        <div className="account-heading"><h1 id="account-heading">{title}</h1>{description && <p>{description}</p>}</div>
        {children}
      </section>
      {!wide && <div className="account-court-print" aria-hidden="true"><i /><b /></div>}
    </div>
  </main>;
}

export function AccountErrorMessage({ message }: { message: string | null }) {
  return message ? <p className="account-error" role="alert">{message}</p> : null;
}

export function passwordIsStrong(value: string): boolean {
  return value.length >= 12 && value.length <= 128 && /\p{L}/u.test(value) && /\p{N}/u.test(value);
}

export function readableAccountError(error: unknown): string {
  if (error instanceof Error && error.name === "AbortError") return "账户状态已改变，请重新操作。";
  return error instanceof Error ? error.message : "请求未完成，请稍后重试。";
}
