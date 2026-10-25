/** Thin Web Speech API wrapper (speechSynthesis, ko-KR). Local to the browser; nothing is sent by this code. */
const LANG = "ko-KR";

export type SpeechResult = "spoken" | "muted" | "unsupported";

export function speechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";
}

export function speak(text: string, muted: boolean): SpeechResult {
  if (muted) return "muted";
  if (!speechSupported()) return "unsupported";
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = LANG;
  window.speechSynthesis.speak(u);
  return "spoken";
}

export function stopSpeaking(): void {
  if (speechSupported()) window.speechSynthesis.cancel();
}

export const SPEECH_RESULT_LABEL: Record<SpeechResult, string> = {
  spoken: "스피커 재생 요청함",
  muted: "음소거 상태라 재생하지 않음",
  unsupported: "이 브라우저는 음성 합성을 지원하지 않아 문구만 표시",
};
