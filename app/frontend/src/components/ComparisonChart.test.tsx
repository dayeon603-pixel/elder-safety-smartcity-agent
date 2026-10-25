import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ComparisonChart } from "./ComparisonChart";

const ROWS = [
  { key: "auroc", label: "AUROC", baseline: { point: 0.9476, lo: 0.914, hi: 0.976 }, ours: { point: 0.9975, lo: 0.993, hi: 1 } },
];

describe("ComparisonChart", () => {
  it("puts baseline first in the legend and exposes the numbers as a table for AT", () => {
    render(<ComparisonChart title="높을수록 좋은 지표" baselineLabel="단일 특성" oursLabel="전체 특성" rows={ROWS} />);
    const fig = screen.getByRole("figure");
    const legend = within(fig).getByText(/기준선 · 단일 특성/);
    const ours = within(fig).getByText(/비교 · 전체 특성/);
    expect(legend.compareDocumentPosition(ours) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const table = within(fig).getByRole("table", { name: "높을수록 좋은 지표" });
    expect(within(table).getByRole("row", { name: /AUROC/ })).toHaveTextContent("0.948 [0.914, 0.976]");
    expect(within(table).getByRole("row", { name: /AUROC/ })).toHaveTextContent("0.998 [0.993, 1.000]");
  });
});
