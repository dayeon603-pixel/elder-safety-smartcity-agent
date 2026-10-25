import type { ReactNode } from "react";
import type { ActionsResponse, ActionsSignal } from "../../api/zpTypes";
import { CROSSWALK_MAX_LENGTH_M, ZP_BIT_TOP, ZP_DRAFT_LABEL } from "../../config";
import { Badge } from "../Badge";
import { Card } from "../Card";

function Channel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="break-inside-avoid border-t border-line py-4 first:border-t-0 first:pt-0 last:pb-0">
      <h3 className="text-body font-semibold">{title}</h3>
      <div className="mt-2 space-y-2 text-small">{children}</div>
    </div>
  );
}

function Draft({ label, text, length, limit }: { label: string; text: string; length: number; limit: number }) {
  return (
    <figure className="m-0">
      <figcaption className="flex flex-wrap items-center gap-2 text-caption text-ink-muted">
        {label}
        <span className="num" aria-label={`${length}자, 제한 ${limit}자`}>{length}/{limit}자</span>
      </figcaption>
      <blockquote className="m-0 mt-1 border-l-2 border-line-strong bg-surface-sunken px-3 py-2 text-body">{text}</blockquote>
    </figure>
  );
}

function SignalRow({ s }: { s: ActionsSignal }) {
  return (
    <li>
      <span className="font-medium">{s.address}</span>
      <span className="block text-ink-muted">
        {s.length_m > CROSSWALK_MAX_LENGTH_M ? (
          <>자료 확인 필요: 길이 {s.length_m}m는 횡단보도로 보기 어렵습니다</>
        ) : (
          <>횡단 {s.length_m}m · 녹색 {s.green_s}초 → 필요 {s.required_green_s}초 · <span className="text-accent">+{s.extend_s}초 연장 권고</span></>
        )}
      </span>
    </li>
  );
}

/** The fourth briefing section: drafts for the channels outside CCTV. Nothing here is sent. */
export function ActionChannels({ data }: { data: ActionsResponse }): ReactNode {
  const { cbs, bit, signals, signals_coverage: cov, route } = data;
  const signalList = Array.isArray(signals) ? signals : [];
  const signalNote = Array.isArray(signals) ? cov.note : signals.note;
  return (
    <Card
      title="행동 채널"
      caption="재난문자, 버스정류장 안내단말, 보행신호, 방문팀. 담당자가 검토할 초안입니다."
      id="bp-channels"
      actions={<Badge>{ZP_DRAFT_LABEL}</Badge>}
    >
      <div>
        <Channel title="재난문자 초안">
          <Draft label="문안" text={cbs.text} length={cbs.length} limit={cbs.limit} />
          <Draft label="쉬운 말" text={cbs.easy_text} length={cbs.easy_length} limit={cbs.limit} />
          {cbs.shelter_note != null && <p className="text-ink-muted">{cbs.shelter_note}</p>}
        </Channel>
        <Channel title={`버스정류장 안내단말 상위 ${ZP_BIT_TOP}곳`}>
          {bit.length === 0 ? (
            <p className="text-ink-muted">이 시군구에서 고른 정류장이 없습니다.</p>
          ) : (
            <ol className="m-0 list-decimal space-y-1 pl-5">
              {bit.slice(0, ZP_BIT_TOP).map((b) => (
                <li key={b.stop_id}>
                  <span className="font-medium">{b.name}</span>
                  <span className="text-ink-muted"> · {b.dong} · 반경 300m 65세 이상 약 {b.covered_pop65_est.toLocaleString("ko-KR")}명 · “{b.message.lines.join(" ")}”</span>
                </li>
              ))}
            </ol>
          )}
        </Channel>
        <Channel title="보행신호 연장 권고">
          {signalList.length > 0 ? (
            <ul className="m-0 list-none space-y-2 p-0">{signalList.map((s) => <SignalRow key={s.crosswalk_id} s={s} />)}</ul>
          ) : (
            <p className="text-ink-muted">{signalNote ?? "연장이 필요한 횡단보도가 없습니다."}</p>
          )}
          <p className="text-caption text-ink-muted">판정 {cov.n_evaluable}곳 / 이 시군구 횡단보도 {cov.n_crosswalks_in_region}곳 중 신호 자료가 갖춰진 곳만</p>
        </Channel>
        <Channel title="방문팀 동선">
          {route.stops.length === 0 ? (
            <p className="text-ink-muted">동선을 만들 행정동 자료가 없습니다.</p>
          ) : (
            <>
              <p>
                <span className="num font-semibold">{route.km.toFixed(1)} km</span>
                <span className="text-ink-muted"> ({route.distance_basis}) · {route.stops.length}곳</span>
              </p>
              <ol className="m-0 flex list-none flex-wrap gap-x-2 gap-y-1 p-0" aria-label="방문 순서">
                {route.stops.map((s, i) => (
                  <li key={s.order} className="whitespace-nowrap">
                    {i > 0 && <span aria-hidden="true" className="mr-2 text-ink-faint">→</span>}
                    <span className="font-medium">{s.dong}</span>
                  </li>
                ))}
              </ol>
              {route.note != null && <p className="text-ink-muted">{route.note}</p>}
            </>
          )}
        </Channel>
      </div>
      <p className="mt-4 text-caption text-ink-muted">{data.disclaimer}</p>
    </Card>
  );
}
