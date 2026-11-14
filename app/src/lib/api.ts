import { cloudBase, edgeBase, type Settings } from "./config";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(url: string, init: RequestInit, headers: Record<string, string>): Promise<T> {
  let r: Response;
  try {
    r = await fetch(url, { ...init, headers: { "content-type": "application/json", ...headers, ...(init.headers as object) } });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection, or the server address in Settings.");
  }
  const text = await r.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!r.ok) {
    const detail = (body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : body) ?? r.statusText;
    throw new ApiError(r.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body as T;
}

export const edge = <T,>(s: Settings, path: string, init: RequestInit = {}) =>
  request<T>(`${edgeBase(s)}${path}`, init, s.guardianKey ? { "X-Guardian-Key": s.guardianKey } : {});

export const cloud = <T,>(s: Settings, path: string, init: RequestInit = {}) =>
  request<T>(
    `${cloudBase(s)}${path}`,
    init,
    s.cloudUrl ? (s.cloudKey ? { "X-Cloud-Key": s.cloudKey } : {}) : s.guardianKey ? { "X-Guardian-Key": s.guardianKey } : {},
  );

export const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const money = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
