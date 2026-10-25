import type { ReactNode } from "react";

/**
 * Horizontally scrollable wrapper. Focusable on purpose: WCAG 2.1.1 requires keyboard users to be able
 * to pan overflowing content (axe rule scrollable-region-focusable).
 */
export function ScrollRegion({ label, labelledBy, children }: { label?: string; labelledBy?: string; children: ReactNode }) {
  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
    <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={label} aria-labelledby={labelledBy}>
      {children}
    </div>
  );
}
