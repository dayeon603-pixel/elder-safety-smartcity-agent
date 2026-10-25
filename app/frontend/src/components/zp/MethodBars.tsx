import type { SilverBar } from "../../lib/zpSilver";
import { pct0 } from "../../lib/zpSilver";
import { COLOR_BASELINE, COLOR_OURS } from "../ComparisonChart";

/**
 * Horizontal bars, one per method, baselines gray first and ours blue last. Whiskers are the 95% confidence interval.
 * Plain HTML so every number is real text for screen readers and print.
 */
export function MethodBars({ bars, label }: { bars: readonly SilverBar[]; label: string }) {
  return (
    <ul aria-label={label} className="m-0 list-none space-y-4 p-0">
      {bars.map((b) => (
        <li key={b.method} className="grid grid-cols-[7.5rem_1fr_3.25rem] items-center gap-x-3 gap-y-1 sm:grid-cols-[10rem_1fr_3.5rem]">
          <span className={`text-small ${b.ours ? "font-semibold text-ink" : "text-ink-muted"}`}>
            {b.label}
          </span>
          <span className="relative block h-5 bg-surface-inset" aria-hidden="true">
            <span className="block h-full" style={{ width: pct0(b.coverage), backgroundColor: b.ours ? COLOR_OURS : COLOR_BASELINE }} />
            <span className="absolute top-1/2 block h-0.5 -translate-y-1/2 bg-ink" style={{ left: pct0(b.lo), width: `${Math.max(0, (b.hi - b.lo) * 100)}%` }} />
            <span className="absolute top-1 block h-3 w-0.5 bg-ink" style={{ left: pct0(b.lo) }} />
            <span className="absolute top-1 block h-3 w-0.5 bg-ink" style={{ left: pct0(b.hi) }} />
          </span>
          <span className={`num text-right text-body ${b.ours ? "font-semibold text-accent" : "text-ink-muted"}`}>{pct0(b.coverage)}</span>
          <span className="col-start-2 col-end-4 -mt-0.5 text-caption text-ink-faint">
            {b.note} · 95% 신뢰구간 {pct0(b.lo)}–{pct0(b.hi)}
          </span>
        </li>
      ))}
    </ul>
  );
}
