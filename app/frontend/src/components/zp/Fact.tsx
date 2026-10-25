import type { ReactNode } from "react";

/** One labelled number inside a module panel: label first, value big. */
export function Fact({ label, value, note, tone = "default" }: { label: ReactNode; value: ReactNode; note?: ReactNode; tone?: "default" | "accent" | "danger" }) {
  const color = tone === "accent" ? "text-accent" : tone === "danger" ? "text-danger" : "text-ink";
  return (
    <div className="border-t border-line py-3 first:border-t-0 first:pt-0">
      <dt className="text-small text-ink-muted">{label}</dt>
      <dd className="m-0">
        <span className={`num block text-h2 font-semibold ${color}`}>{value}</span>
        {note !== undefined && <span className="mt-0.5 block text-caption text-ink-faint">{note}</span>}
      </dd>
    </div>
  );
}
