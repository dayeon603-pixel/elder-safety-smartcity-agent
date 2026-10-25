import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Column } from "./DataTable";
import { DataTable } from "./DataTable";

interface Row { id: number; name: string; n: number }
const COLS: Column<Row>[] = [
  { key: "name", header: "이름", render: (r) => r.name, sortValue: (r) => r.name },
  { key: "n", header: "건수", numeric: true, render: (r) => r.n.toLocaleString("ko-KR"), sortValue: (r) => r.n },
];
const ROWS: Row[] = Array.from({ length: 25 }, (_, i) => ({ id: i, name: `항목${String(i).padStart(2, "0")}`, n: (i * 7919) % 1000 }));

function names(): string[] {
  const body = screen.getAllByRole("rowgroup")[1];
  if (body === undefined) throw new Error("no tbody");
  return within(body).getAllByRole("row").map((r) => within(r).getAllByRole("cell")[0]?.textContent ?? "");
}

describe("DataTable", () => {
  it("shows the empty state with hint", () => {
    render(<DataTable caption="c" columns={COLS} rows={[]} rowKey={(r) => r.id} emptyTitle="비어 있음" emptyHint="수집하세요" />);
    expect(screen.getByText("비어 있음")).toBeInTheDocument();
    expect(screen.getByText("수집하세요")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows skeleton rows while loading, not the empty state", () => {
    render(<DataTable caption="c" columns={COLS} rows={[]} rowKey={(r) => r.id} loading />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("불러오는 중");
  });

  it("paginates and reports the range", async () => {
    const user = userEvent.setup();
    render(<DataTable caption="c" columns={COLS} rows={ROWS} rowKey={(r) => r.id} pageSize={10} />);
    expect(names()).toHaveLength(10);
    expect(screen.getByText(/전체 25건 중 1–10/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "이전 페이지" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "다음 페이지" }));
    await user.click(screen.getByRole("button", { name: "다음 페이지" }));
    expect(names()).toHaveLength(5);
    expect(screen.getByText(/21–25/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다음 페이지" })).toBeDisabled();
  });

  it("sorts asc, desc, then clears, and exposes aria-sort", async () => {
    const user = userEvent.setup();
    render(<DataTable caption="c" columns={COLS} rows={ROWS} rowKey={(r) => r.id} pageSize={25} />);
    const th = screen.getByRole("columnheader", { name: /건수/ });
    expect(th).toHaveAttribute("aria-sort", "none");
    await user.click(within(th).getByRole("button"));
    expect(th).toHaveAttribute("aria-sort", "ascending");
    const asc = names();
    await user.click(within(th).getByRole("button"));
    expect(th).toHaveAttribute("aria-sort", "descending");
    expect(names()).toEqual([...names()]); // stable read
    expect(names()[0]).not.toBe(asc[0]);
    await user.click(within(th).getByRole("button"));
    expect(th).toHaveAttribute("aria-sort", "none");
    expect(names()[0]).toBe("항목00");
  });

  it("formats numbers through the column renderer (ko-KR grouping)", () => {
    render(<DataTable caption="c" columns={COLS} rows={[{ id: 1, name: "a", n: 1234567 }]} rowKey={(r) => r.id} />);
    expect(screen.getByText("1,234,567")).toBeInTheDocument();
  });
});
