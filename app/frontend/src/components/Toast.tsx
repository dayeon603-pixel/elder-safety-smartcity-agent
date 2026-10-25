import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { TOAST_ERROR_MS, TOAST_MS } from "../config";

export type ToastTone = "info" | "success" | "error";

interface ToastItem {
  id: number;
  tone: ToastTone;
  message: string;
}

interface ToastApi {
  show: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const TONE_CLASS: Record<ToastTone, string> = {
  info: "border-l-ink-muted",
  success: "border-l-success",
  error: "border-l-danger",
};

function ToastView({ item, onClose }: { item: ToastItem; onClose: (id: number) => void }) {
  const timer = useRef<number | null>(null);
  const duration = item.tone === "error" ? TOAST_ERROR_MS : TOAST_MS;

  const start = useCallback(() => {
    timer.current = window.setTimeout(() => onClose(item.id), duration);
  }, [duration, item.id, onClose]);
  const stop = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  useEffect(() => {
    start();
    return stop;
  }, [start, stop]);

  return (
    <li
      className={`pointer-events-auto flex animate-rise items-start gap-3 border border-l-4 border-line bg-surface px-4 py-3 text-small shadow-[0_2px_8px_rgba(20,23,28,0.12)] ${TONE_CLASS[item.tone]}`}
      onMouseEnter={stop}
      onMouseLeave={start}
      onFocus={stop}
      onBlur={start}
    >
      <p className="flex-1 text-ink">{item.message}</p>
      <button
        type="button"
        onClick={() => onClose(item.id)}
        aria-label="알림 닫기"
        className="-m-1 p-1 text-ink-muted hover:text-ink"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M3 3l10 10M13 3L3 13" stroke="currentColor" strokeWidth="1.5" fill="none" />
        </svg>
      </button>
    </li>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const close = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);
  const show = useCallback((message: string, tone: ToastTone = "info") => {
    const id = nextId.current++;
    setItems((xs) => [...xs, { id, tone, message }]);
  }, []);
  const api = useMemo<ToastApi>(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      {/* Live region exists permanently so screen readers announce inserted toasts. */}
      <div className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex justify-end sm:inset-x-auto sm:right-6 sm:bottom-6 sm:w-96">
        <ul role="status" aria-live="polite" aria-relevant="additions" className="flex w-full flex-col gap-2">
          {items.map((it) => (
            <ToastView key={it.id} item={it} onClose={close} />
          ))}
        </ul>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (ctx === null) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
