export type AccountUser = {
  id: string;
  email: string;
  username: string;
  role: "ADMIN" | "USER";
  status: "PENDING" | "APPROVED" | "REJECTED" | "SUSPENDED";
  must_change_password: boolean;
  application_reason?: string | null;
  created_at?: string;
  last_login_at?: string | null;
};
export type AuthUser = AccountUser;
export type AccountIdentity = { userId: string | null; generation: number };
export type AccountRequestState = AccountIdentity & { csrfToken: string | null };

export const ACCOUNT_AUTH_REQUIRED_EVENT = "rallymate:auth-required";
export const ACCOUNT_IDENTITY_EVENT = "rallymate:identity-changed";
let state: AccountRequestState = { userId: null, csrfToken: null, generation: 0 };
const pending = new Set<AbortController>();

export function getAccountRequestState(): AccountRequestState { return { ...state }; }
export function captureAccountIdentity(): AccountIdentity { return { userId: state.userId, generation: state.generation }; }
export function isAccountIdentityCurrent(identity: AccountIdentity): boolean {
  return identity.userId === state.userId && identity.generation === state.generation;
}
export function invalidateAccountRequests(): void {
  state = { ...state, generation: state.generation + 1 };
  for (const controller of pending) controller.abort(new DOMException("账户状态已改变。", "AbortError"));
  pending.clear();
}
export function setAccountRequestState(next: { userId: string | null; csrfToken: string | null }): void {
  if (next.userId === state.userId && next.csrfToken === state.csrfToken) return;
  invalidateAccountRequests();
  state = { ...next, generation: state.generation };
  if (typeof window !== "undefined") window.dispatchEvent(new Event(ACCOUNT_IDENTITY_EVENT));
}

export class AccountError extends Error {
  constructor(message: string, public status = 0, public code = "request_failed") {
    super(message);
    this.name = "AccountError";
  }
}

export async function accountResponse<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => null) as { detail?: { message?: unknown; code?: unknown } } | null;
  if (!response.ok) {
    const detail = payload?.detail;
    throw new AccountError(typeof detail?.message === "string" ? detail.message : "请求未完成，请稍后重试。", response.status,
      typeof detail?.code === "string" ? detail.code : "request_failed");
  }
  if (payload === null) throw new AccountError("服务器返回了无法读取的内容。", response.status, "invalid_response");
  return payload as T;
}

// eslint-disable-next-line no-control-regex -- Reject literal and URL-decoded ASCII control characters in redirect paths.
const unsafePathCharacters = /[\\\u0000-\u001f\u007f]/;

export function safeNextPath(value: string | null | undefined, fallback: "/welcome" | "/?view=overview" = "/welcome"): string {
  if (!value || value.length > 2048 || !value.startsWith("/") || value.startsWith("//") || value.includes(" ") || unsafePathCharacters.test(value)) return fallback;
  try {
    const decoded = decodeURIComponent(value);
    if (decoded.startsWith("//") || unsafePathCharacters.test(decoded)) return fallback;
    const url = new URL(value, "https://rallymate.invalid");
    if (url.origin !== "https://rallymate.invalid" || /^\/(?:login|register|account-status|change-password)(?:\/|$)/.test(url.pathname) || url.pathname.startsWith("/api/")) return fallback;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch { return fallback; }
}

export function accountDestination(user: AccountUser, next?: string | null): string {
  if (user.must_change_password) return accountStepHref("/change-password", next);
  return user.status === "APPROVED" ? safeNextPath(next) : accountStepHref("/account-status", next);
}

/** Keep an explicit destination across required account steps without nesting login links. */
export function accountStepHref(path: "/login" | "/login?password-changed=1" | "/register" | "/account-status" | "/change-password", next?: string | null): string {
  const destination = safeNextPath(next);
  if (destination === "/welcome") return path;
  return `${path}${path.includes("?") ? "&" : "?"}next=${encodeURIComponent(destination)}`;
}

export function replaceAccountLocation(path: string): void {
  if (!path.startsWith("/") || path.startsWith("//") || unsafePathCharacters.test(path)) throw new AccountError("跳转地址无效。", 0, "invalid_destination");
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin) throw new AccountError("跳转地址无效。", 0, "invalid_destination");
  window.location.replace(`${url.pathname}${url.search}${url.hash}`);
}

function notifyAuth(status: number, code?: string): void {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(ACCOUNT_AUTH_REQUIRED_EVENT, { detail: { status, code } }));
}

export async function accountFetch(input: RequestInfo | URL, init: RequestInit = {}, fetchImpl: typeof fetch = fetch): Promise<Response> {
  const source = typeof Request !== "undefined" && input instanceof Request ? input : null;
  const origin = typeof window !== "undefined" ? window.location.origin : "https://rallymate.invalid";
  const url = new URL(source ? source.url : String(input), origin);
  if (typeof window !== "undefined" && url.origin !== origin) throw new AccountError("账户请求必须使用本站地址。", 0, "cross_origin_request");
  const headers = new Headers(init.headers ?? source?.headers);
  const method = (init.method ?? source?.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && state.csrfToken) headers.set("x-csrf-token", state.csrfToken);
  const identity = captureAccountIdentity();
  const controller = new AbortController();
  const upstreamSignal = init.signal ?? source?.signal;
  const abort = () => controller.abort(upstreamSignal?.reason);
  const release = () => { pending.delete(controller); upstreamSignal?.removeEventListener("abort", abort); };
  const check = () => {
    controller.signal.throwIfAborted();
    if (!isAccountIdentityCurrent(identity)) throw new DOMException("账户状态已改变。", "AbortError");
  };
  if (upstreamSignal?.aborted) abort();
  else upstreamSignal?.addEventListener("abort", abort, { once: true });
  pending.add(controller);
  try {
    check();
    const response = await fetchImpl(input, { ...init, method, headers, credentials: "same-origin", cache: "no-store", signal: controller.signal });
    check();
    if (response.status === 401 && !["/api/auth/me", "/api/auth/login", "/api/auth/register"].includes(url.pathname)) notifyAuth(401);
    if (response.status === 403 && url.pathname !== "/api/auth/me") {
      const errorPayload = await response.clone().json().catch(() => null) as { detail?: { code?: unknown }; error?: unknown } | null;
      const code = errorPayload?.detail?.code ?? errorPayload?.error;
      if (typeof code === "string" && (code.startsWith("account_") || ["password_change_required", "csrf_invalid"].includes(code))) notifyAuth(403, code);
    }
    check();
    if (!response.body) { release(); return response; }
    const reader = response.body.getReader();
    const cancel = () => { void reader.cancel(controller.signal.reason).catch(() => undefined); release(); };
    controller.signal.addEventListener("abort", cancel, { once: true });
    const finish = () => { controller.signal.removeEventListener("abort", cancel); release(); };
    const body = new ReadableStream<Uint8Array>({
      async pull(stream) {
        try {
          check();
          const item = await reader.read();
          check();
          if (item.done) { stream.close(); finish(); }
          else stream.enqueue(item.value);
        } catch (error) { stream.error(error); finish(); }
      },
      cancel(reason) { finish(); return reader.cancel(reason); },
    }, { highWaterMark: 0 });
    const wrapped = new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
    for (const name of ["url", "redirected", "type"] as const) Object.defineProperty(wrapped, name, { value: response[name] });
    return wrapped;
  } catch (error) { release(); throw error; }
}
