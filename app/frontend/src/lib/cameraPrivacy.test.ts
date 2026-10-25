import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { PRIVACY_SENT_VIDEO, PRIVACY_STORED_FRAMES } from "../config";

/**
 * Code-path audit behind the "저장된 원본 프레임: 0 · 전송된 영상: 0" line: the camera code contains no
 * storage, recording or network API. The only image export is lib/snapshot.ts (face-masked canvas, opt-in).
 */
const CAMERA_FILES = [
  "src/components/field/FieldCamera.tsx", "src/lib/faceMask.ts", "src/lib/tracker.ts", "src/lib/gait.ts",
  "src/lib/fallModel.ts", "src/lib/homography.ts", "src/lib/perfStats.ts",
];
const FORBIDDEN = /\b(fetch|XMLHttpRequest|sendBeacon|WebSocket|MediaRecorder|toBlob|toDataURL|localStorage|sessionStorage|indexedDB|getImageData|createObjectURL|ImageCapture|captureStream)\b/;

describe("camera privacy code-path audit", () => {
  for (const f of CAMERA_FILES) {
    it(`${f} has no storage, recording or network API`, () => {
      expect(readFileSync(f, "utf8")).not.toMatch(FORBIDDEN);
    });
  }
  it("the counters shown in the UI are zero because of the above", () => {
    expect(PRIVACY_STORED_FRAMES).toBe(0);
    expect(PRIVACY_SENT_VIDEO).toBe(0);
  });
  it("the snapshot exporter is the only toDataURL user and exports JPEG of the masked canvas only", () => {
    const s = readFileSync("src/lib/snapshot.ts", "utf8");
    expect(s).toContain("toDataURL");
    expect(s).not.toMatch(/video/i);
  });
  it("the raw <video> element is never visible: it is 1px and transparent", () => {
    expect(readFileSync("src/components/field/FieldCamera.tsx", "utf8")).toMatch(/<video[^>]*opacity-0/);
  });
});
