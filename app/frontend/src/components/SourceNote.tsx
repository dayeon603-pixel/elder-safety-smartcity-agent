import { useState } from "react";
import { api } from "../api/client";
import type { Dataset } from "../api/types";
import { useApi } from "../api/useApi";
import { formatDate, formatInt, publisherOf, shortHash } from "../lib/format";

interface SourceNoteProps {
  /** Dataset to describe. Omit `dataset` and give `datasetId`/`datasetName` to look it up from /api/datasets. */
  dataset?: Dataset;
  datasetId?: number | null;
  datasetName?: string;
}

function Provenance({ d }: { d: Dataset }) {
  const [copied, setCopied] = useState(false);
  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(d.sha256);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      if (!(err instanceof DOMException)) throw err; // clipboard denied: hash is still selectable in title
    }
  };
  return (
    <p className="text-caption text-ink-muted">
      <span className="font-medium text-ink">출처</span>{" "}
      <a href={d.source_url} target="_blank" rel="noreferrer noopener">
        {d.name}
      </a>{" "}
      · 제공 {publisherOf(d.source_url)} · 라이선스 {d.license} · {formatInt(d.row_count)}행 · 수집 {formatDate(d.fetched_at)} · SHA-256{" "}
      <code title={d.sha256} className="bg-surface-inset px-1">
        {shortHash(d.sha256)}
      </code>{" "}
      <button
        type="button"
        onClick={() => void copy()}
        className="text-accent underline underline-offset-2 hover:text-accent-strong"
        aria-label="SHA-256 전체 값 복사"
      >
        {copied ? "복사됨" : "복사"}
      </button>
    </p>
  );
}

function Lookup({ id, name }: { id?: number | null; name?: string }) {
  const { state } = useApi(api.datasets);
  if (state.status === "loading") return <p className="text-caption text-ink-faint">출처 정보를 불러오는 중</p>;
  if (state.status === "error") return <p className="text-caption text-ink-faint">출처 정보를 불러오지 못했습니다.</p>;
  const hit = state.data.find((d) => (id != null ? d.id === id : d.name === name));
  if (hit === undefined) return <p className="text-caption text-ink-faint">이 결과에 연결된 데이터셋 출처가 기록되어 있지 않습니다.</p>;
  return <Provenance d={hit} />;
}

/** Dataset provenance line: publisher, license, short SHA-256. Required under every figure that uses external data. */
export function SourceNote({ dataset, datasetId, datasetName }: SourceNoteProps) {
  return (
    <div className="border-t border-line pt-3">
      {dataset !== undefined ? <Provenance d={dataset} /> : <Lookup id={datasetId} name={datasetName} />}
    </div>
  );
}
