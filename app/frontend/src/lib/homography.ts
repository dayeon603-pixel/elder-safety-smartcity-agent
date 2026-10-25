/**
 * Ground-plane calibration for a single camera: 4-point DLT homography from image pixels to metres on the floor.
 * H maps (u, v, 1) in pixels to (X, Y, 1) in metres on the calibrated floor plane. Valid only for points ON that plane
 * (ankles on the ground, not hips), and only near the calibrated area; error grows away from the four reference points.
 */
export interface Pt {
  x: number;
  y: number;
}

export type Mat3 = readonly [number, number, number, number, number, number, number, number, number];

const SINGULAR_EPS = 1e-12;

/** Solve A x = b by Gaussian elimination with partial pivoting. null if singular. */
export function solveLinear(a: number[][], b: number[]): number[] | null {
  const n = b.length;
  const m = a.map((row, i) => [...row, b[i] as number]);
  for (let c = 0; c < n; c++) {
    let piv = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(m[r]![c]!) > Math.abs(m[piv]![c]!)) piv = r;
    if (Math.abs(m[piv]![c]!) < SINGULAR_EPS) return null;
    [m[c], m[piv]] = [m[piv]!, m[c]!];
    for (let r = c + 1; r < n; r++) {
      const f = m[r]![c]! / m[c]![c]!;
      for (let k = c; k <= n; k++) m[r]![k]! -= f * m[c]![k]!;
    }
  }
  const x = new Array<number>(n).fill(0);
  for (let r = n - 1; r >= 0; r--) {
    let s = m[r]![n]!;
    for (let k = r + 1; k < n; k++) s -= m[r]![k]! * x[k]!;
    x[r] = s / m[r]![r]!;
  }
  return x;
}

/** Exact homography through 4 point pairs (h33 = 1). null for degenerate input (e.g. 3 collinear points). */
export function homographyFrom4(src: readonly Pt[], dst: readonly Pt[]): Mat3 | null {
  if (src.length !== 4 || dst.length !== 4) throw new RangeError("need exactly 4 point pairs");
  const A: number[][] = [];
  const b: number[] = [];
  for (let i = 0; i < 4; i++) {
    const { x, y } = src[i]!;
    const { x: X, y: Y } = dst[i]!;
    A.push([x, y, 1, 0, 0, 0, -x * X, -y * X]);
    b.push(X);
    A.push([0, 0, 0, x, y, 1, -x * Y, -y * Y]);
    b.push(Y);
  }
  const h = solveLinear(A, b);
  if (h === null || h.some((v) => !Number.isFinite(v))) return null;
  return [h[0]!, h[1]!, h[2]!, h[3]!, h[4]!, h[5]!, h[6]!, h[7]!, 1];
}

export function applyH(h: Mat3, p: Pt): Pt | null {
  const w = h[6] * p.x + h[7] * p.y + h[8];
  if (Math.abs(w) < SINGULAR_EPS) return null;
  return { x: (h[0] * p.x + h[1] * p.y + h[2]) / w, y: (h[3] * p.x + h[4] * p.y + h[5]) / w };
}

export function invertH(h: Mat3): Mat3 | null {
  const [a, b, c, d, e, f, g, i, j] = h;
  const det = a * (e * j - f * i) - b * (d * j - f * g) + c * (d * i - e * g);
  if (Math.abs(det) < SINGULAR_EPS) return null;
  const inv: Mat3 = [
    (e * j - f * i) / det, (c * i - b * j) / det, (b * f - c * e) / det,
    (f * g - d * j) / det, (a * j - c * g) / det, (c * d - a * f) / det,
    (d * i - e * g) / det, (b * g - a * i) / det, (a * e - b * d) / det,
  ];
  return inv;
}

/** RMS pixel distance between the clicked image points and the ground points projected back through H^-1. */
export function reprojectionRmsPx(h: Mat3, image: readonly Pt[], ground: readonly Pt[]): number | null {
  const inv = invertH(h);
  if (inv === null) return null;
  let sum = 0;
  for (let k = 0; k < image.length; k++) {
    const back = applyH(inv, ground[k]!);
    if (back === null) return null;
    sum += (back.x - image[k]!.x) ** 2 + (back.y - image[k]!.y) ** 2;
  }
  return Math.sqrt(sum / image.length);
}

/** True when the 4 points form a simple convex quadrilateral (all cross products share a sign and are non-trivial). */
export function isConvexQuad(p: readonly Pt[], minCross = 1e-6): boolean {
  if (p.length !== 4) return false;
  let sign = 0;
  for (let i = 0; i < 4; i++) {
    const a = p[i]!;
    const b = p[(i + 1) % 4]!;
    const c = p[(i + 2) % 4]!;
    const cross = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x);
    if (Math.abs(cross) < minCross) return false;
    const s = Math.sign(cross);
    if (sign === 0) sign = s;
    else if (s !== sign) return false;
  }
  return true;
}

/** Floor rectangle corners in metres, in click order: near-left (origin), near-right, far-right, far-left. */
export function groundRect(widthM: number, depthM: number): Pt[] {
  if (!(widthM > 0) || !(depthM > 0)) throw new RangeError("rectangle sides must be > 0");
  return [{ x: 0, y: 0 }, { x: widthM, y: 0 }, { x: widthM, y: depthM }, { x: 0, y: depthM }];
}
