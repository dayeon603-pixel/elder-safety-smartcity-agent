import { useId, useState } from "react";
import type { ReactNode } from "react";

/**
 * Keyboard-accessible "what does this mean" note. A real button toggles the explanation, which then
 * sits inline (no floating layer to clip on a 375px screen). Escape closes it.
 */
export function InfoTip({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        aria-label={`${label} 설명`}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
        className="mx-1 inline-flex h-5 w-5 items-center justify-center border border-line-strong align-middle text-caption font-semibold text-ink-muted hover:bg-surface-sunken aria-expanded:border-accent aria-expanded:bg-accent-wash aria-expanded:text-accent-strong"
      >
        <span aria-hidden="true">i</span>
      </button>
      {open && (
        <span id={id} role="note" className="my-1 block max-w-prose border-l-2 border-accent bg-accent-wash/40 px-3 py-1.5 text-caption font-normal text-ink-muted">
          {children}
        </span>
      )}
    </>
  );
}
