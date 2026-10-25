import { useEffect, useRef } from "react";
import type { HeatDay } from "../../api/heatTypes";
import { HEAT_DAY_LIST_MAX_HEIGHT_REM } from "../../config";
import { formatInt } from "../../lib/format";
import { dayLabel, gapOf, sharePercent } from "../../lib/heatDays";

interface DayPickerProps {
  days: readonly HeatDay[];
  selected: string | null;
  onSelect: (date: string) => void;
  /** A date to flag as the day with the largest gap. */
  highlight?: string;
}

function Captured({ n, total }: { n: number; total: number }) {
  const pct = sharePercent(n, total);
  return (
    <>
      <span className="num font-medium">{formatInt(n)}</span>
      {pct !== null && <span className="num block text-caption text-ink-faint">{pct}%</span>}
    </>
  );
}

/**
 * Test-season days, biggest "ours minus B1" gap first. Each row is one button; the selection is exposed with
 * aria-pressed (and a visible fill), never by color alone.
 */
export function DayPicker({ days, selected, onSelect, highlight }: DayPickerProps) {
  const box = useRef<HTMLDivElement>(null);
  const row = useRef<HTMLTableRowElement>(null);
  // Keep the preselected day visible when it is not near the top of the list (scrolls the list only, never the page).
  useEffect(() => {
    const b = box.current;
    const r = row.current;
    if (b === null || r === null) return;
    if (r.offsetTop < b.scrollTop || r.offsetTop + r.offsetHeight > b.scrollTop + b.clientHeight) {
      b.scrollTop = Math.max(0, r.offsetTop - b.clientHeight / 2);
    }
  }, [selected]);
  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
    <div ref={box} role="region" aria-label="날짜 목록" tabIndex={0} className="overflow-auto border border-line" style={{ maxHeight: `${HEAT_DAY_LIST_MAX_HEIGHT_REM}rem` }}>
      <table className="w-full border-collapse text-small">
        <caption className="sr-only">
          날짜별 환자 수와, 같은 경보 수에서 경보 지역에 있던 환자 수. 우리 모형이 더 잡은 날이 위에 있습니다.
        </caption>
        <thead className="sticky top-0 z-10 bg-surface-sunken text-left text-ink-muted">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">날짜</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">환자</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">
              <abbr title="특보 규칙이 경보한 지역에서 나온 환자" className="no-underline">특보 포착</abbr>
            </th>
            <th scope="col" className="px-2 py-2 text-right font-medium">우리 포착</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">차이</th>
          </tr>
        </thead>
        <tbody>
          {days.map((d) => {
            const gap = gapOf(d);
            const on = d.date === selected;
            return (
              <tr key={d.date} ref={on ? row : undefined} className={`border-t border-line ${on ? "bg-accent-wash" : ""}`}>
                <th scope="row" className="p-0 text-left font-normal">
                  <button
                    type="button"
                    aria-pressed={on}
                    onClick={() => onSelect(d.date)}
                    className="flex min-h-11 w-full items-center px-3 text-left font-medium hover:bg-surface-sunken aria-pressed:hover:bg-accent-wash sm:min-h-10"
                  >
                    <span className="num">{dayLabel(d.date)}</span>
                    {d.date === highlight && <span className="ml-2 border border-accent/40 bg-accent-wash px-1.5 text-caption font-medium text-accent-strong">차이 최대</span>}
                  </button>
                </th>
                <td className="num px-2 py-1 text-right">{formatInt(d.n_cases)}</td>
                <td className="px-2 py-1 text-right"><Captured n={d.b1_captured} total={d.n_cases} /></td>
                <td className="px-2 py-1 text-right"><Captured n={d.ours_captured} total={d.n_cases} /></td>
                <td className={`num px-3 py-1 text-right font-semibold ${gap > 0 ? "text-accent-strong" : "text-ink-muted"}`}>
                  {gap > 0 ? `+${formatInt(gap)}` : gap < 0 ? `−${formatInt(-gap)}` : "0"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
