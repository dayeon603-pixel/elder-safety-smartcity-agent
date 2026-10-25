import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, describeError } from "../api/client";
import type { Dataset, IngestRequest } from "../api/types";
import { useApi } from "../api/useApi";
import { useAuth } from "../auth/AuthContext";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { SelectField, TextAreaField, TextField } from "../components/Field";
import { Modal } from "../components/Modal";
import { EmptyState, Notice } from "../components/Notice";
import { SourceNote } from "../components/SourceNote";
import { useToast } from "../components/Toast";
import { formatBytes } from "../lib/format";
import {
  MAX_DESCRIPTION_LEN, suggestDatasetName, validateColumn, validateDatasetName,
  validateDescription, validateLicense, validateSourceUrl,
} from "../lib/validate";

type FormKey = "file" | "name" | "source_url" | "license" | "description" | "id_column" | "time_column" | "label_column" | "group_column";
type Form = Record<FormKey, string>;
type Errors = Partial<Record<FormKey, string | null>>;

const EMPTY: Form = {
  file: "", name: "", source_url: "", license: "", description: "",
  id_column: "", time_column: "", label_column: "", group_column: "",
};

const RULES: Record<FormKey, (v: string) => string | null> = {
  file: (v) => (v === "" ? "수집할 파일을 선택해 주세요." : null),
  name: validateDatasetName,
  source_url: validateSourceUrl,
  license: validateLicense,
  description: validateDescription,
  id_column: validateColumn,
  time_column: validateColumn,
  label_column: validateColumn,
  group_column: validateColumn,
};

const ORDER = Object.keys(RULES) as FormKey[];
const orNull = (v: string): string | null => (v.trim() === "" ? null : v.trim());

function toRequest(f: Form): IngestRequest {
  return {
    file_name: f.file, name: f.name.trim(), source_url: f.source_url.trim(), license: f.license.trim(),
    description: f.description.trim(), id_column: orNull(f.id_column), time_column: orNull(f.time_column),
    label_column: orNull(f.label_column), group_column: orNull(f.group_column),
  };
}

/** Ingest a CSV already in the server's data folder; the server hashes the exact bytes. */
export function IngestCard({ onIngested }: { onIngested: () => void }) {
  const { session } = useAuth();
  const location = useLocation();
  const toast = useToast();
  const files = useApi(async () => (session === null ? [] : api.availableFiles()));
  const [form, setForm] = useState<Form>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [result, setResult] = useState<{ created: boolean; dataset: Dataset } | null>(null);

  const fileOptions = useMemo(
    () => (files.state.status === "ready" ? files.state.data.map((f) => ({ value: f.file_name, label: `${f.file_name} (${formatBytes(f.size_bytes)})` })) : []),
    [files.state],
  );

  const set = (k: FormKey, v: string): void => {
    setForm((f) => {
      const next = { ...f, [k]: v };
      if (k === "file" && f.name === "") next.name = suggestDatasetName(v);
      return next;
    });
  };
  const blur = (k: FormKey) => (): void => setErrors((e) => ({ ...e, [k]: RULES[k](form[k]) }));

  const review = (ev: FormEvent): void => {
    ev.preventDefault();
    const next: Errors = Object.fromEntries(ORDER.map((k) => [k, RULES[k](form[k])]));
    setErrors(next);
    const bad = ORDER.find((k) => next[k]);
    if (bad !== undefined) {
      document.getElementById(`ingest-${bad}`)?.focus();
      return;
    }
    setServerError(null);
    setConfirming(true);
  };

  const run = async (): Promise<void> => {
    setBusy(true);
    try {
      const out = await api.ingest(toRequest(form));
      setResult(out);
      setConfirming(false);
      onIngested();
      toast.show(out.created ? `${out.dataset.name} 데이터셋을 수집했습니다.` : "같은 내용의 데이터셋이 이미 있어 새로 만들지 않았습니다.", out.created ? "success" : "info");
      if (out.created) setForm(EMPTY);
    } catch (err) {
      setConfirming(false);
      setServerError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  if (session === null) {
    return (
      <Card title="데이터 수집" caption="서버의 데이터 폴더에 있는 CSV를 출처와 함께 등록합니다.">
        <EmptyState
          title="로그인하면 데이터를 수집할 수 있습니다"
          action={
            <div className="flex gap-2">
              <Link to="/login" state={{ from: location.pathname }} className="inline-flex min-h-10 items-center border border-accent bg-accent px-4 font-medium text-white no-underline hover:bg-accent-strong hover:text-white">
                로그인
              </Link>
              <Link to="/register" state={{ from: location.pathname }} className="inline-flex min-h-10 items-center border border-line-strong px-4 font-medium no-underline">
                회원가입
              </Link>
            </div>
          }
        >
          수집은 감사 기록에 남는 쓰기 작업이라 계정이 필요합니다. 목록 조회는 로그인 없이 가능합니다.
        </EmptyState>
      </Card>
    );
  }

  return (
    <Card
      title="데이터 수집"
      caption="서버의 데이터 폴더에 있는 CSV를 출처와 함께 등록합니다. 출처와 라이선스는 입력한 대로 기록되며 서버가 검증하지 않습니다. SHA-256만 서버가 파일에서 직접 계산합니다."
      id="ingest"
    >
      <AsyncBoundary state={files.state} onRetry={files.reload}>
        {(list) =>
          list.length === 0 ? (
            <EmptyState title="수집할 CSV 파일이 없습니다" action={<Button onClick={files.reload}>목록 새로고침</Button>}>
              서버의 데이터 폴더(<code>data/cache</code>)에 CSV 파일을 넣은 뒤 새로고침하세요. 폴더 바로 아래의 .csv 파일만 표시됩니다.
            </EmptyState>
          ) : (
            <form onSubmit={review} noValidate className="space-y-5">
              {serverError !== null && <Notice tone="danger">{serverError}</Notice>}
              <div className="grid gap-5 md:grid-cols-2">
                <SelectField
                  id="ingest-file" label="파일" required options={fileOptions} placeholder="파일을 선택하세요"
                  value={form.file} onChange={(e) => set("file", e.target.value)} onBlur={blur("file")} error={errors.file}
                />
                <TextField
                  id="ingest-name" label="데이터셋 이름" required value={form.name} autoComplete="off" spellCheck={false}
                  hint="영문·숫자·_ . - , 64자 이내" onChange={(e) => set("name", e.target.value)} onBlur={blur("name")} error={errors.name}
                />
                <TextField
                  id="ingest-source_url" label="출처 URL" required type="url" inputMode="url" value={form.source_url} autoComplete="off"
                  hint="원본을 받은 페이지 주소" onChange={(e) => set("source_url", e.target.value)} onBlur={blur("source_url")} error={errors.source_url}
                />
                <TextField
                  id="ingest-license" label="라이선스" required value={form.license} autoComplete="off" hint="예: CC BY 4.0, 공공누리 1유형"
                  onChange={(e) => set("license", e.target.value)} onBlur={blur("license")} error={errors.license}
                />
              </div>
              <TextAreaField
                id="ingest-description" label="설명" required={false} optionalText="(선택)" value={form.description}
                hint={`${form.description.length} / ${MAX_DESCRIPTION_LEN}자`}
                onChange={(e) => set("description", e.target.value)} onBlur={blur("description")} error={errors.description}
              />
              <details className="border border-line px-4 py-3">
                <summary className="cursor-pointer text-small font-medium">열 지정 (선택)</summary>
                <p className="mt-2 text-caption text-ink-faint">CSV 첫 줄의 열 이름 그대로 입력합니다. 비워 두면 행 번호를 ID로 씁니다.</p>
                <div className="mt-4 grid gap-5 sm:grid-cols-2">
                  {(
                    [["id_column", "ID 열"], ["time_column", "시간 열"], ["label_column", "정답(라벨) 열"], ["group_column", "그룹 열"]] as const
                  ).map(([k, label]) => (
                    <TextField
                      key={k} id={`ingest-${k}`} label={label} value={form[k]} autoComplete="off" spellCheck={false}
                      onChange={(e) => set(k, e.target.value)} onBlur={blur(k)} error={errors[k]}
                    />
                  ))}
                </div>
              </details>
              <Button type="submit" variant="primary" onMouseDown={(e) => e.preventDefault()}>수집 내용 확인</Button>
            </form>
          )
        }
      </AsyncBoundary>

      {result !== null && (
        <div className="mt-6 space-y-3" role="status">
          <Notice tone="success">{result.created ? "수집했습니다. 기록된 출처는 아래와 같습니다." : "이미 같은 내용이 등록되어 있습니다. 기록된 출처는 아래와 같습니다."}</Notice>
          <SourceNote dataset={result.dataset} />
        </div>
      )}

      <Modal
        open={confirming}
        title="이 내용으로 수집할까요?"
        onClose={() => (busy ? undefined : setConfirming(false))}
        footer={
          <>
            <Button onClick={() => setConfirming(false)} disabled={busy}>취소</Button>
            <Button variant="primary" loading={busy} onClick={() => void run()} data-autofocus>수집</Button>
          </>
        }
      >
        <dl className="m-0 space-y-3 text-small">
          {[["파일", form.file], ["이름", form.name.trim()], ["출처", form.source_url.trim()], ["라이선스", form.license.trim()]].map(([k, v]) => (
            <div key={k} className="grid grid-cols-[5rem_1fr] gap-2">
              <dt className="text-ink-muted">{k}</dt>
              <dd className="m-0 break-all">{v}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 text-caption text-ink-faint">수집하면 감사 기록에 남고, 같은 이름의 다른 내용으로는 덮어쓸 수 없습니다.</p>
      </Modal>
    </Card>
  );
}
