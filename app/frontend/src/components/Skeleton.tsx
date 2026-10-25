import type { CSSProperties } from "react";

/** Placeholder block. Decorative; the visible label for AT is the sr-only text. */
export function Skeleton({ className = "", style }: { className?: string; style?: CSSProperties }) {
  return <div aria-hidden="true" style={style} className={`animate-pulse2 bg-surface-inset ${className}`} />;
}

export function SkeletonText({ lines = 3, label = "불러오는 중" }: { lines?: number; label?: string }) {
  return (
    <div role="status" className="space-y-3">
      <span className="sr-only">{label}</span>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={`h-4 ${i === lines - 1 ? "w-2/3" : "w-full"}`} />
      ))}
    </div>
  );
}
