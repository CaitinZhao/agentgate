/** Auth/session state + the REST client shared by every view. */
import { reactive } from "vue";

// re-export so views can pull every helper from one module
export { setLang } from "./i18n";

export interface SessionUser {
  username: string;
  role: string;
  display_name: string;
}

export const store = reactive({
  token: localStorage.getItem("ag_token") || "",
  user: JSON.parse(localStorage.getItem("ag_user") || "null") as SessionUser | null,
});

export function setAuth(token: string, user: SessionUser) {
  store.token = token;
  store.user = user;
  localStorage.setItem("ag_token", token);
  localStorage.setItem("ag_user", JSON.stringify(user));
}

export function clearAuth() {
  store.token = "";
  store.user = null;
  localStorage.removeItem("ag_token");
  localStorage.removeItem("ag_user");
}

export const ROLE_RANK: Record<string, number> = { viewer: 0, member: 1, admin: 2, owner: 3 };

export function hasMin(role: string | undefined, min: string): boolean {
  return (ROLE_RANK[role || ""] ?? -1) >= (ROLE_RANK[min] ?? 99);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** REST client: JSON or FormData body; 401 clears the session and bounces to login. */
export async function api(method: string, path: string, body?: any): Promise<any> {
  const headers: Record<string, string> = {};
  if (store.token) headers["Authorization"] = "Bearer " + store.token;
  let payload: any;
  if (body !== undefined) {
    if (body instanceof FormData) {
      payload = body;
    } else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
  }
  const resp = await fetch("/api/v1" + path, { method, headers, body: payload });
  if (resp.status === 401) {
    clearAuth();
    if (!location.hash.startsWith("#/login")) location.hash = "#/login";
    throw new ApiError(401, "unauthorized");
  }
  if (!resp.ok) {
    let msg = resp.statusText;
    try {
      const j = await resp.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(resp.status, msg);
  }
  return resp.json();
}

/** Authenticated download (report/artifact/template/marked-excel) via fetch + blob. */
export async function download(path: string, fallbackName: string) {
  const resp = await fetch("/api/v1" + path, {
    headers: { Authorization: "Bearer " + store.token },
  });
  if (!resp.ok) throw new ApiError(resp.status, "download failed");
  const disp = resp.headers.get("content-disposition") || "";
  const m = disp.match(/filename="?([^";]+)"?/);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = m ? decodeURIComponent(m[1]) : fallbackName;
  a.click();
  URL.revokeObjectURL(url);
}

/** Plain-text fetch used for report/analysis markdown previews. */
export async function fetchText(path: string): Promise<string> {
  const resp = await fetch("/api/v1" + path, {
    headers: { Authorization: "Bearer " + store.token },
  });
  if (!resp.ok) throw new ApiError(resp.status, resp.statusText);
  return resp.text();
}

/** 2026-09-22T20:10:36 -> 2026-09-22 20:10:36 (display only). */
export function fmtTime(s?: string | null): string {
  return (s || "").replace("T", " ") || "-";
}

/** New-tab guide link (the /view endpoint renders markdown and authenticates via cookie). */
export function guideUrl(guide: string): string {
  const lang = localStorage.getItem("ag_lang")
    || (navigator.language.startsWith("zh") ? "zh" : "en");
  return `/api/v1/guides/${guide}/view?lang=${lang}`;
}
