import type { PipelineStageId } from "../../lib/fieldIncident";
import { PIPELINE_STAGES } from "../../lib/fieldIncident";

/** Five-stage pipeline; a stage lights up only while the part of the demo that uses it is actually running. */
export function PipelineStrip({ active }: { active: Readonly<Record<PipelineStageId, boolean>> }) {
  return (
    <nav aria-label="처리 흐름">
      <ol className="m-0 grid list-none grid-cols-2 gap-2 p-0 sm:grid-cols-4 lg:grid-cols-7">
        {PIPELINE_STAGES.map((s, i) => {
          const on = active[s.id];
          return (
            <li
              key={s.id}
              data-stage={s.id}
              data-active={on}
              className={`relative border px-3 py-2 ${on ? "border-accent bg-accent-wash" : "border-line bg-surface"}`}
            >
              <p className="text-caption text-ink-faint">
                <span className="num">{i + 1}</span> · {on ? "실행 중" : "대기"}
              </p>
              <p className="text-small font-semibold">{s.label}</p>
              <p className="text-caption text-ink-muted">{s.sub}</p>
              <p className="mt-1"><span className="inline-block border border-line-strong bg-surface-sunken px-1.5 text-caption text-ink-muted">{s.badge}</span></p>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
