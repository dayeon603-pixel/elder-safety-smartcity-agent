import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setToken } from "../api/client";
import { SESSION_STORAGE_KEY } from "../config";
import { renderApp } from "../test/render";
import { loadSession, useAuth } from "./AuthContext";

function Probe() {
  const { session, login, logout } = useAuth();
  return (
    <div>
      <p data-testid="who">{session?.email ?? "anon"}</p>
      <button onClick={() => void login("a@example.com", "pw-pw-pw-pw-pw").catch(() => undefined)}>login</button>
      <button onClick={logout}>logout</button>
    </div>
  );
}

const json = (status: number, body: unknown, headers: Record<string, string> = {}): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

beforeEach(() => setToken(null));
afterEach(() => vi.unstubAllGlobals());

describe("auth", () => {
  it("login stores the session in sessionStorage and sends the bearer token afterwards; logout clears both", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (...args: [string, RequestInit?]) =>
      args[0].endsWith("/auth/login") ? json(200, { access_token: "tok123", token_type: "bearer", expires_in: 900 }) : json(200, []),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderApp(<Probe />);
    await user.click(screen.getByText("login"));
    await waitFor(() => expect(screen.getByTestId("who")).toHaveTextContent("a@example.com"));
    expect(JSON.parse(window.sessionStorage.getItem(SESSION_STORAGE_KEY) ?? "{}")).toMatchObject({ token: "tok123", email: "a@example.com" });

    const { api } = await import("../api/client");
    await api.datasets();
    const headers = new Headers((fetchMock.mock.calls.at(-1) as unknown as [string, RequestInit])[1].headers);
    expect(headers.get("Authorization")).toBe("Bearer tok123");

    await user.click(screen.getByText("logout"));
    expect(screen.getByTestId("who")).toHaveTextContent("anon");
    expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
    await api.datasets();
    expect(new Headers((fetchMock.mock.calls.at(-1) as unknown as [string, RequestInit])[1].headers).has("Authorization")).toBe(false);
  });

  it("a 401 on an authenticated request logs out and tells the user", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      url.endsWith("/auth/login")
        ? json(200, { access_token: "tok", token_type: "bearer", expires_in: 900 })
        : json(401, { detail: "invalid token" }),
    ));
    renderApp(<Probe />);
    await user.click(screen.getByText("login"));
    await waitFor(() => expect(screen.getByTestId("who")).toHaveTextContent("a@example.com"));
    const { api } = await import("../api/client");
    await expect(api.datasets()).rejects.toMatchObject({ status: 401 });
    await waitFor(() => expect(screen.getByTestId("who")).toHaveTextContent("anon"));
    expect(screen.getByRole("status")).toHaveTextContent("세션이 만료");
  });

  it("a 401 from the login call itself (bad password) does not trigger the expiry flow", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { detail: "invalid credentials" })));
    renderApp(<Probe />);
    await user.click(screen.getByText("login"));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByText(/세션이 만료/)).toBeNull();
  });

  it("restores a valid session and drops an expired or corrupt one", () => {
    const now = 1_000_000;
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({ token: "t", email: "e@x.kr", expiresAt: now + 5 }));
    expect(loadSession(now)?.email).toBe("e@x.kr");
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({ token: "t", email: "e@x.kr", expiresAt: now - 1 }));
    expect(loadSession(now)).toBeNull();
    expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, "{not json");
    expect(loadSession(now)).toBeNull();
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({ token: 1 }));
    expect(loadSession(now)).toBeNull();
  });
});
