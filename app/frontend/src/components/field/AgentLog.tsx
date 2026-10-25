import type { ReactNode } from "react";

export interface LogEntry {
  id: string;
  tag: string;
  title: string;
  lines: readonly ReactNode[];
  tone?: "neutral" | "danger" | "success";
}

const BORDER = { neutral: "border-line-strong", danger: "border-danger", success: "border-success" } as const;

/** Side log of the engine's steps. Lines carry the arithmetic so a reader can check every number. */
export function AgentLog({ entries, emptyText }: { entries: readonly LogEntry[]; emptyText: string }) {
  return (
    <section aria-labelledby="agent-log-title" className="border border-line bg-surface-sunken">
      <h3 id="agent-log-title" className="border-b border-line px-4 py-3 text-body font-semibold">에이전트 로그</h3>
      {entries.length === 0 ? (
        <p className="px-4 py-4 text-small text-ink-muted">{emptyText}</p>
      ) : (
        <ol className="m-0 list-none space-y-3 p-4" aria-live="polite">
          {entries.map((e) => (
            <li key={e.id} className={`animate-rise border-l-2 pl-3 ${BORDER[e.tone ?? "neutral"]}`}>
              <p className="text-small font-semibold">
                <span className="num mr-2 font-mono text-ink-faint">{e.tag}</span>
                {e.title}
              </p>
              {e.lines.map((l, i) => (
                <p key={i} className="num mt-0.5 break-words text-small text-ink-muted">{l}</p>
              ))}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
