/** Story state machine (pure, time-driven; no deck.gl / DOM). Phases: idle > enter > walk > arrived > fall > rescue > done. */
import { ARRIVE_DWELL_MS, ENTER_MS, FALL_ANIM_MS, RUN_MPS, WALK_MPS } from "./constants";
import { cumulative, pointAt } from "./geo";
import type { XY } from "./geo";
import type { StoryPlan } from "./plan";

export type Phase = "idle" | "enter" | "walk" | "arrived" | "fall" | "rescue" | "done";
export type FallSource = "camera" | "button";

export interface Rescue { path: XY[]; cum: number[]; meters: number }

export class StoryMachine {
  phase: Phase = "idle";
  plan: StoryPlan | null = null;
  /** metres walked along plan.path */
  s = 0;
  phaseMs = 0;
  fallSource: FallSource | null = null;
  fallXY: XY | null = null;
  rescue: Rescue | null = null;
  /** metres run by the passerby along the out-and-back path */
  runS = 0;

  start(plan: StoryPlan): void {
    this.plan = plan;
    this.phase = "enter";
    this.phaseMs = 0;
    this.s = 0;
    this.fallSource = null;
    this.fallXY = null;
    this.rescue = null;
    this.runS = 0;
  }

  reset(): void {
    this.phase = "idle";
    this.plan = null;
    this.rescue = null;
    this.fallXY = null;
    this.fallSource = null;
    this.s = 0;
    this.runS = 0;
    this.phaseMs = 0;
  }

  private to(p: Phase): void { this.phase = p; this.phaseMs = 0; }

  /** A fall is accepted in any active phase before the fall itself; the person is then placed at `at`. */
  fall(src: FallSource, at: XY): boolean {
    if (this.plan === null || this.phase === "idle" || this.phase === "fall" || this.phase === "rescue" || this.phase === "done") return false;
    this.fallSource = src;
    this.fallXY = at;
    this.to("fall");
    return true;
  }

  /** Called once the AED route is known (metres frame). Starts the passerby run. */
  beginRescue(path: XY[], meters: number): boolean {
    if (this.phase !== "fall" || path.length < 2) return false;
    this.rescue = { path, cum: cumulative(path), meters };
    this.runS = 0;
    this.to("rescue");
    return true;
  }

  update(dtMs: number): void {
    const plan = this.plan;
    if (plan === null) return;
    this.phaseMs += dtMs;
    const sp = plan.speedup;
    switch (this.phase) {
      case "enter":
        if (this.phaseMs >= ENTER_MS) this.to("walk");
        break;
      case "walk":
        this.s = Math.min(plan.lengthM, this.s + (WALK_MPS * sp * dtMs) / 1000);
        if (this.s >= plan.lengthM) this.to("arrived");
        break;
      case "rescue": {
        const r = this.rescue as Rescue;
        this.runS = Math.min(2 * r.meters, this.runS + (RUN_MPS * sp * dtMs) / 1000);
        if (this.runS >= 2 * r.meters) this.to("done");
        break;
      }
      default:
        break;
    }
  }

  get dwellDone(): boolean { return this.phase === "arrived" && this.phaseMs >= ARRIVE_DWELL_MS; }
  get walking(): boolean { return this.phase === "walk"; }
  /** 0 = standing, 1 = lying */
  get lie01(): number { return this.phase === "fall" || this.phase === "rescue" || this.phase === "done" ? Math.min(1, this.phaseMsSinceFall / FALL_ANIM_MS) : 0; }
  private get phaseMsSinceFall(): number { return this.phase === "fall" ? this.phaseMs : Number.POSITIVE_INFINITY; }

  person(): { p: XY; heading: number } | null {
    const plan = this.plan;
    if (plan === null) return null;
    if (this.fallXY !== null && (this.phase === "fall" || this.phase === "rescue" || this.phase === "done")) return { p: this.fallXY, heading: this.fallHeading };
    return pointAt(plan.path, plan.cum, this.s);
  }

  fallHeading = 0;

  /** Passerby on the out-and-back route (from the person to the AED and back); null before the rescue. */
  passerby(): { p: XY; heading: number; leg: "out" | "back" } | null {
    const r = this.rescue;
    if (r === null) return null;
    const leg = this.runS < r.meters ? "out" : "back";
    const d = leg === "out" ? this.runS : 2 * r.meters - this.runS;
    const h = pointAt(r.path, r.cum, d);
    return { p: h.p, heading: leg === "out" ? h.heading : h.heading + Math.PI, leg };
  }

  /** Real seconds the passerby has been running (distance / 1.2 m/s). */
  get runRealSec(): number { return this.runS / RUN_MPS; }
  get runTotalRealSec(): number { return this.rescue === null ? 0 : (2 * this.rescue.meters) / RUN_MPS; }
}
