import type { ReactNode } from "react";

export type BadgeTone = "neutral" | "accent" | "success" | "danger";

const TONE: Record<BadgeTone, string> = {
  neutral: "border-line-strong bg-surface-sunken text-ink-muted",
  accent: "border-accent/40 bg-accent-wash text-accent-strong",
  success: "border-success/40 bg-success-wash text-success",
  danger: "border-danger/40 bg-danger-wash text-danger",
};

/** Status label. Always text, never color alone. */
export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center border px-2 py-0.5 text-caption font-medium ${TONE[tone]}`}>{children}</span>
  );
}
