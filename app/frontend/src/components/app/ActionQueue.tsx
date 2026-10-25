import { useState } from "react";
import { Link } from "react-router-dom";
import { downloadDocx, playUrl, playVillageBroadcast } from "../../api/opsChannels";
import type { AudioSource, ChannelResult } from "../../api/opsChannels";
import { Badge } from "../Badge";
import { Button } from "../Button";
import { Card } from "../Card";
import { EmptyState } from "../Notice";
import { ActionFeedback } from "../zp/ActionFeedback";
import { OPS_DRAFT_LABEL } from "../../config";
import { ACTOR_LABEL, actorSummary } from "../../lib/app/opsModel";
import type { Channel, Focus, QueueItem } from "../../lib/app/opsModel";

interface Props {
  items: readonly QueueItem[];
  sggCode: string;
  date: string;
  /** null while probing /feedback */
  feedbackOpen: boolean | null;
  focusLabel: string | null;
  onFocus: (f: Focus | null, label: string) => void;
  onSaved: () => void;
  /** what the 마을방송 button reads aloud; null when /actions has not loaded */
  audio: AudioSource | null;
}

function ChannelChip({ c, sggCode, date, audio }: { c: Channel; sggCode: string; date: string; audio: AudioSource | null }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ChannelResult | null>(null);
  if (c.kind === "bit" || c.kind === "cbs") {
    return <span className="inline-flex min-h-9 items-center border border-line bg-surface-sunken px-3 text-small">{c.label} <span className="num ml-1 font-semibold">{c.detail}</span></span>;
  }
  const run = async (): Promise<void> => {
    setBusy(true);
    setResult(null);
    const r = c.kind === "gongmun" && c.docId !== null ? await downloadDocx(c.docId, sggCode, date) : c.kind === "audio" && audio !== null ? await playVillageBroadcast(audio, playUrl) : ({ kind: "failed", reason: "준비 중" } as ChannelResult);
    setResult(r);
    setBusy(false);
  };
  const usable = c.available && (c.kind !== "audio" || audio !== null);
  return (
    <span className="inline-flex flex-col">
      <Button variant="secondary" disabled={!usable} loading={busy} onClick={() => void run()} className="!min-h-11 !px-3 !text-small sm:!min-h-9">
        {c.label}{usable ? "" : " · 준비 중"}
      </Button>
      <span role="status" className="text-caption">
        {result?.kind === "ok" && <span className="text-success">{result.note}</span>}
        {result?.kind === "failed" && <span className="text-danger">실패: {result.reason}</span>}
      </span>
    </span>
  );
}

/** 오늘의 인프라 조치: every row carries its numbers (근거) and a 담당자 decision. Nothing is sent from here. */
export function ActionQueue({ items, sggCode, date, feedbackOpen, focusLabel, onFocus, onSaved, audio }: Props) {
  const sum = actorSummary(items);
  return (
    <Card title="오늘의 인프라 조치" caption="AI는 추천, 결정은 담당자. 채택 · 보류 · 부적합을 누르면 기록됩니다. 발송은 하지 않습니다." id="ops-queue">
      {sum.total > 0 && (
        <p className="mb-5 border border-line bg-surface-sunken px-3 py-2 text-small" data-testid="actor-summary">
          오늘 조치 <span className="num font-semibold">{sum.total}</span>건 중 <span className="num font-semibold">{sum.none}</span>건은 고령자가 아무것도 하지 않아도 작동합니다.
          {sum.phone > 0 && <> <span className="num font-semibold">{sum.phone}</span>건은 휴대폰 기본 수신(재난문자, 앱 설치 없음)입니다.</>}
          <span className="mt-1 block text-caption text-ink-muted">보호자는 고령자 화면(<Link to="/citizen">/citizen</Link>)을 웹으로 열어 같은 내용을 볼 수 있습니다.</span>
        </p>
      )}
      {items.length === 0 ? (
        <EmptyState title="조치 제안이 없습니다">이 시군구에서 불러온 자료로는 제안할 항목이 없습니다.</EmptyState>
      ) : (
        <ol className="m-0 list-none space-y-5 p-0">
          {items.map((it, idx) => (
            <li key={it.id} data-testid={`queue-${it.id}`} className="border-t border-line pt-5 first:border-t-0 first:pt-0">
              <p className="flex flex-wrap items-center gap-2">
                <span className="num text-small font-semibold text-ink-faint">{idx + 1}</span>
                <Badge tone="accent">{it.tag}</Badge>
                {it.decidable && <Badge tone={it.actor === "none" ? "success" : "neutral"}>{ACTOR_LABEL[it.actor]}</Badge>}
              </p>
              <h3 className="mt-1 text-lead font-semibold">{it.title}</h3>
              {it.lines.length > 0 && (
                <ul className="m-0 mt-2 list-none space-y-1 p-0 text-small text-ink-muted">
                  {it.lines.map((l) => <li key={l} className="break-words border-l-2 border-line-strong pl-3">{l}</li>)}
                </ul>
              )}
              <details className="mt-2 text-small">
                <summary className="inline-flex min-h-11 cursor-pointer items-center font-medium text-accent sm:min-h-9">근거</summary>
                <dl className="m-0 mt-1 grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 border-l-2 border-line-strong pl-3">
                  {it.evidence.map((r) => (
                    <div key={r.label} className="contents">
                      <dt className="text-ink-muted">{r.label}</dt>
                      <dd className="num m-0 text-right font-medium">{r.value}</dd>
                    </div>
                  ))}
                </dl>
                <p className="mt-1 border-l-2 border-line-strong pl-3 text-caption text-ink-muted">출처: {it.source}</p>
                <p className="mt-1 pl-3 text-small">
                  {it.evidenceLink.external === true ? (
                    <a href={it.evidenceLink.to} target="_blank" rel="noreferrer noopener">{it.evidenceLink.label}</a>
                  ) : (
                    <Link to={it.evidenceLink.to}>근거 화면: {it.evidenceLink.label} →</Link>
                  )}
                </p>
              </details>
              {it.channels.length > 0 && (
                <div className="mt-3" data-testid="channels">
                  <p className="text-caption font-medium text-ink-muted">전달 경로 · {OPS_DRAFT_LABEL}</p>
                  <ul className="m-0 mt-1 flex list-none flex-wrap gap-2 p-0">
                    {it.channels.map((c) => (
                      <li key={c.kind}><ChannelChip c={c} sggCode={sggCode} date={date} audio={audio} /></li>
                    ))}
                  </ul>
                </div>
              )}
              {it.focus !== null && (
                <Button variant="ghost" className="mt-1 !min-h-11 !px-0 !text-small text-accent sm:!min-h-9" aria-pressed={focusLabel === it.id} onClick={() => onFocus(focusLabel === it.id ? null : it.focus, it.id)}>
                  {focusLabel === it.id ? "지도 표시 끄기" : "지도에서 위치 보기"}
                </Button>
              )}
              {it.decidable && <ActionFeedback sggCode={sggCode} date={date} actionId={it.id} available={feedbackOpen} onSaved={onSaved} />}
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
