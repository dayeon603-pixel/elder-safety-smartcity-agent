import { useEffect, useId, useState } from "react";
import {
  FIELD_PLAYBACK_DEFAULT, FIELD_PLAYBACK_RATES, FIELD_SPEED_DEFAULT, FIELD_SPEED_MAX, FIELD_SPEED_MIN, FIELD_SPEED_STEP,
} from "../../config";
import { assessCrossing, formatMeters, greenRemaining, HEAT_STAGE_LABEL, pedestrianPosition, vmsText } from "../../lib/field";
import type { Crosswalk, HeatInfo } from "../../lib/field";
import { usePrefersReducedMotion } from "../../lib/useReducedMotion";
import { Badge } from "../Badge";
import { Button } from "../Button";
import { Card } from "../Card";
import { SelectField } from "../Field";
import { AgentLog } from "./AgentLog";
import type { LogEntry } from "./AgentLog";
import { CrosswalkScene } from "./CrosswalkScene";
import { VmsMock } from "./VmsMock";
import { useSimClock } from "./useSimClock";

const TAIL_SEC = 3;
const f1 = (v: number): string => v.toFixed(1);

interface Props {
  crosswalk: Crosswalk;
  heat: HeatInfo;
  onStage: (level: 0 | 1 | 2) => void;
}

export function ScenarioA({ crosswalk, heat, onStage }: Props) {
  const reduced = usePrefersReducedMotion();
  const [speed, setSpeed] = useState(FIELD_SPEED_DEFAULT);
  const [startRemaining, setStartRemaining] = useState<number | null>(null);
  const [rate, setRate] = useState(FIELD_PLAYBACK_DEFAULT);
  const [applyExt, setApplyExt] = useState(false);
  const speedId = useId();
  const startId = useId();

  const startBase = Math.min(startRemaining ?? crosswalk.greenSec, crosswalk.greenSec);
  const plan = assessCrossing(crosswalk.lengthM, speed, startBase);
  const green0 = startBase + (applyExt ? plan.extensionSec : 0);
  const eff = assessCrossing(crosswalk.lengthM, speed, green0);
  const maxT = Math.max(eff.requiredSec, green0) + TAIL_SEC;
  const clock = useSimClock(maxT, rate);
  const started = clock.playing || clock.t > 0;
  const t = clock.t;
  const remaining = greenRemaining(green0, t);
  const pos = pedestrianPosition(crosswalk.lengthM, speed, t);
  const greenEndM = eff.finishes ? null : speed * green0;
  const stuck = remaining === 0 && pos < crosswalk.lengthM;
  const warn = started && !plan.finishes;

  const level: 0 | 1 | 2 = !started ? 0 : warn ? 2 : 1;

  const entries: LogEntry[] = [];
  if (started) {
    entries.push({
      id: "a", tag: "(a)", title: "폭염 단계",
      lines: [heat.source === "api" && heat.tmaxC !== null ? `${heat.date} ${heat.regionName} 최고기온 ${f1(heat.tmaxC)}℃ → ${HEAT_STAGE_LABEL[heat.stage]}` : `직접 선택: ${HEAT_STAGE_LABEL[heat.stage]}`],
    });
    entries.push({
      id: "b", tag: "(b)", title: "예상 횡단 시간과 남은 녹색",
      lines: [
        `예상 횡단 시간 = 길이 ÷ 속도 = ${f1(crosswalk.lengthM)} m ÷ ${speed.toFixed(2)} m/s = ${f1(plan.requiredSec)} 초`,
        `남은 녹색시간 = ${f1(startBase)} 초 (횡단 시작 시점)`,
      ],
    });
    entries.push(
      plan.finishes
        ? { id: "c", tag: "(c)", title: "판정: 녹색 안에 건넘", lines: [`${f1(plan.requiredSec)} ≤ ${f1(startBase)} → 경고 없음`], tone: "success" }
        : {
            id: "c", tag: "(c)", title: "판정: 녹색 안에 못 건넘 → 전광판 경고 + 신호 연장 권고",
            lines: [`부족 = ${f1(plan.requiredSec)} − ${f1(startBase)} = ${f1(plan.deficitSec)} 초`, `권고 = 올림(${f1(plan.deficitSec)}) = +${plan.extensionSec} 초`],
            tone: "danger",
          },
    );
    if (t > 0 && remaining === 0) {
      entries.push({
        id: "d", tag: "(d)", title: "녹색 종료 시점",
        lines: [stuck ? `보행자 ${f1(pos)} m / ${f1(crosswalk.lengthM)} m, 남은 ${f1(crosswalk.lengthM - pos)} m가 도로 위` : `횡단 완료 (${f1(pos)} m)`],
        tone: stuck ? "danger" : "success",
      });
    }
  }

  return (
    <Card
      id="scenario-a"
      title="신호 시간 부족 경고 (시뮬레이션)"
      caption="속도는 가정값이고 길이·녹색시간·차로수는 실제 자료입니다. 0.7 m/s는 노인 보행속도 연구값을 가정값으로 쓴 것입니다."
      actions={<Badge>시뮬레이션</Badge>}
    >
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_22rem]">
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor={speedId} className="block text-small font-medium">보행 속도 <span className="num font-semibold">{speed.toFixed(2)} m/s</span></label>
              <input id={speedId} type="range" min={FIELD_SPEED_MIN} max={FIELD_SPEED_MAX} step={FIELD_SPEED_STEP} value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="mt-1 h-11 w-full accent-accent sm:h-8" />
            </div>
            <div>
              <label htmlFor={startId} className="block text-small font-medium">횡단 시작 시 남은 녹색 <span className="num font-semibold">{f1(startBase)} 초</span></label>
              <input id={startId} type="range" min={1} max={crosswalk.greenSec} step={1} value={startBase} onChange={(e) => setStartRemaining(Number(e.target.value))} className="mt-1 h-11 w-full accent-accent sm:h-8" />
            </div>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            {clock.playing ? (
              <Button variant="primary" onClick={clock.pause}>일시정지</Button>
            ) : (
              <Button variant="primary" onClick={clock.play} disabled={reduced} title={reduced ? "움직임 줄이기 설정이 켜져 있어 자동 재생을 끕니다" : undefined}>시작</Button>
            )}
            <Button variant="secondary" onClick={clock.reset}>처음으로</Button>
            <label className="flex min-h-11 items-center gap-2 text-small sm:min-h-10">
              <input type="checkbox" checked={applyExt} onChange={(e) => { setApplyExt(e.target.checked); clock.reset(); }} className="h-4 w-4 accent-accent" />
              신호 연장 권고 적용
            </label>
            <div className="w-28">
              <SelectField label="재생 배속" value={String(rate)} onChange={(e) => setRate(Number(e.target.value))} options={FIELD_PLAYBACK_RATES.map((r) => ({ value: String(r), label: `${r}배` }))} />
            </div>
          </div>
          {reduced && <p className="text-small text-ink-muted">움직임 줄이기 설정에 따라 자동 재생을 끄고, 아래 슬라이더로 시간을 직접 옮깁니다.</p>}
          <CrosswalkScene lengthM={crosswalk.lengthM} lanes={crosswalk.lanes} positionM={pos} greenEndM={greenEndM} signalGreen={remaining > 0} />
          <div>
            <label htmlFor={`${speedId}-t`} className="block text-small font-medium">경과 시간 <span className="num font-semibold">{f1(t)} 초</span></label>
            <input id={`${speedId}-t`} type="range" min={0} max={maxT} step={0.1} value={t} onChange={(e) => clock.seek(Number(e.target.value))} className="h-11 w-full accent-accent sm:h-8" />
          </div>
          <dl className="grid grid-cols-3 gap-3 text-small">
            <div><dt className="text-caption text-ink-faint">신호</dt><dd className="num font-semibold">{remaining > 0 ? `녹색 ${f1(remaining)} 초` : "적색"}</dd></div>
            <div><dt className="text-caption text-ink-faint">보행자</dt><dd className="num font-semibold">{f1(pos)} / {formatMeters(crosswalk.lengthM)}</dd></div>
            <div><dt className="text-caption text-ink-faint">상태</dt><dd className="font-semibold">{!started ? "대기" : pos >= crosswalk.lengthM ? "횡단 완료" : stuck ? "도로 위에 남음" : "횡단 중"}</dd></div>
          </dl>
          <div className="grid gap-4 sm:grid-cols-2">
            <VmsMock active={warn} text={vmsText(heat.stage)} idleText={started ? "표시 없음" : "대기"} />
            <div className={`border px-4 py-3 ${warn ? "border-danger/40 bg-danger-wash" : "border-line bg-surface-sunken"}`} aria-live="polite">
              <p className="text-caption text-ink-faint">행동 카드</p>
              {warn ? (
                <>
                  <p className="text-body font-semibold text-danger">신호 연장 +{plan.extensionSec}초 권고</p>
                  <p className="num text-small text-ink-muted">부족 {f1(plan.deficitSec)} 초를 올림. 권고일 뿐 신호기에는 전송하지 않습니다.</p>
                </>
              ) : (
                <p className="text-small text-ink-muted">{started ? "권고 없음" : "시작을 누르면 엔진이 판정합니다."}</p>
              )}
            </div>
          </div>
        </div>
        <AgentLog entries={entries} emptyText="시작을 누르면 엔진이 단계별로 판정합니다." />
      </div>
      <StageReporter level={level} onStage={onStage} />
    </Card>
  );
}


/** Tells the page how far the engine got so the pipeline strip can light up. */
function StageReporter({ level, onStage }: { level: 0 | 1 | 2; onStage: (l: 0 | 1 | 2) => void }) {
  useEffect(() => {
    onStage(level);
  }, [level, onStage]);
  return null;
}
