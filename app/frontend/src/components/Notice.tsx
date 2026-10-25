import type { ReactNode } from "react";
import { Button } from "./Button";

type Tone = "neutral" | "danger" | "success";
const TONE: Record<Tone, string> = {
  neutral: "border-line bg-surface-sunken text-ink-muted",
  danger: "border-danger/40 bg-danger-wash text-danger",
  success: "border-success/40 bg-success-wash text-success",
};

/** Inline message. `danger` uses role=alert so it is announced. */
export function Notice({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <p role={tone === "danger" ? "alert" : undefined} className={`border px-4 py-3 text-small ${TONE[tone]}`}>
      {children}
    </p>
  );
}

/** Empty list/table state: say what is missing and what to do next. */
export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 border border-dashed border-line-strong px-4 py-8 sm:px-6">
      <p className="text-body font-semibold text-ink">{title}</p>
      {children !== undefined && <p className="max-w-prose text-small text-ink-muted">{children}</p>}
      {action !== undefined && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** Failed load with a retry button. */
export function ErrorState({ message, onRetry, title = "불러오지 못했습니다" }: { message: string; onRetry?: () => void; title?: string }) {
  return (
    <div role="alert" className="flex flex-col items-start gap-2 border border-danger/40 bg-danger-wash px-4 py-6 sm:px-6">
      <p className="text-body font-semibold text-danger">{title}</p>
      <p className="max-w-prose text-small text-ink-muted">{message}</p>
      {onRetry !== undefined && (
        <Button variant="secondary" onClick={onRetry} className="mt-2">
          다시 시도
        </Button>
      )}
    </div>
  );
}
