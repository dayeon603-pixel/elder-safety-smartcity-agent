import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useAuth } from "../auth/AuthContext";
import { EXTENSION_LINKS, FOOTER_CREDITS, FOOTER_DATA_CREDITS, IS_WALK_FRAMING, SERVICE_NAME, SERVICE_TAGLINE } from "../config";
import { Button } from "./Button";
import { ErrorBoundary } from "./ErrorBoundary";

export interface NavItem {
  to: string;
  label: string;
  end?: boolean;
  /** Other path prefixes that keep this item marked active (e.g. 근거 covers /silver, /demo, /aed). */
  also?: readonly string[];
}

interface AppShellProps {
  /** Slot for the service name / logo. Defaults to the configured text name. */
  serviceName?: ReactNode;
  nav: readonly NavItem[];
}

const NAV_LINK =
  "relative inline-flex min-h-11 items-center whitespace-nowrap px-3 text-body no-underline sm:min-h-10 " +
  "decoration-transparent hover:text-ink";

function Footer() {
  return (
    <footer className="mt-16 border-t border-line bg-surface print:hidden">
      <div className="mx-auto grid max-w-page gap-8 px-4 py-8 sm:px-6 md:grid-cols-[1.2fr_2fr]">
        <div>
          <p className="text-body font-semibold">{SERVICE_NAME}</p>
          <p className="mt-1 max-w-prose text-small text-ink-muted">{SERVICE_TAGLINE}</p>
        </div>
        <div className="grid gap-6 sm:grid-cols-2">
          <section aria-labelledby="credits-data">
            <h2 id="credits-data" className="eyebrow">데이터 출처</h2>
            <ul className="mt-2 space-y-1 text-small">
              {FOOTER_DATA_CREDITS.map((c) => (
                <li key={c.href}>
                  <a href={c.href} target="_blank" rel="noreferrer noopener">
                    {c.label}
                  </a>
                </li>
              ))}
              <li>
                <Link to="/data" className="font-medium">데이터 출처 전체 보기 →</Link>
              </li>
            </ul>
          </section>
          {IS_WALK_FRAMING && (
          <section aria-labelledby="credits-ext">
            <h2 id="credits-ext" className="eyebrow">확장 모듈</h2>
            <ul className="mt-2 space-y-1 text-small">
              {EXTENSION_LINKS.map((l) => <li key={l.to}><Link to={l.to}>{l.label}</Link></li>)}
            </ul>
          </section>
          )}
          <section aria-labelledby="credits-oss">
            <h2 id="credits-oss" className="eyebrow">오픈소스</h2>
            <ul className="mt-2 space-y-1 text-small">
              {FOOTER_CREDITS.map((c) => (
                <li key={c.href}>
                  <a href={c.href} target="_blank" rel="noreferrer noopener">
                    {c.label}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </footer>
  );
}

function ApiStatus() {
  const { state } = useApi(api.info);
  const text =
    state.status === "ready" ? `API ${state.data.version}` : state.status === "error" ? "API 연결 안 됨" : "API 확인 중";
  return (
    <span className={`hidden font-mono text-caption sm:inline ${state.status === "error" ? "text-danger" : "text-ink-faint"}`} role="status">
      {text}
    </span>
  );
}

export function AppShell({ serviceName = SERVICE_NAME, nav }: AppShellProps) {
  const { session, logout } = useAuth();
  const location = useLocation();
  const main = useRef<HTMLElement>(null);
  const first = useRef(true);

  // Route change: move focus to <main> so keyboard/AT users land at the new content.
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    main.current?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }, [location.pathname]);

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only print:hidden focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:bg-surface focus:px-4 focus:py-2"
        onClick={(e) => {
          e.preventDefault();
          main.current?.focus();
        }}
      >
        본문으로 건너뛰기
      </a>
      <header className="sticky top-0 z-30 border-b border-line bg-surface print:hidden">
        <div className="mx-auto flex max-w-page flex-wrap items-center justify-between gap-x-6 px-4 sm:px-6 md:flex-nowrap">
          <div className="flex h-14 items-center md:h-16">
            <Link to="/" className="text-h3 font-bold text-ink no-underline hover:text-ink" aria-label={`${SERVICE_NAME} 홈`}>
              {serviceName}
            </Link>
          </div>
          <div className="order-2 flex h-14 items-center gap-3 md:order-3 md:h-16">
            <ApiStatus />
            {session === null ? (
              <>
                <Link to="/login" className="px-2 py-2 text-small font-medium no-underline">
                  로그인
                </Link>
                <Link
                  to="/register"
                  className="border border-accent px-3 py-1.5 text-small font-medium text-accent no-underline hover:bg-accent-wash"
                >
                  회원가입
                </Link>
              </>
            ) : (
              <>
                <span className="hidden max-w-[16ch] truncate text-small text-ink-muted sm:inline" title={session.email}>
                  {session.email}
                </span>
                <Button variant="secondary" onClick={logout} className="!min-h-9 !px-3 !text-small">
                  로그아웃
                </Button>
              </>
            )}
          </div>
          <nav
            aria-label="주 메뉴"
            className="order-3 -mx-4 flex w-[calc(100%+2rem)] overflow-x-auto border-t border-line px-1 sm:-mx-6 sm:w-[calc(100%+3rem)] sm:px-3 md:order-2 md:m-0 md:w-auto md:flex-1 md:border-0 md:px-0"
          >
            {nav.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) => {
                  const active = isActive || (n.also ?? []).some((p) => location.pathname === p || location.pathname.startsWith(`${p}/`));
                  return `${NAV_LINK} ${active ? "font-semibold text-ink after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-accent" : "text-ink-muted"}`;
                }}
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main id="main" ref={main} tabIndex={-1} className="mx-auto w-full max-w-page flex-1 px-4 py-8 outline-none sm:px-6 sm:py-12">
        <ErrorBoundary resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
      <Footer />
    </div>
  );
}

/** Page title block used at the top of every page. */
export function PageHeader({ eyebrow, title, lead, actions }: { eyebrow?: string; title: string; lead?: ReactNode; actions?: ReactNode }) {
  useEffect(() => {
    document.title = `${title} · ${SERVICE_NAME}`;
  }, [title]);
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4 sm:mb-10">
      <div className="max-w-prose">
        {eyebrow !== undefined && <p className="eyebrow mb-2">{eyebrow}</p>}
        <h1 className="text-h1 sm:text-display">{title}</h1>
        {lead !== undefined && <p className="mt-3 text-lead text-ink-muted">{lead}</p>}
      </div>
      {actions !== undefined && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}
