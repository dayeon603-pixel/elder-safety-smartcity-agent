import { api } from "../api/client";
import type { Dataset } from "../api/types";
import { useApi } from "../api/useApi";
import { PageHeader } from "../components/AppShell";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { Badge } from "../components/Badge";
import { Card } from "../components/Card";
import type { Column } from "../components/DataTable";
import { DataTable } from "../components/DataTable";
import { FOOTER_DATA_CREDITS, HIDDEN_DATASET_NAMES } from "../config";
import { formatDate, formatInt, publisherOf, shortHash } from "../lib/format";
import { IngestCard } from "./IngestCard";

const COLUMNS: readonly Column<Dataset>[] = [
  { key: "name", header: "이름", render: (d) => (
      <span className="font-medium">
        {d.name}
        {d.status !== undefined && d.status !== "complete" && <span className="ml-2"><Badge tone="danger">{d.status === "loading" ? "적재 중" : d.status}</Badge></span>}
      </span>
    ), sortValue: (d) => d.name },
  {
    key: "publisher",
    header: "제공처",
    render: (d) => (
      <a href={d.source_url} target="_blank" rel="noreferrer noopener" className="break-all">
        {publisherOf(d.source_url)}
      </a>
    ),
    sortValue: (d) => publisherOf(d.source_url),
  },
  { key: "license", header: "라이선스", render: (d) => d.license, sortValue: (d) => d.license },
  { key: "rows", header: "행 수", numeric: true, render: (d) => formatInt(d.row_count), sortValue: (d) => d.row_count },
  {
    key: "sha",
    header: "SHA-256",
    render: (d) => <code title={d.sha256}>{shortHash(d.sha256)}</code>,
    hideOnMobile: true,
  },
  { key: "fetched", header: "수집일", render: (d) => formatDate(d.fetched_at), sortValue: (d) => d.fetched_at, hideOnMobile: true },
];

export function DataPage() {
  const list = useApi(api.datasets);
  return (
    <>
      <PageHeader
        eyebrow="데이터"
        title="데이터와 출처"
        lead="사용한 모든 데이터는 제공처, 라이선스, 사용한 파일 바이트의 SHA-256과 함께 기록됩니다. 제공처와 라이선스는 등록자가 입력한 값이고, SHA-256은 서버가 파일 바이트로 직접 계산합니다."
      />
      <div className="space-y-8">
        <Card title="사용한 자료" caption="이 서비스의 결과에 쓰인 공개 자료와 라이선스 표기입니다.">
          <ul className="m-0 space-y-1 pl-5 text-body">
            {FOOTER_DATA_CREDITS.map((c) => (
              <li key={c.href}><a href={c.href} target="_blank" rel="noreferrer noopener">{c.label}</a></li>
            ))}
          </ul>
        </Card>
        <Card title="수집한 데이터셋" flush>
          <AsyncBoundary state={list.state} onRetry={list.reload}>
            {(rows) => (
              <DataTable
                caption="수집한 데이터셋 목록"
                columns={COLUMNS}
                rows={rows.filter((d) => !HIDDEN_DATASET_NAMES.includes(d.name))}
                rowKey={(d) => d.id}
                emptyTitle="수집한 데이터셋이 없습니다"
                emptyHint="아래에서 데이터 폴더의 CSV 파일을 수집하면 이 목록에 나타납니다."
              />
            )}
          </AsyncBoundary>
        </Card>
        <IngestCard onIngested={list.reload} />
      </div>
    </>
  );
}
