import type { ReactNode } from "react";

export interface StepDef {
  id: string;
  label: string;
  /** Short helper under the label on wide screens. */
  description?: string;
}

interface StepperProps {
  steps: readonly StepDef[];
  /** Zero-based index of the current step. */
  current: number;
  /** Highest step index that may be revisited (steps after it are not clickable). */
  reached: number;
  onSelect?: (index: number) => void;
}

/**
 * Progress indicator for a single guided flow. Past steps are buttons (go back),
 * the current step is aria-current="step", future steps are inert.
 */
export function Stepper({ steps, current, reached, onSelect }: StepperProps) {
  return (
    <nav aria-label="진행 단계">
      <ol className="grid gap-0 border border-line bg-surface sm:grid-flow-col sm:auto-cols-fr">
        {steps.map((s, i) => {
          const state = i < current ? "done" : i === current ? "current" : "todo";
          const clickable = onSelect !== undefined && i <= reached && i !== current;
          const body: ReactNode = (
            <>
              <span
                aria-hidden="true"
                className={`num flex h-6 w-6 shrink-0 items-center justify-center border text-caption font-semibold ${
                  state === "current"
                    ? "border-accent bg-accent text-white"
                    : state === "done"
                      ? "border-accent bg-accent-wash text-accent-strong"
                      : "border-line-strong text-ink-faint"
                }`}
              >
                {state === "done" ? "✓" : i + 1}
              </span>
              <span className="min-w-0 text-left">
                <span className={`block text-small font-medium ${state === "todo" ? "text-ink-faint" : "text-ink"}`}>{s.label}</span>
                {s.description !== undefined && <span className="hidden text-caption text-ink-faint lg:block">{s.description}</span>}
              </span>
              <span className="sr-only">
                {state === "done" ? " (완료)" : state === "current" ? " (현재 단계)" : " (예정)"}
              </span>
            </>
          );
          const cell = "flex min-h-12 w-full items-center gap-3 px-4 py-3";
          return (
            <li
              key={s.id}
              aria-current={state === "current" ? "step" : undefined}
              className={`border-line ${i > 0 ? "border-t sm:border-l sm:border-t-0" : ""} ${state === "current" ? "bg-accent-wash/50 shadow-[inset_0_-2px_0_#0072B2] sm:shadow-[inset_0_-2px_0_#0072B2]" : ""}`}
            >
              {clickable ? (
                <button type="button" onClick={() => onSelect(i)} className={`${cell} hover:bg-surface-sunken`}>
                  {body}
                </button>
              ) : (
                <div className={cell}>{body}</div>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
