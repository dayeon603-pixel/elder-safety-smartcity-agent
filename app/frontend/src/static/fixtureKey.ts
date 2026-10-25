/** Canonical fixture key shared (by contract) with scripts/record_fixtures.mjs: "METHOD /path?sorted&query", API prefix stripped. */
export const TIME_PARAMS: ReadonlySet<string> = new Set(["date", "hour", "minute", "at", "day", "daytype"]);

export interface ParsedKey { method: string; path: string; params: [string, string][] }

export function canonicalKey(method: string, pathWithQuery: string): string {
  const [path = "", query = ""] = pathWithQuery.split("?", 2);
  const sp = new URLSearchParams(query);
  const params = [...sp.entries()].sort((a, b) => (a[0] === b[0] ? (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0) : a[0] < b[0] ? -1 : 1));
  const q = params.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
  return `${method.toUpperCase()} ${path}${q === "" ? "" : `?${q}`}`;
}

export function parseKey(key: string): ParsedKey {
  const sp = key.indexOf(" ");
  const method = key.slice(0, sp);
  const [path = "", query = ""] = key.slice(sp + 1).split("?", 2);
  return { method, path, params: [...new URLSearchParams(query).entries()] };
}
