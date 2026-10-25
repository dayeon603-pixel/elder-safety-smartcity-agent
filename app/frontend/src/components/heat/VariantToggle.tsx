import { useId } from "react";
import type { HeatVariant } from "../../api/heatTypes";
import { VARIANT_EXPLAIN } from "../../lib/heatEval";

interface VariantToggleProps {
  value: HeatVariant;
  onChange: (v: HeatVariant) => void;
  variants: readonly HeatVariant[];
}

/** Two-option segmented control (native radios, so arrow keys and screen readers work) with a one-line explanation. */
export function VariantToggle({ value, onChange, variants }: VariantToggleProps) {
  const name = useId();
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className="mb-1 p-0 text-small font-medium text-ink">입력 시점</legend>
      <div className="inline-flex border border-line-strong">
        {variants.map((v, i) => (
          <label key={v} className={`relative ${i > 0 ? "border-l border-line-strong" : ""}`}>
            <input
              type="radio"
              name={name}
              value={v}
              checked={value === v}
              onChange={() => onChange(v)}
              className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0"
            />
            <span className="flex min-h-11 items-center px-4 text-body text-ink-muted peer-checked:bg-accent peer-checked:font-medium peer-checked:text-white peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent sm:min-h-10">
              {v} · {v === "F" ? "예보 포함" : "관측만"}
            </span>
          </label>
        ))}
      </div>
      <ul className="m-0 mt-2 list-none space-y-0.5 p-0 text-small text-ink-muted">
        {variants.map((v) => (
          <li key={v} className={v === value ? "font-medium text-ink" : ""}>
            {v}: {VARIANT_EXPLAIN[v]}
            {v === value && <span className="sr-only"> (선택됨)</span>}
          </li>
        ))}
      </ul>
    </fieldset>
  );
}
