/** Variable-message-sign mock. Text is a template filled with real values; nothing is sent to a real sign. */
export function VmsMock({ active, text, idleText }: { active: boolean; text: string; idleText: string }) {
  return (
    <div>
      <p className="text-caption text-ink-faint">전광판(VMS) 모형 · 실제 전광판에 전송하지 않음</p>
      <div role="status" aria-label="전광판" className="mt-1 flex min-h-[4.5rem] items-center justify-center border-2 border-ink bg-ink px-3 py-3 text-center">
        <span className={`font-semibold ${active ? "text-[#ffb000]" : "text-[#8a919c]"}`} data-testid="vms-text">{active ? text : idleText}</span>
      </div>
    </div>
  );
}
