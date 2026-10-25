/**
 * Generated alert tones (Web Audio oscillators). No OS sound files are copied or imitated sample-for-sample;
 * the patterns only echo the idea that louder classes sound more insistent. Must be unlocked inside a user tap.
 */
import type { AlertClass } from "./alertClass";

interface Note { f: number; f2?: number; t: number; d: number }

/** Tone peak gain (0..1). Kept moderate: this is a demo in a hall, not a siren. */
const PEAK_GAIN = 0.22;
const ATTACK_S = 0.01;
const RELEASE_S = 0.04;

function beeps(f: number, count: number, on: number, off: number, start = 0): Note[] {
  return Array.from({ length: count }, (_, i) => ({ f, t: start + i * (on + off), d: on }));
}

/** Patterns per class, seconds. */
export const TONE_PATTERNS: Readonly<Record<AlertClass, readonly Note[]>> = {
  // Rising/falling sweep, three times.
  emergency: [0, 1, 2].map((i) => ({ f: 700, f2: 1400, t: i * 0.9, d: 0.8 })),
  // Fast double beeps.
  urgent: [...beeps(1050, 3, 0.18, 0.08), ...beeps(1050, 3, 0.18, 0.08, 0.95)],
  // Two-note chime, twice.
  safety: [{ f: 880, t: 0, d: 0.22 }, { f: 1175, t: 0.26, d: 0.34 }, { f: 880, t: 0.8, d: 0.22 }, { f: 1175, t: 1.06, d: 0.34 }],
  local: [...beeps(960, 4, 0.2, 0.1), ...beeps(960, 4, 0.2, 0.1, 1.4)],
};

export function patternDurationMs(cls: AlertClass): number {
  const p = TONE_PATTERNS[cls];
  return Math.ceil(Math.max(...p.map((n) => n.t + n.d)) * 1000 + RELEASE_S * 1000);
}

type Ctor = new () => AudioContext;

function audioCtor(): Ctor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { AudioContext?: Ctor; webkitAudioContext?: Ctor };
  return w.AudioContext ?? w.webkitAudioContext ?? null;
}

export class AlertTone {
  private ctx: AudioContext | null = null;

  /** Call from a user gesture. Returns false when Web Audio is unavailable. */
  unlock(): boolean {
    const C = audioCtor();
    if (C === null) return false;
    if (this.ctx === null) this.ctx = new C();
    void this.ctx.resume().catch(() => undefined);
    return true;
  }

  get ready(): boolean {
    return this.ctx !== null && this.ctx.state === "running";
  }

  /** Plays the class pattern; resolves after it ends (immediately when audio is not unlocked). */
  play(cls: AlertClass): Promise<void> {
    const ctx = this.ctx;
    if (ctx === null || ctx.state !== "running") return Promise.resolve();
    const t0 = ctx.currentTime + 0.02;
    for (const n of TONE_PATTERNS[cls]) {
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = "square";
      osc.frequency.setValueAtTime(n.f, t0 + n.t);
      if (n.f2 !== undefined) osc.frequency.linearRampToValueAtTime(n.f2, t0 + n.t + n.d);
      g.gain.setValueAtTime(0, t0 + n.t);
      g.gain.linearRampToValueAtTime(PEAK_GAIN, t0 + n.t + ATTACK_S);
      g.gain.setValueAtTime(PEAK_GAIN, t0 + n.t + n.d - RELEASE_S);
      g.gain.linearRampToValueAtTime(0, t0 + n.t + n.d);
      osc.connect(g).connect(ctx.destination);
      osc.start(t0 + n.t);
      osc.stop(t0 + n.t + n.d + 0.01);
    }
    return new Promise((r) => window.setTimeout(r, patternDurationMs(cls)));
  }

  close(): void {
    void this.ctx?.close().catch(() => undefined);
    this.ctx = null;
  }
}
