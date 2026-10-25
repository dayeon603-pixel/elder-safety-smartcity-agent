import type { ReactNode } from "react";

interface CardProps {
  title?: string;
  caption?: ReactNode;
  /** Right-aligned header slot (buttons, badges). */
  actions?: ReactNode;
  /** Heading level for the title; default h2. */
  as?: "h2" | "h3";
  id?: string;
  children: ReactNode;
  /** Remove inner padding (tables that bleed to the edge). */
  flush?: boolean;
}

/** Bordered panel on the sunken page background. No shadow, no rounding beyond 2px. */
export function Card({ title, caption, actions, as: H = "h2", id, children, flush = false }: CardProps) {
  const headingId = id !== undefined ? `${id}-title` : undefined;
  return (
    <section id={id} aria-labelledby={title !== undefined ? headingId : undefined} className="scroll-mt-24 border border-line bg-surface">
      {title !== undefined && (
        <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-2 border-b border-line px-4 py-4 sm:px-6">
          <div className="min-w-0">
            <H id={headingId} className={H === "h2" ? "text-h3" : "text-lead font-semibold"}>
              {title}
            </H>
            {caption !== undefined && <p className="mt-1 max-w-prose text-small text-ink-muted">{caption}</p>}
          </div>
          {actions !== undefined && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? "" : "p-4 sm:p-6"}>{children}</div>
    </section>
  );
}
