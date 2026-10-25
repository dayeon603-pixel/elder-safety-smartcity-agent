/** One-second-less silent WAV; playing it inside the tap handler unlocks this element for later programmatic play (iOS/Chrome autoplay policy). */
const SILENT_WAV = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";

export type VoiceState = "idle" | "playing" | "blocked" | "failed";

/** Plays the backend WAV on one reused <audio> element. Falls back to speechSynthesis when a message has no audio_url. */
export class VoicePlayer {
  private readonly el: HTMLAudioElement;
  private onState: (s: VoiceState) => void = () => undefined;

  constructor(createAudio: () => HTMLAudioElement = () => new Audio()) {
    this.el = createAudio();
    this.el.onended = () => this.onState("idle");
    this.el.onerror = () => this.onState("failed");
  }

  subscribe(fn: (s: VoiceState) => void): void {
    this.onState = fn;
  }

  /** Call from a user gesture. */
  async unlock(): Promise<boolean> {
    this.el.src = SILENT_WAV;
    try {
      await this.el.play();
      return true;
    } catch (err) {
      if (err instanceof DOMException) return false;
      throw err;
    }
  }

  async play(url: string | null, fallbackText: string): Promise<void> {
    this.cancel();
    if (url === null) {
      this.speak(fallbackText);
      return;
    }
    this.el.src = url;
    try {
      await this.el.play();
      this.onState("playing");
    } catch (err) {
      if (!(err instanceof DOMException)) throw err;
      this.onState(err.name === "NotAllowedError" ? "blocked" : "failed");
    }
  }

  cancel(): void {
    this.el.pause();
    if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
    this.onState("idle");
  }

  private speak(text: string): void {
    if (typeof speechSynthesis === "undefined" || typeof SpeechSynthesisUtterance === "undefined") return;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "ko-KR";
    speechSynthesis.speak(u);
    this.onState("playing");
  }
}
