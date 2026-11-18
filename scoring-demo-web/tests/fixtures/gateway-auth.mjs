export const USER_AUTHORIZATION = "Bearer gateway-user-jwt";

export function userRequest(url, options = {}) {
  const headers = new Headers(options.headers);
  if (!headers.has("authorization")) headers.set("authorization", USER_AUTHORIZATION);
  return new Request(url, { ...options, headers });
}
