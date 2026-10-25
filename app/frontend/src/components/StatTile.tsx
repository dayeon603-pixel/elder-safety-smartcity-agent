import type { ReactNode } from "react";
import { Skeleton } from "./Skeleton";

interface StatTileProps {
  label: string;
  value: ReactNode;
  /** Small line under the value: unit, period, or how it was computed. */
  note?: ReactNode;
  tone?: "default" | "accent";
  loading?: boolean;
}

/** One number with its label. Label first in DOM order so screen readers read "label, value". */
export function StatTile({ label, value, note, tone = "default", loading = false }: StatTileProps) {
  return (
    <div className="flex flex-col gap-1 border border-line bg-surface px-4 py-4 sm:px-6">
      <dt className="text-small text-ink-muted">{label}</dt>
      <dd className="m-0">
        {loading ? (
          <>
            <Skeleton className="mt-1 h-8 w-24" />
            <span className="sr-only">불러오는 중</span>
          </>
        ) : (
          <span className={`num block text-h1 font-semibold ${tone === "accent" ? "text-accent" : "text-ink"}`}>{value}</span>
        )}
        {note !== undefined && !loading && <span className="mt-1 block text-caption text-ink-faint">{note}</span>}
      </dd>
    </div>
  );
}

/** Grid wrapper so tiles share one <dl>. */
export function StatGrid({ children }: { children: ReactNode }) {
  return <dl className="m-0 grid grid-cols-1 gap-4 min-[480px]:grid-cols-2 lg:grid-cols-4">{children}</dl>;
}
