import { api } from "../api/client";
import type { Run } from "../api/types";
import { useApi } from "../api/useApi";
import { PageHeader } from "../components/AppShell";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { Badge } from "../components/Badge";
import { Card } from "../components/Card";
import { ComparisonResult, pairOf } from "../components/ComparisonResult";
import type { Column } from "../components/DataTable";
import { DataTable } from "../components/DataTable";
import { EmptyState } from "../components/Notice";
import { PRIMARY_METRIC, RUNS_FETCH_LIMIT } from "../config";
import { formatDateTime, formatDecimal, formatInt } from "../lib/format";
import { metricLabel } from "../lib/metrics";

const COLUMNS: readonly Column<Run>[] = [
  { key: "id", header: "번호", numeric: true, render: (r) => r.id, sortValue: (r) => r.id, hideOnMobile: true },
  { key: "experiment", header: "실험", render: (r) => <span className="font-mono text-caption break-all">{r.experiment}</span>, sortValue: (r) => r.experiment, hideOnMobile: true },
  {
    key: "role",
    header: "구분",
    render: (r) => <Badge tone={r.role === "ours" ? "accent" : "neutral"}>{r.role === "ours" ? "비교" : "기준선"}</Badge>,
    sortValue: (r) => r.role,
  },
  { key: "method", header: "방법", render: (r) => r.method, sortValue: (r) => r.method },
  {
    key: "metric",
    header: metricLabel(PRIMARY_METRIC),
    numeric: true,
    render: (r) => {
      const m = r.metrics.point_ci[PRIMARY_METRIC];
      return m === undefined ? "–" : formatDecimal(m.point);
    },
    sortValue: (r) => r.metrics.point_ci[PRIMARY_METRIC]?.point ?? -1,
  },
  { key: "n_test", header: "시험 n", numeric: true, render: (r) => formatInt(r.n_test), sortValue: (r) => r.n_test, hideOnMobile: true },
  { key: "split", header: "분할", render: (r) => (r.split_kind === "temporal" ? "시간순" : "무작위"), sortValue: (r) => r.split_kind, hideOnMobile: true },
  { key: "started", header: "실행 시각", render: (r) => formatDateTime(r.started_at), sortValue: (r) => r.started_at, hideOnMobile: true },
];

export function RunsPage() {
  const cmp = useApi(api.comparison);
  const all = useApi(() => api.runs(RUNS_FETCH_LIMIT));
  return (
    <>
      <PageHeader
        eyebrow="실험 비교"
        title="기준선 대비 성능"
        lead="같은 시험 집합에서 기준선과 비교 방법을 나란히 놓고, 부트스트랩 95% 신뢰구간으로 차이가 우연인지 확인합니다."
      />
      <div className="space-y-8">
        <Card title="최근 실험" caption="가장 최근 실험의 기준선과 비교 방법. 기준선은 항상 회색, 비교는 파랑입니다.">
          <AsyncBoundary state={cmp.state} onRetry={cmp.reload}>
            {(runs) => {
              const pair = pairOf(runs);
              return pair === null ? (
                <EmptyState title="아직 실험 결과가 없습니다">실험을 실행하면 이곳에 기준선과 비교 방법의 결과가 나타납니다.</EmptyState>
              ) : (
                <ComparisonResult pair={pair} />
              );
            }}
          </AsyncBoundary>
        </Card>

        <Card title="전체 실행 기록" caption="열 제목을 눌러 정렬합니다." flush>
          <AsyncBoundary state={all.state} onRetry={all.reload}>
            {(runs) => (
              <DataTable
                caption="실험 실행 기록"
                columns={COLUMNS}
                rows={runs}
                rowKey={(r) => r.id}
                initialSort={{ key: "started", dir: "desc" }}
                emptyTitle="실행 기록이 없습니다"
                emptyHint="실험을 실행하면 기준선과 비교 방법의 기록이 쌓입니다."
              />
            )}
          </AsyncBoundary>
        </Card>
      </div>
    </>
  );
}
