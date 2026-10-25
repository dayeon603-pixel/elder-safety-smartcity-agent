import { useId, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { TABLE_PAGE_SIZE } from "../config";
import { formatInt } from "../lib/format";
import { Button } from "./Button";
import { EmptyState } from "./Notice";
import { ScrollRegion } from "./ScrollRegion";
import { Skeleton } from "./Skeleton";

export interface Column<T> {
  key: string;
  header: string;
  /** Cell content. */
  render: (row: T) => ReactNode;
  /** Presence makes the column sortable. Strings sort with Korean collation. */
  sortValue?: (row: T) => string | number;
  /** Right-align + tabular figures. */
  numeric?: boolean;
  /** Hide below the sm breakpoint (keep tables readable at 375px). */
  hideOnMobile?: boolean;
}

interface DataTableProps<T> {
  caption: string;
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string | number;
  pageSize?: number;
  loading?: boolean;
  emptyTitle?: string;
  emptyHint?: ReactNode;
  emptyAction?: ReactNode;
  initialSort?: { key: string; dir: "asc" | "desc" };
}

type SortDir = "asc" | "desc";
const COLLATOR = new Intl.Collator("ko-KR", { numeric: true });
const SKELETON_ROWS = 4;

function compare(a: string | number, b: string | number): number {
  return typeof a === "number" && typeof b === "number" ? a - b : COLLATOR.compare(String(a), String(b));
}

/**
 * Accessible data table: sortable headers (aria-sort, real buttons), client pagination,
 * loading skeleton, empty state, ko-KR number formatting via column renderers.
 * The scroll wrapper is focusable so keyboard users can pan wide tables.
 */
export function DataTable<T>({
  caption, columns, rows, rowKey, pageSize = TABLE_PAGE_SIZE, loading = false,
  emptyTitle = "표시할 데이터가 없습니다", emptyHint, emptyAction, initialSort,
}: DataTableProps<T>) {
  const capId = useId();
  const [sort, setSort] = useState<{ key: string; dir: SortDir } | null>(initialSort ?? null);
  const [page, setPage] = useState(0);

  const sorted = useMemo(() => {
    const col = sort === null ? undefined : columns.find((c) => c.key === sort.key);
    if (sort === null || col?.sortValue === undefined) return rows;
    const sv = col.sortValue;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => sign * compare(sv(a), sv(b)));
  }, [rows, columns, sort]);

  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(page, pages - 1);
  const visible = sorted.slice(safePage * pageSize, safePage * pageSize + pageSize);

  const toggle = (key: string): void => {
    setPage(0);
    setSort((s) => (s?.key === key ? (s.dir === "asc" ? { key, dir: "desc" } : null) : { key, dir: "asc" }));
  };

  if (!loading && rows.length === 0) return <EmptyState title={emptyTitle} action={emptyAction}>{emptyHint}</EmptyState>;

  const first = sorted.length === 0 ? 0 : safePage * pageSize + 1;
  const last = Math.min(sorted.length, (safePage + 1) * pageSize);

  return (
    <div>
      <ScrollRegion labelledBy={capId}>
        <table className="w-full min-w-full border-collapse text-left text-small">
          <caption id={capId} className="sr-only">
            {caption}
          </caption>
          <thead>
            <tr className="border-b border-line-strong bg-surface-sunken">
              {columns.map((c) => {
                const active = sort?.key === c.key;
                const ariaSort = c.sortValue === undefined ? undefined : active ? (sort.dir === "asc" ? "ascending" : "descending") : "none";
                return (
                  <th
                    key={c.key}
                    scope="col"
                    aria-sort={ariaSort}
                    className={`whitespace-nowrap px-4 py-2 text-caption font-medium text-ink-muted ${c.numeric ? "text-right" : ""} ${c.hideOnMobile ? "hidden sm:table-cell" : ""}`}
                  >
                    {c.sortValue === undefined ? (
                      c.header
                    ) : (
                      <button
                        type="button"
                        onClick={() => toggle(c.key)}
                        className={`-mx-1 inline-flex min-h-8 items-center gap-1 px-1 hover:text-ink ${c.numeric ? "flex-row-reverse" : ""}`}
                      >
                        {c.header}
                        <span aria-hidden="true" className={active ? "text-accent" : "text-line-strong"}>
                          {active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
                        </span>
                      </button>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: SKELETON_ROWS }, (_, i) => (
                  <tr key={i} className="border-b border-line">
                    {columns.map((c) => (
                      <td key={c.key} className={`px-4 py-3 ${c.hideOnMobile ? "hidden sm:table-cell" : ""}`}>
                        <Skeleton className="h-4 w-full max-w-[8rem]" />
                      </td>
                    ))}
                  </tr>
                ))
              : visible.map((row) => (
                  <tr key={rowKey(row)} className="border-b border-line last:border-b-0 hover:bg-surface-sunken/60">
                    {columns.map((c) => (
                      <td
                        key={c.key}
                        className={`px-4 py-3 align-top ${c.numeric ? "num text-right" : ""} ${c.hideOnMobile ? "hidden sm:table-cell" : ""}`}
                      >
                        {c.render(row)}
                      </td>
                    ))}
                  </tr>
                ))}
          </tbody>
        </table>
        {loading && <span role="status" className="sr-only">불러오는 중</span>}
      </ScrollRegion>
      {!loading && sorted.length > pageSize && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-3">
          <p className="num text-small text-ink-muted" aria-live="polite">
            전체 {formatInt(sorted.length)}건 중 {formatInt(first)}–{formatInt(last)}
          </p>
          <div className="flex items-center gap-2">
            <Button onClick={() => setPage(safePage - 1)} disabled={safePage === 0} aria-label="이전 페이지">
              이전
            </Button>
            <span className="num min-w-16 text-center text-small text-ink-muted">
              {safePage + 1} / {pages}
            </span>
            <Button onClick={() => setPage(safePage + 1)} disabled={safePage >= pages - 1} aria-label="다음 페이지">
              다음
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
