import { useState } from "react";
import { DECISIONS, NOTE_MAX_CHARS, postFeedback } from "../../api/feedback";
import type { Decision } from "../../api/feedback";
import { Button } from "../Button";

interface Props {
  sggCode: string;
  date: string;
  actionId: string;
  /** null while probing; false = backend has no /feedback yet. */
  available: boolean | null;
  onSaved: () => void;
}

type Saved = { kind: "idle" } | { kind: "saving" } | { kind: "saved"; decision: Decision } | { kind: "failed"; reason: string };

/** 채택 / 보류 / 부적합 for one recommended action. Nothing is shown as recorded until the server accepted it. */
export function ActionFeedback({ sggCode, date, actionId, available, onSaved }: Props) {
  const [note, setNote] = useState("");
  const [state, setState] = useState<Saved>({ kind: "idle" });
  const disabled = available !== true || state.kind === "saving";

  const send = async (decision: Decision): Promise<void> => {
    setState({ kind: "saving" });
    const trimmed = note.trim();
    const res = await postFeedback({ sgg_code: sggCode, date, action_id: actionId, decision, ...(trimmed === "" ? {} : { note: trimmed }) });
    if (res.kind === "saved") {
      setState({ kind: "saved", decision });
      onSaved();
    } else {
      setState({ kind: "failed", reason: res.reason });
    }
  };

  return (
    <div className="mt-2 print:hidden">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="담당자 판단">
        {DECISIONS.map((d) => (
          <Button key={d} variant="secondary" disabled={disabled} aria-pressed={state.kind === "saved" && state.decision === d} onClick={() => void send(d)} className="!min-h-9 !px-3 !text-small">
            {d}
          </Button>
        ))}
        {available === false && <span className="text-caption text-ink-muted">준비 중 (서버에 기록 창구가 아직 없습니다)</span>}
      </div>
      {available === true && (
        <label className="mt-2 block text-caption text-ink-muted">
          메모(선택)
          <input
            type="text"
            value={note}
            maxLength={NOTE_MAX_CHARS}
            disabled={state.kind === "saving" || state.kind === "saved"}
            onChange={(e) => setNote(e.target.value)}
            className="mt-1 block min-h-10 w-full max-w-xs border border-line-strong bg-surface px-3 text-small text-ink"
          />
        </label>
      )}
      <p className="mt-1 text-caption" role="status">
        {state.kind === "saved" && <span className="text-success">현장 판단 기록됨 · 다음 학습에 반영 예정</span>}
        {state.kind === "failed" && <span className="text-danger">기록하지 못했습니다: {state.reason}</span>}
      </p>
    </div>
  );
}
