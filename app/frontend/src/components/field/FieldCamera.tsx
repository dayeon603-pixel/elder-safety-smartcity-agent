import { useCallback, useEffect, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import type { PoseLandmarker } from "@mediapipe/tasks-vision";
import { FALL_CHIP_HOLD_MS, FALL_DETECT_INTERVAL_MS, FALL_UI_INTERVAL_MS, FALL_UPRIGHT_DEG, FIELD_MAX_PERSONS, FIELD_POSE_MODEL_URL, FIELD_WASM_BASE, GAIT_FIELD_WIDTH_DEFAULT_M, GAIT_FIELD_WIDTH_MAX_M, GAIT_FIELD_WIDTH_MIN_M, PRIVACY_SENT_VIDEO, PRIVACY_STORED_FRAMES, FACE_BLOCKS_ACROSS, WHOLE_FRAME_BLOCK_PX } from "../../config";
import { faceBox, personBox } from "../../lib/faceMask";
import { initialAi, initialRule, stepAi, stepRule, torsoAngle } from "../../lib/fallModel";
import type { AiState, FallModel, Frame, RuleState } from "../../lib/fallModel";
import { estimateGroundSpeed, estimateSpeed, personStatus, pushGround, pushSample, PERSON_STATUS_LABEL } from "../../lib/gait";
import type { GaitStatus, GroundSample, HipSample, PersonStatus } from "../../lib/gait";
import { applyH, groundRect, homographyFrom4, invertH, isConvexQuad, reprojectionRmsPx } from "../../lib/homography";
import type { Mat3, Pt } from "../../lib/homography";
import { fpsFrom, heapUsedMb, RollingWindow } from "../../lib/perfStats";
import { canvasToJpegBase64 } from "../../lib/snapshot";
import { initialTracker, trackStep } from "../../lib/tracker";
import type { TrackerState } from "../../lib/tracker";
import { Badge } from "../Badge";
import { Button } from "../Button";
import { TextField } from "../Field";
import { Notice } from "../Notice";

export interface PersonTelemetry {
  id: number;
  torsoDeg: number | null;
  speedMps: number | null;
  speedStatus: GaitStatus;
  /** AI fall-model probability of the latest 1 s window (null while the window fills) */
  aiProb: number | null;
  aiFired: boolean;
  ruleFired: boolean;
  status: PersonStatus;
}

export interface CamTelemetry {
  on: boolean;
  persons: PersonTelemetry[];
  inferP50: number | null;
  inferP95: number | null;
  fps: number;
  heapMb: number | null;
  calibrated: boolean;
}

export const CAM_OFF: CamTelemetry = { on: false, persons: [], inferP50: null, inferP95: null, fps: 0, heapMb: null, calibrated: false };

export interface FallInfo {
  id: number;
  perfMs: number;
  torsoDeg: number | null;
  speedMps: number | null;
  aiProb: number | null;
  /** whether the 60 deg / 1.5 s rule baseline had already fired when the AI fired */
  ruleFiredAlready: boolean;
  /** performance.now() of the frame where the torso first left upright before this alarm (fall onset), or null if unknown */
  onsetMs?: number | null;
}

interface Props {
  requiredMps: number | null;
  fallModel: FallModel | null;
  snapshotRef: MutableRefObject<(() => string | null) | null>;
  telemetry: CamTelemetry;
  onTelemetry: (t: CamTelemetry) => void;
  onFall: (f: FallInfo) => void;
  /** Compact mode (control-room inset): no explanatory text, calibration inputs or tables. */
  compact?: boolean;
  /** Replaces the mask badge text. */
  maskLabel?: string;
  /** Fired with performance.now() when a person's torso leaves upright, and with null when upright again. */
  onOnset?: (t: number | null) => void;
  /** Recorded clips played through the same pose + fall-model pipeline (backup when the webcam fails). */
  files?: readonly { id: string; label: string; url: string }[];
  /** Badge shown while a recorded clip is processed. */
  fileBadge?: string;
  /** Replaces the empty-state explanation (compact inset). */
  placeholder?: React.ReactNode;
}

interface Landmark {
  x: number;
  y: number;
  visibility?: number;
}

interface Runtime {
  ai: AiState;
  rule: RuleState;
  aiProb: number | null;
  aiFiredAt: number | null;
  ruleFiredAt: number | null;
  hip: HipSample[];
  ground: GroundSample[];
  torsoDeg: number | null;
  speedMps: number | null;
  speedStatus: GaitStatus;
  nonUprightSince: number | null;
  /** hip height samples (normalised image y, smaller = higher) of the last ONSET_LOOKBACK_MS, for the fall-onset estimate */
  hipY: { t: number; y: number }[];
}

const ONSET_LOOKBACK_MS = 3000;

/** Fall onset = the frame where the hips were highest within the look-back window (start of the descent); null without samples. */
function descentStart(rt: Runtime, firedAt: number): number | null {
  let best: { t: number; y: number } | null = null;
  for (const q of rt.hipY) if (firedAt - q.t <= ONSET_LOOKBACK_MS && (best === null || q.y < best.y)) best = q;
  return best === null ? null : best.t;
}

type CamState = "off" | "loading" | "on" | "error";
const AN = { LEFT: 27, RIGHT: 28 } as const;
const ANKLE_MIN_VIS = 0.5;
const CALIB_DEFAULT_W = "2";
const CALIB_DEFAULT_D = "1";
const POINT_LABELS = ["1 왼쪽 앞", "2 오른쪽 앞", "3 오른쪽 뒤", "4 왼쪽 뒤"] as const;
const GRID_STEP_M = 0.5;
const KEY_STEP = 0.01;
const KEY_STEP_BIG = 0.05;
const COLOR_OK = "#0072B2";
const COLOR_FALL = "#b3261e";

function describeCameraError(err: unknown): string {
  if (err instanceof DOMException) {
    if (err.name === "NotAllowedError") return "카메라 권한이 거부되었습니다. 브라우저의 사이트 권한에서 허용한 뒤 다시 눌러 주세요.";
    if (err.name === "NotFoundError") return "카메라를 찾지 못했습니다. 연결을 확인해 주세요.";
    if (err.name === "NotReadableError") return "다른 앱이 카메라를 쓰고 있습니다. 닫은 뒤 다시 눌러 주세요.";
  }
  if (err instanceof Error) return `카메라 또는 자세 모델을 시작하지 못했습니다: ${err.message}`;
  return "카메라를 시작하지 못했습니다.";
}

function pixelate(ctx: CanvasRenderingContext2D, scratch: HTMLCanvasElement, sx: number, sy: number, sw: number, sh: number, block: number): void {
  const w = Math.max(1, Math.round(sw));
  const h = Math.max(1, Math.round(sh));
  const tw = Math.max(1, Math.round(w / block));
  const th = Math.max(1, Math.round(h / block));
  const sctx = scratch.getContext("2d");
  if (sctx === null) return;
  scratch.width = tw;
  scratch.height = th;
  sctx.drawImage(ctx.canvas, sx, sy, w, h, 0, 0, tw, th);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(scratch, 0, 0, tw, th, sx, sy, w, h);
  ctx.imageSmoothingEnabled = true;
}

export function FieldCamera({ requiredMps, fallModel, snapshotRef, telemetry, onTelemetry, onFall, compact = false, maskLabel, onOnset, files, fileBadge, placeholder }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState<CamState>("off");
  const [error, setError] = useState<string | null>(null);
  const [fileMode, setFileMode] = useState(false);
  const stopRef = useRef<(() => void) | null>(null);

  const [fieldText, setFieldText] = useState(String(GAIT_FIELD_WIDTH_DEFAULT_M));
  const fieldNum = Number(fieldText);
  const fieldValid = Number.isFinite(fieldNum) && fieldNum >= GAIT_FIELD_WIDTH_MIN_M && fieldNum <= GAIT_FIELD_WIDTH_MAX_M;
  const fieldRef = useRef(GAIT_FIELD_WIDTH_DEFAULT_M);
  if (fieldValid) fieldRef.current = fieldNum;
  const reqRef = useRef(requiredMps);
  reqRef.current = requiredMps;
  const onTelRef = useRef(onTelemetry);
  onTelRef.current = onTelemetry;
  const modelRef = useRef(fallModel);
  modelRef.current = fallModel;
  const onFallRef = useRef(onFall);
  onFallRef.current = onFall;
  const onOnsetRef = useRef(onOnset);
  onOnsetRef.current = onOnset;

  // ground calibration
  const [picking, setPicking] = useState(false);
  const [points, setPoints] = useState<Pt[]>([]);
  const [widthText, setWidthText] = useState(CALIB_DEFAULT_W);
  const [depthText, setDepthText] = useState(CALIB_DEFAULT_D);
  const [calibError, setCalibError] = useState<string | null>(null);
  const [residual, setResidual] = useState<number | null>(null);
  const [cursor, setCursor] = useState<Pt>({ x: 0.5, y: 0.5 });
  const homographyRef = useRef<Mat3 | null>(null);
  const invRef = useRef<Mat3 | null>(null);
  const rectRef = useRef<{ w: number; d: number } | null>(null);
  const [calibrated, setCalibrated] = useState(false);

  const clearCalibration = useCallback(() => {
    homographyRef.current = null;
    invRef.current = null;
    rectRef.current = null;
    setCalibrated(false);
    setResidual(null);
    setPoints([]);
    setCalibError(null);
    setPicking(false);
  }, []);

  const addPoint = useCallback((srcPt: Pt) => {
    setPoints((prev) => {
      const next = [...prev, srcPt];
      if (next.length < 4) return next;
      const video = videoRef.current;
      const w = Number(widthText);
      const d = Number(depthText);
      if (video === null || !(w > 0) || !(d > 0)) {
        setCalibError("사각형 가로·세로를 0보다 큰 숫자(m)로 입력해 주세요.");
        setPicking(false);
        return [];
      }
      const px = next.map((p) => ({ x: p.x * video.videoWidth, y: p.y * video.videoHeight }));
      const ground = groundRect(w, d);
      const h = isConvexQuad(px) ? homographyFrom4(px, ground) : null;
      if (h === null) {
        setCalibError("네 점이 볼록 사각형이 아닙니다. 바닥 사각형의 네 모서리를 순서대로 다시 찍어 주세요.");
        setPicking(false);
        return [];
      }
      homographyRef.current = h;
      invRef.current = invertH(h);
      rectRef.current = { w, d };
      setResidual(reprojectionRmsPx(h, px, ground));
      setCalibrated(true);
      setCalibError(null);
      setPicking(false);
      return next;
    });
  }, [widthText, depthText]);

  const onCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>): void => {
    if (!picking) return;
    const r = e.currentTarget.getBoundingClientRect();
    addPoint({ x: 1 - (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height });
  };
  const onCanvasKey = (e: React.KeyboardEvent<HTMLCanvasElement>): void => {
    if (!picking) return;
    const step = e.shiftKey ? KEY_STEP_BIG : KEY_STEP;
    const move = (dx: number, dy: number): void => {
      e.preventDefault();
      setCursor((c) => ({ x: Math.min(1, Math.max(0, c.x + dx)), y: Math.min(1, Math.max(0, c.y + dy)) }));
    };
    if (e.key === "ArrowLeft") move(-step, 0);
    else if (e.key === "ArrowRight") move(step, 0);
    else if (e.key === "ArrowUp") move(0, -step);
    else if (e.key === "ArrowDown") move(0, step);
    else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      addPoint({ x: 1 - cursor.x, y: cursor.y });
    }
  };

  const stop = useCallback(() => {
    stopRef.current?.();
    stopRef.current = null;
    snapshotRef.current = null;
    setState("off");
    onTelRef.current(CAM_OFF);
  }, [snapshotRef]);

  useEffect(() => () => stopRef.current?.(), []);

  const start = async (fileUrl?: string): Promise<void> => {
    setError(null);
    setFileMode(fileUrl !== undefined);
    stopRef.current?.();
    stopRef.current = null;
    setState("loading");
    let stream: MediaStream | null = null;
    let landmarker: PoseLandmarker | null = null;
    let raf = 0;
    let alive = true;
    const cleanup = (): void => {
      alive = false;
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((t) => t.stop());
      landmarker?.close();
      const v = videoRef.current;
      if (v !== null) {
        v.srcObject = null;
        v.removeAttribute("src");
      }
    };
    try {
      if (fileUrl === undefined) {
        if (navigator.mediaDevices === undefined || typeof navigator.mediaDevices.getUserMedia !== "function") {
          throw new Error("이 브라우저는 카메라 API를 지원하지 않거나 보안 연결(HTTPS 또는 localhost)이 아닙니다.");
        }
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
      }
      const vision = await import("@mediapipe/tasks-vision");
      const fileset = await vision.FilesetResolver.forVisionTasks(FIELD_WASM_BASE);
      landmarker = await vision.PoseLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: FIELD_POSE_MODEL_URL },
        runningMode: "VIDEO",
        numPoses: FIELD_MAX_PERSONS,
      });
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (video === null || canvas === null || !alive) throw new Error("화면 요소를 찾지 못했습니다.");
      if (fileUrl === undefined) video.srcObject = stream;
      else {
        video.muted = true;
        video.loop = false;
        video.src = fileUrl;
      }
      await video.play();
      const ctx = canvas.getContext("2d");
      if (ctx === null) throw new Error("캔버스를 만들지 못했습니다.");
      const scratch = document.createElement("canvas");
      const connections = vision.PoseLandmarker.POSE_CONNECTIONS;
      const lm = landmarker;
      snapshotRef.current = () => canvasToJpegBase64(canvas);
      stopRef.current = cleanup;
      setState("on");

      const infer = new RollingWindow();
      const stamps: number[] = [];
      const runtimes = new Map<number, Runtime>();
      let tracker: TrackerState = initialTracker();
      let lastDetect = 0;
      let lastUi = 0;
      let lastVideoTime = -1;

      const loop = (): void => {
        raf = requestAnimationFrame(loop);
        if (video.readyState < 2 || video.currentTime === lastVideoTime) return;
        const now = performance.now();
        if (now - lastDetect < FALL_DETECT_INTERVAL_MS) return;
        lastVideoTime = video.currentTime;
        lastDetect = now;
        const W = video.videoWidth;
        const H = video.videoHeight;
        if (canvas.width !== W) canvas.width = W;
        if (canvas.height !== H) canvas.height = H;

        const t0 = performance.now();
        const res = lm.detectForVideo(video, now);
        const t1 = performance.now();
        infer.push(t1 - t0);
        stamps.push(t1);

        const people = res.landmarks.map((l) => l as Landmark[]);
        const boxes = people.map((l) => personBox(l));
        const valid = people.map((l, i) => ({ l, b: boxes[i] ?? null })).filter((p): p is { l: Landmark[]; b: NonNullable<typeof p.b> } => p.b !== null);
        const step = trackStep(tracker, valid.map((p) => p.b), now);
        tracker = step.state;
        step.lost.forEach((id) => runtimes.delete(id));
        const aspect = W / H;
        const hom = homographyRef.current;

        const tSec = now / 1000;
        const seen = new Set<number>();
        valid.forEach((p, i) => {
          const id = step.ids[i] as number;
          seen.add(id);
          const rt: Runtime = runtimes.get(id) ?? { ai: initialAi(), rule: initialRule(), aiProb: null, aiFiredAt: null, ruleFiredAt: null, hip: [], ground: [], torsoDeg: null, speedMps: null, speedStatus: "warming_up", nonUprightSince: null, hipY: [] };
          const frame: Frame = p.l.map((q) => [q.x, q.y] as const);
          rt.torsoDeg = torsoAngle(frame, aspect);
          if (rt.torsoDeg < FALL_UPRIGHT_DEG) {
            rt.aiFiredAt = null;
            rt.ruleFiredAt = null;
            if (rt.nonUprightSince !== null) onOnsetRef.current?.(null);
            rt.nonUprightSince = null;
          } else if (rt.nonUprightSince === null) {
            rt.nonUprightSince = now;
            onOnsetRef.current?.(now);
          }
          const rr = stepRule(rt.rule, tSec, rt.torsoDeg);
          rt.rule = rr.state;
          if (rr.fired) rt.ruleFiredAt = now;
          let aiFired = false;
          const mdl = modelRef.current;
          if (mdl !== null) {
            const ar = stepAi(mdl, rt.ai, tSec, frame, aspect);
            rt.ai = ar.state;
            rt.aiProb = ar.prob;
            aiFired = ar.fired;
            if (aiFired) rt.aiFiredAt = now;
          }
          const hipX = ((p.l[23]?.x ?? 0) + (p.l[24]?.x ?? 0)) / 2;
          rt.hipY.push({ t: now, y: ((p.l[23]?.y ?? 0) + (p.l[24]?.y ?? 0)) / 2 });
          while (rt.hipY.length > 0 && now - (rt.hipY[0] as { t: number }).t > ONSET_LOOKBACK_MS) rt.hipY.shift();
          if (hom !== null) {
            const la = p.l[AN.LEFT];
            const ra = p.l[AN.RIGHT];
            const ok = [la, ra].filter((a): a is Landmark => a !== undefined && (a.visibility ?? 1) >= ANKLE_MIN_VIS);
            if (ok.length > 0) {
              const ax = ok.reduce((sum, a) => sum + a.x, 0) / ok.length;
              const ay = ok.reduce((sum, a) => sum + a.y, 0) / ok.length;
              const g = applyH(hom, { x: ax * W, y: ay * H });
              if (g !== null) {
                rt.ground = pushGround(rt.ground, { tMs: now, X: g.x, Y: g.y });
                const e = estimateGroundSpeed(rt.ground);
                rt.speedMps = e.speedMps;
                rt.speedStatus = e.status;
              }
            }
          } else {
            rt.hip = pushSample(rt.hip, { tMs: now, x: hipX });
            const e = estimateSpeed(rt.hip, fieldRef.current);
            rt.speedMps = e.speedMps;
            rt.speedStatus = e.status;
          }
          runtimes.set(id, rt);
          // Primary trigger is the AI model; the rule baseline triggers only if the model file could not be loaded.
          const trig = mdl !== null ? aiFired : rr.fired;
          if (trig) onFallRef.current({ id, perfMs: performance.now(), torsoDeg: rt.torsoDeg, speedMps: rt.speedMps, aiProb: rt.aiProb, ruleFiredAlready: rt.ruleFiredAt !== null, onsetMs: descentStart(rt, performance.now()) ?? rt.nonUprightSince });
        });
        // People the tracker still holds but the detector missed this frame: carry their last pose forward (causal).
        const mdl2 = modelRef.current;
        if (mdl2 !== null) {
          for (const [id, rt] of runtimes) {
            if (seen.has(id)) continue;
            const ar = stepAi(mdl2, rt.ai, tSec, null, aspect);
            rt.ai = ar.state;
            rt.aiProb = ar.prob;
            if (ar.fired) {
              rt.aiFiredAt = now;
              onFallRef.current({ id, perfMs: performance.now(), torsoDeg: rt.torsoDeg, speedMps: rt.speedMps, aiProb: rt.aiProb, ruleFiredAlready: rt.ruleFiredAt !== null, onsetMs: descentStart(rt, performance.now()) ?? rt.nonUprightSince });
            }
          }
        }

        // Draw ONLY the masked frame: the raw frame is never left on screen between detection and masking.
        ctx.save();
        ctx.translate(W, 0);
        ctx.scale(-1, 1);
        ctx.drawImage(video, 0, 0, W, H);
        ctx.restore();
        if (valid.length === 0) {
          pixelate(ctx, scratch, 0, 0, W, H, WHOLE_FRAME_BLOCK_PX);
        } else {
          for (const p of valid) {
            const fb = faceBox(p.l);
            if (fb === null) {
              pixelate(ctx, scratch, 0, 0, W, H, WHOLE_FRAME_BLOCK_PX);
              break;
            }
            const sw = (fb.x1 - fb.x0) * W;
            pixelate(ctx, scratch, (1 - fb.x1) * W, fb.y0 * H, sw, (fb.y1 - fb.y0) * H, Math.max(6, sw / FACE_BLOCKS_ACROSS));
          }
        }
        const inv = invRef.current;
        const rect = rectRef.current;
        if (inv !== null && rect !== null) {
          ctx.strokeStyle = "rgba(255,255,255,0.85)";
          ctx.lineWidth = 1;
          const line = (a: Pt, b: Pt): void => {
            const pa = applyH(inv, a);
            const pb = applyH(inv, b);
            if (pa === null || pb === null) return;
            ctx.beginPath();
            ctx.moveTo(W - pa.x, pa.y);
            ctx.lineTo(W - pb.x, pb.y);
            ctx.stroke();
          };
          for (let x = 0; x <= rect.w + 1e-9; x += GRID_STEP_M) line({ x, y: 0 }, { x, y: rect.d });
          for (let y = 0; y <= rect.d + 1e-9; y += GRID_STEP_M) line({ x: 0, y }, { x: rect.w, y });
        }
        valid.forEach((p, i) => {
          const id = step.ids[i] as number;
          const fallen = runtimes.get(id)?.aiFiredAt != null;
          ctx.strokeStyle = fallen ? COLOR_FALL : COLOR_OK;
          ctx.fillStyle = ctx.strokeStyle;
          ctx.lineWidth = 3;
          for (const c of connections) {
            const a = p.l[c.start];
            const b = p.l[c.end];
            if (a === undefined || b === undefined || (a.visibility ?? 1) < 0.5 || (b.visibility ?? 1) < 0.5) continue;
            ctx.beginPath();
            ctx.moveTo((1 - a.x) * W, a.y * H);
            ctx.lineTo((1 - b.x) * W, b.y * H);
            ctx.stroke();
          }
          ctx.font = "bold 16px sans-serif";
          ctx.fillText(`ID ${id}`, (1 - p.b.x1) * W, Math.max(16, p.b.y0 * H - 6));
        });

        if (now - lastUi >= FALL_UI_INTERVAL_MS) {
          lastUi = now;
          const persons: PersonTelemetry[] = [...runtimes.entries()]
            .filter(([id]) => step.ids.includes(id))
            .sort((a, b) => a[0] - b[0])
            .map(([id, rt]) => {
              const aiOn = rt.aiFiredAt !== null && now - rt.aiFiredAt < FALL_CHIP_HOLD_MS;
              const ruleOn = rt.ruleFiredAt !== null && now - rt.ruleFiredAt < FALL_CHIP_HOLD_MS;
              return {
                id, torsoDeg: rt.torsoDeg, speedMps: rt.speedMps, speedStatus: rt.speedStatus, aiProb: rt.aiProb, aiFired: aiOn, ruleFired: ruleOn,
                status: personStatus(aiOn || (modelRef.current === null && ruleOn), rt.speedMps, reqRef.current),
              };
            });
          onTelRef.current({
            on: true, persons, inferP50: infer.percentile(50), inferP95: infer.percentile(95), fps: fpsFrom(stamps, now), heapMb: heapUsedMb(),
            calibrated: homographyRef.current !== null,
          });
        }
      };
      raf = requestAnimationFrame(loop);
    } catch (err) {
      cleanup();
      stopRef.current = null;
      setError(describeCameraError(err));
      setState("error");
    }
  };

  const on = state === "on";
  const fmt = (v: number | null, d: number): string => (v === null ? "–" : v.toFixed(d));

  return (
    <div className="space-y-3">
      <div className="relative border border-line bg-surface-inset">
        <video ref={videoRef} muted playsInline aria-hidden="true" tabIndex={-1} className="pointer-events-none absolute left-0 top-0 h-px w-px opacity-0" />
        <canvas
          ref={canvasRef}
          width={640}
          height={480}
          onClick={onCanvasClick}
          onKeyDown={onCanvasKey}
          tabIndex={picking ? 0 : -1}
          aria-label={picking ? `지면 보정: 바닥 사각형의 ${POINT_LABELS[points.length] ?? ""} 모서리를 클릭하거나 방향키로 옮기고 Enter` : "얼굴을 가린 카메라 화면과 골격"}
          className={`block h-auto w-full ${on ? "" : "hidden"} ${picking ? "cursor-crosshair" : ""}`}
        />
        {on && picking && (
          <span aria-hidden="true" className="pointer-events-none absolute h-5 w-5 -translate-x-1/2 -translate-y-1/2 border-2 border-danger" style={{ left: `${cursor.x * 100}%`, top: `${cursor.y * 100}%` }} />
        )}
        {on && (
          <span className="absolute left-2 top-2 flex gap-2">
            <Badge tone="success">{maskLabel ?? "얼굴 자동 가림"}</Badge>
            {calibrated && <Badge tone="accent">단안 카메라 지면 보정</Badge>}
            {fileMode && fileBadge !== undefined && <Badge tone="accent">{fileBadge}</Badge>}
          </span>
        )}
        {!on && (
          <div className="flex aspect-[4/3] flex-col items-center justify-center gap-3 p-6 text-center">
            {placeholder ?? (!compact && <p className="text-small text-ink-muted">카메라는 아래 버튼을 눌러야 켜집니다. 켜기 전에는 권한을 요청하지 않습니다.</p>)}
            <div className="flex flex-wrap items-center justify-center gap-2">
              <Button variant="primary" onClick={() => void start()} loading={state === "loading" && !fileMode}>카메라 켜기</Button>
              {files?.map((f) => <Button key={f.id} variant="secondary" onClick={() => void start(f.url)} loading={state === "loading" && fileMode}>{f.label}</Button>)}
            </div>
          </div>
        )}
      </div>
      {error !== null && <Notice tone="danger">{error}</Notice>}
      {!compact && <p className="text-small font-medium">영상은 브라우저 안에서만 처리 · 저장·전송 없음</p>}
      {on && (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={stop}>{fileMode ? "영상 끄기" : "카메라 끄기"}</Button>
          {files?.map((f) => <Button key={f.id} variant="secondary" onClick={() => void start(f.url)}>{f.label}</Button>)}
          {!compact && !picking && !calibrated && <Button variant="secondary" onClick={() => { setPoints([]); setCalibError(null); setPicking(true); }}>지면 보정 시작</Button>}
          {picking && <Button variant="ghost" onClick={clearCalibration}>보정 취소</Button>}
          {calibrated && <Button variant="ghost" onClick={clearCalibration}>보정 해제</Button>}
        </div>
      )}
      {on && picking && (
        <Notice>
          바닥에 놓인 {widthText} m × {depthText} m 사각형의 모서리를 순서대로 찍습니다. 다음 점: <strong>{POINT_LABELS[points.length]}</strong> ({points.length}/4). 화면을 클릭하거나, 화면에 초점을 두고 방향키로 십자를 옮긴 뒤 Enter를 누르세요.
        </Notice>
      )}
      {calibError !== null && <Notice tone="danger">{calibError}</Notice>}

      <div className={`dev-only grid gap-3 sm:grid-cols-3 ${compact ? "hidden" : ""}`}>
        <TextField label="바닥 사각형 가로 (m)" type="number" inputMode="decimal" min={0.1} step={0.1} value={widthText} onChange={(e) => setWidthText(e.target.value)} disabled={picking || calibrated} />
        <TextField label="바닥 사각형 세로 (m)" type="number" inputMode="decimal" min={0.1} step={0.1} value={depthText} onChange={(e) => setDepthText(e.target.value)} disabled={picking || calibrated} />
        <TextField
          label="시야 폭 (m, 지면 보정 없을 때)"
          type="number" inputMode="decimal" min={GAIT_FIELD_WIDTH_MIN_M} max={GAIT_FIELD_WIDTH_MAX_M} step={0.5}
          value={fieldText} onChange={(e) => setFieldText(e.target.value)} disabled={calibrated}
          error={fieldValid ? null : `${GAIT_FIELD_WIDTH_MIN_M}–${GAIT_FIELD_WIDTH_MAX_M} m 사이로 입력해 주세요.`}
        />
      </div>
      {!compact && calibrated && residual !== null && (
        <p className="dev-only text-small text-ink-muted">
          지면 보정 잔차 <span className="num font-medium text-ink">{residual.toFixed(2)} px</span> (네 점은 정확해라 0에 가깝습니다. 흰 격자 0.5 m 간격이 바닥과 맞는지 눈으로 확인하세요.)
        </p>
      )}

      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
      <div role="region" aria-label="보행자별 측정값" tabIndex={0} className={`overflow-x-auto ${compact ? "hidden" : ""}`}>
        <table className="w-full min-w-[34rem] border-collapse text-small">
          <thead>
            <tr className="border-b border-line text-left text-caption text-ink-faint">
              <th className="py-1 pr-3 font-medium">ID</th>
              <th className="py-1 pr-3 font-medium">기울기°</th>
              <th className="py-1 pr-3 font-medium">추정 보행속도 m/s</th>
              <th className="py-1 pr-3 font-medium">AI 낙상 확률</th>
              <th className="py-1 pr-3 font-medium">규칙 기준선</th>
              <th className="py-1 font-medium">상태</th>
            </tr>
          </thead>
          <tbody>
            {telemetry.persons.length === 0 ? (
              <tr><td colSpan={6} className="py-2 text-ink-muted">{on ? "사람이 감지되지 않았습니다." : "카메라가 꺼져 있습니다."}</td></tr>
            ) : (
              telemetry.persons.map((p) => (
                <tr key={p.id} className="border-b border-line">
                  <td className="num py-1.5 pr-3 font-medium">{p.id}</td>
                  <td className="num py-1.5 pr-3">{fmt(p.torsoDeg, 0)}</td>
                  <td className="num py-1.5 pr-3">{p.speedMps === null ? (p.speedStatus === "warming_up" ? "측정 중" : "–") : p.speedMps.toFixed(2)}</td>
                  <td className="py-1.5 pr-3">
                    <span className="num mr-1">{p.aiProb === null ? "–" : p.aiProb.toFixed(2)}</span>
                    {p.aiFired && <Badge tone="danger">AI 발동</Badge>}
                  </td>
                  <td className="py-1.5 pr-3">{p.ruleFired ? <Badge tone="danger">규칙 발동</Badge> : <Badge>규칙 대기</Badge>}</td>
                  <td className="py-1.5"><Badge tone={p.status === "danger" ? "danger" : p.status === "caution" ? "accent" : "success"}>{PERSON_STATUS_LABEL[p.status]}</Badge></td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        <p className="mt-1 text-caption text-ink-faint">
          추정치 · {telemetry.calibrated ? "단안 카메라 지면 보정(호모그래피)" : "보정 필요: 시야 폭 근사"}
        </p>
      </div>

      <div className={`grid grid-cols-2 gap-x-4 gap-y-2 border border-line bg-surface-sunken p-3 text-small ${compact ? "hidden" : ""}`} aria-label="성능">
        <p><span className="block text-caption text-ink-faint">추론 p50 / p95 (최근 100프레임)</span><span className="num font-medium">{fmt(telemetry.inferP50, 1)} / {fmt(telemetry.inferP95, 1)} ms</span></p>
        <p><span className="block text-caption text-ink-faint">처리 FPS</span><span className="num font-medium">{on ? telemetry.fps.toFixed(0) : "–"}</span></p>
        {telemetry.heapMb !== null && <p><span className="block text-caption text-ink-faint">JS 힙 사용</span><span className="num font-medium">{telemetry.heapMb.toFixed(0)} MB</span></p>}
        <p className="col-span-2"><span className="block text-caption text-ink-faint">개인정보</span><span className="num font-medium">저장된 원본 프레임: {PRIVACY_STORED_FRAMES} · 전송된 영상: {PRIVACY_SENT_VIDEO}</span></p>
      </div>
    </div>
  );
}
