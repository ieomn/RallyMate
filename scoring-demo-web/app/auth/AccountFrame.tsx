import type { ReactNode } from "react";

export function AccountFrame({ title, description, children, wide = false }: { title: string; description?: string; children: ReactNode; wide?: boolean }) {
  return <main className={`account-app${wide ? " account-app-wide" : ""}`}>
    <header className="account-header"><a className="account-brand" href="/welcome"><span aria-hidden="true">◉</span> RallyMate</a><a href="/welcome">了解训练空间 <span aria-hidden="true">↗</span></a></header>
    <div className="account-layout">
      {!wide && <aside className="account-intro"><span className="account-eyebrow">YOUR NEXT GOOD SHOT</span><h1>每一次练习，<br />都有新的方向。</h1><p>留下自己的训练记录，回看关键动作，<br />把这一次的发现带到下一次上场。</p><div className="account-court" aria-hidden="true"><i /><b /><span>RALLY / REFLECT / REPEAT</span></div><span className="account-intro-note">属于你的训练，也属于你的进步。</span></aside>}
      <section className="account-panel" aria-labelledby="account-heading"><div className="account-heading"><span className="account-eyebrow">RALLYMATE ACCOUNT</span><h2 id="account-heading">{title}</h2>{description && <p>{description}</p>}</div>{children}</section>
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
