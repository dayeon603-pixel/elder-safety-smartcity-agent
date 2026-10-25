import { useState } from "react";
import { fetchDocument } from "../../api/documents";
import { Button } from "../Button";

type State = { kind: "idle" } | { kind: "busy" } | { kind: "failed"; reason: string };

/** 공문 초안 download for one recommended action. A draft for the 담당자 to edit; nothing is sent. */
export function DocumentLink({ sggCode, date, actionId }: { sggCode: string; date: string; actionId: string | null }) {
  const [state, setState] = useState<State>({ kind: "idle" });
  if (actionId === null) return null;
  const download = async (): Promise<void> => {
    setState({ kind: "busy" });
    const res = await fetchDocument(actionId, sggCode, date);
    if (res.kind === "failed") {
      setState({ kind: "failed", reason: res.reason });
      return;
    }
    const url = URL.createObjectURL(res.blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = res.filename;
    a.click();
    URL.revokeObjectURL(url);
    setState({ kind: "idle" });
  };
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 print:hidden">
      <Button variant="ghost" loading={state.kind === "busy"} onClick={() => void download()} className="!min-h-9 !px-3 !text-small">공문 초안 내려받기 (.docx)</Button>
      {state.kind === "failed" && <span className="text-caption text-ink-muted" role="status">{state.reason}</span>}
    </div>
  );
}
