import type { AvailableFile, AuditVerify, Dataset, Info, IngestRequest, IngestResult, RegisterResponse, Run, TokenResponse } from "./types";

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";
const HTTP_UNAUTHORIZED = 401;
const HTTP_TOO_MANY = 429;

export class ApiError extends Error {
  readonly status: number;
  /** Seconds from Retry-After on 429, if present. */
  readonly retryAfter: number | null;
  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

let accessToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setToken(token: string | null): void {
  accessToken = token;
}

/** Called when an authenticated request comes back 401 (expired or revoked token). */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

/** FastAPI returns detail as a string or as a list of {loc,msg}; flatten both. */
function detailText(body: unknown, fallback: string): string {
  if (typeof body !== "object" || body === null || !("detail" in body)) return fallback;
  const d = body.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((e: unknown) => {
        if (typeof e === "object" && e !== null && "msg" in e && typeof e.msg === "string") {
          const loc = "loc" in e && Array.isArray(e.loc) ? e.loc.filter((x) => x !== "body").join(".") : "";
          return loc === "" ? e.msg : `${loc}: ${e.msg}`;
        }
        return JSON.stringify(e);
      })
      .join("; ");
  }
  return JSON.stringify(d);
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
  const hadToken = accessToken !== null;
  if (accessToken !== null) headers.set("Authorization", `Bearer ${accessToken}`);
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch (err) {
      if (!(err instanceof SyntaxError)) throw err; // non-JSON error body: keep statusText
    }
    const retry = Number(res.headers.get("Retry-After"));
    const apiErr = new ApiError(res.status, detailText(body, res.statusText), Number.isFinite(retry) && retry > 0 ? retry : null);
    if (res.status === HTTP_UNAUTHORIZED && hadToken) onUnauthorized?.();
    throw apiErr;
  }
  return (await res.json()) as T;
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  info: () => request<Info>("/info"),
  datasets: () => request<Dataset[]>("/datasets"),
  availableFiles: () => request<AvailableFile[]>("/datasets/available"),
  ingest: (body: IngestRequest) => request<IngestResult>("/datasets/ingest", post(body)),
  runs: (limit: number, offset = 0) => request<Run[]>(`/runs?limit=${limit}&offset=${offset}`),
  comparison: () => request<Run[]>("/runs/comparison"),
  auditVerify: () => request<AuditVerify>("/audit/verify"),
  login: (email: string, password: string) => request<TokenResponse>("/auth/login", post({ email, password })),
  register: (email: string, password: string) => request<RegisterResponse>("/auth/register", post({ email, password })),
};

const KNOWN_DETAIL: Record<string, string> = {
  "invalid credentials": "이메일 또는 비밀번호가 올바르지 않습니다.",
  "file not found in data cache": "데이터 폴더에서 해당 파일을 찾을 수 없습니다.",
  "dataset name exists with different content": "같은 이름의 데이터셋이 이미 있고 내용이 다릅니다. 다른 이름을 사용해 주세요.",
  "dataset conflict": "데이터셋이 충돌했습니다. 이름을 바꿔 다시 시도해 주세요.",
  "file too large": "파일이 너무 큽니다.",
  "CSV must be UTF-8": "CSV 파일이 UTF-8 인코딩이 아닙니다.",
  "CSV is empty": "CSV 파일이 비어 있습니다.",
  "dataset exists with a different column mapping; use a new dataset name to re-ingest":
    "같은 이름의 데이터셋이 다른 열 지정으로 이미 있습니다. 다시 수집하려면 새 이름을 사용해 주세요.",
  "dataset conflict or ingest already in progress": "데이터셋이 충돌했거나 같은 수집이 이미 진행 중입니다. 잠시 후 목록을 새로고침해 주세요.",
  "file changed while it was being ingested": "수집하는 동안 파일이 바뀌었습니다. 파일이 안정된 뒤 다시 시도해 주세요.",
  "internal server error": "서버에서 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
};

/** Turn any thrown value into one Korean sentence a user can act on. */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    const known = KNOWN_DETAIL[err.message];
    if (known !== undefined) return known;
    if (err.status === HTTP_TOO_MANY) {
      return err.retryAfter === null
        ? "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
        : `요청이 너무 많습니다. ${Math.ceil(err.retryAfter)}초 후 다시 시도해 주세요.`;
    }
    if (err.status === HTTP_UNAUTHORIZED) return "로그인이 필요하거나 세션이 만료되었습니다.";
    if (err.status === 403) return "이 계정에는 데이터 수집 권한이 없습니다. 관리자에게 권한을 요청해 주세요.";
    const row = /^row (\d+) \(line (\d+)\): (.*)$/.exec(err.message);
    if (row !== null) return `${row[1]}번째 행(파일 ${row[2]}번째 줄)에 문제가 있습니다: ${row[3]}`;
    if (err.status === 413) return `파일이 허용 크기를 넘습니다. (${err.message})`;
    if (err.status === 422) return `입력값을 확인해 주세요. (${err.message})`;
    if (err.status >= 500) return "서버에서 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.";
    return err.message;
  }
  if (err instanceof TypeError) return "서버에 연결할 수 없습니다. 네트워크와 서버 상태를 확인해 주세요.";
  return err instanceof Error ? err.message : String(err);
}
