import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, describeError } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("describeError", () => {
  it("maps known server messages and statuses to Korean", () => {
    expect(describeError(new ApiError(401, "invalid credentials"))).toMatch(/이메일 또는 비밀번호/);
    expect(describeError(new ApiError(403, "forbidden"))).toMatch(/권한/);
    expect(describeError(new ApiError(422, "row 5 (line 7): label is not numeric"))).toBe("5번째 행(파일 7번째 줄)에 문제가 있습니다: label is not numeric");
    expect(describeError(new ApiError(409, "CSV is empty"))).toMatch(/비어 있습니다/);
    expect(describeError(new ApiError(429, "Too Many Requests", 7.2))).toContain("8초");
    expect(describeError(new ApiError(500, "x"))).toMatch(/서버에서 오류/);
    expect(describeError(new TypeError("Failed to fetch"))).toMatch(/연결할 수 없습니다/);
  });
});

describe("request", () => {
  it("flattens FastAPI validation detail arrays and reads Retry-After", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: [{ loc: ["body", "email"], msg: "bad email" }] }), { status: 422 }),
    ));
    await expect(api.login("a", "b")).rejects.toMatchObject({ status: 422, message: "email: bad email" });

    vi.stubGlobal("fetch", vi.fn(async () => new Response("slow down", { status: 429, headers: { "Retry-After": "5" } })));
    await expect(api.datasets()).rejects.toMatchObject({ status: 429, retryAfter: 5 });
  });
});
