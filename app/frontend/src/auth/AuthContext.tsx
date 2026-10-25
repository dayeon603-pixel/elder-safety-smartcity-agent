import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError, api, setToken, setUnauthorizedHandler } from "../api/client";
import type { TokenResponse } from "../api/types";
import { useToast } from "../components/Toast";
import { SESSION_STORAGE_KEY } from "../config";

export interface Session {
  token: string;
  email: string;
  /** epoch ms */
  expiresAt: number;
}

export type RegisterOutcome = "signed-in" | "needs-login";

interface AuthApi {
  session: Session | null;
  login: (email: string, password: string) => Promise<void>;
  /** Resolves 'signed-in' when the follow-up login worked, 'needs-login' otherwise (backend replies identically for taken emails). */
  register: (email: string, password: string) => Promise<RegisterOutcome>;
  logout: () => void;
}

const MS = 1000;
const MAX_TIMEOUT_MS = 2 ** 31 - 1;
const AuthContext = createContext<AuthApi | null>(null);

function isSession(x: unknown): x is Session {
  return (
    typeof x === "object" && x !== null &&
    "token" in x && typeof x.token === "string" &&
    "email" in x && typeof x.email === "string" &&
    "expiresAt" in x && typeof x.expiresAt === "number"
  );
}

/** Read a still-valid session from sessionStorage (tab-scoped; cleared when the tab closes). */
export function loadSession(now: number = Date.now()): Session | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    if (isSession(parsed) && parsed.expiresAt > now) return parsed;
    window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
  } catch (err) {
    if (!(err instanceof SyntaxError) && !(err instanceof DOMException)) throw err;
  }
  return null;
}

function saveSession(s: Session | null): void {
  try {
    if (s === null) window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
    else window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(s));
  } catch (err) {
    if (!(err instanceof DOMException)) throw err; // storage blocked: memory-only session still works
  }
}

function fromToken(email: string, t: TokenResponse): Session {
  return { token: t.access_token, email, expiresAt: Date.now() + t.expires_in * MS };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => {
    const s = loadSession();
    setToken(s?.token ?? null); // before first child effect so first requests are authorised
    return s;
  });
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();

  const apply = useCallback((s: Session | null) => {
    setToken(s?.token ?? null);
    saveSession(s);
    setSession(s);
  }, []);

  const logout = useCallback(() => apply(null), [apply]);

  const expire = useCallback(() => {
    apply(null);
    toast.show("세션이 만료되어 로그아웃되었습니다. 다시 로그인해 주세요.", "info");
    navigate("/login", { state: { from: location.pathname } });
  }, [apply, toast, navigate, location.pathname]);

  useEffect(() => {
    setUnauthorizedHandler(expire);
    return () => setUnauthorizedHandler(null);
  }, [expire]);

  useEffect(() => {
    if (session === null) return;
    const wait = Math.min(Math.max(session.expiresAt - Date.now(), 0), MAX_TIMEOUT_MS);
    const id = window.setTimeout(expire, wait);
    return () => window.clearTimeout(id);
  }, [session, expire]);

  const login = useCallback(
    async (email: string, password: string) => apply(fromToken(email, await api.login(email, password))),
    [apply],
  );
  /**
   * POST /auth/register answers 202 without a token (and identically for taken emails, so it cannot be used
   * to probe which emails exist). We therefore sign in with the same credentials. A 401 here means the email
   * already had an account with another password: send the user to the login form instead of guessing.
   */
  const register = useCallback(
    async (email: string, password: string): Promise<RegisterOutcome> => {
      await api.register(email, password);
      try {
        apply(fromToken(email, await api.login(email, password)));
        return "signed-in";
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return "needs-login";
        throw err;
      }
    },
    [apply],
  );

  const value = useMemo<AuthApi>(() => ({ session, login, register, logout }), [session, login, register, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthApi {
  const ctx = useContext(AuthContext);
  if (ctx === null) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
