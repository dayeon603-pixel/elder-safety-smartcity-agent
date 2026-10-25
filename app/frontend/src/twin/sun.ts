/** Solar position (NOAA low-precision) and the lighting / sky derived from it. Pure functions. */

const DEG = Math.PI / 180;
const DAYS_IN_YEAR = 365;
const ARGUMENT_OFFSET_DAY = 81;
const OBLIQUITY_DEG = 23.44;
const KST_UTC_OFFSET_H = 9;
const DEG_PER_HOUR = 15;
const DEG_PER_LON_HOUR = 15;
const MS_PER_DAY = 86_400_000;

export interface SunPosition { azimuthDeg: number; elevationDeg: number }

export function dayOfYear(date: string): number {
  const d = Date.parse(`${date}T00:00:00Z`);
  const y0 = Date.parse(`${date.slice(0, 4)}-01-01T00:00:00Z`);
  return Math.round((d - y0) / MS_PER_DAY) + 1;
}

/** Sun azimuth (deg clockwise from north) and elevation at `hourKst` (fractional hours, KST) on `date`. */
export function sunPosition(date: string, hourKst: number, latDeg: number, lonDeg: number): SunPosition {
  const n = dayOfYear(date);
  const b = (360 / DAYS_IN_YEAR) * (n - ARGUMENT_OFFSET_DAY) * DEG;
  const decl = Math.asin(Math.sin(OBLIQUITY_DEG * DEG) * Math.sin(b));
  const eotMin = 9.87 * Math.sin(2 * b) - 7.53 * Math.cos(b) - 1.5 * Math.sin(b);
  const solarHour = hourKst - KST_UTC_OFFSET_H + lonDeg / DEG_PER_LON_HOUR + eotMin / 60;
  const h = (solarHour - 12) * DEG_PER_HOUR * DEG;
  const lat = latDeg * DEG;
  const sinEl = Math.sin(lat) * Math.sin(decl) + Math.cos(lat) * Math.cos(decl) * Math.cos(h);
  const el = Math.asin(Math.max(-1, Math.min(1, sinEl)));
  const az = Math.atan2(Math.sin(h), Math.cos(h) * Math.sin(lat) - Math.tan(decl) * Math.cos(lat)) / DEG + 180;
  return { azimuthDeg: (az + 360) % 360, elevationDeg: el / DEG };
}

/** Unit vector pointing from the scene toward the sun in deck's frame (x east, y north, z up). */
export function sunVector(p: SunPosition): [number, number, number] {
  const az = p.azimuthDeg * DEG;
  const el = p.elevationDeg * DEG;
  return [Math.sin(az) * Math.cos(el), Math.cos(az) * Math.cos(el), Math.sin(el)];
}

export interface Rgb { r: number; g: number; b: number }
const mix = (a: Rgb, b: Rgb, t: number): Rgb => ({ r: a.r + (b.r - a.r) * t, g: a.g + (b.g - a.g) * t, b: a.b + (b.b - a.b) * t });
export const rgbCss = (c: Rgb): string => `rgb(${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)})`;
const clamp01 = (x: number): number => Math.max(0, Math.min(1, x));
const smooth = (e0: number, e1: number, x: number): number => { const t = clamp01((x - e0) / (e1 - e0)); return t * t * (3 - 2 * t); };

/** Elevation thresholds (deg): full night below DAWN_LO, full day above DAY_HI. */
const DAWN_LO = -8;
const DAY_HI = 14;
const WARM_HI = 24;

const SKY = {
  nightTop: { r: 5, g: 8, b: 16 }, nightBottom: { r: 12, g: 18, b: 30 },
  dayTop: { r: 58, g: 94, b: 138 }, dayBottom: { r: 150, g: 176, b: 198 },
  duskBottom: { r: 120, g: 92, b: 86 },
  rainTop: { r: 34, g: 42, b: 52 }, rainBottom: { r: 62, g: 72, b: 82 },
};
const SUN_WARM: Rgb = { r: 255, g: 196, b: 140 };
const SUN_WHITE: Rgb = { r: 255, g: 246, b: 232 };
const MOON: Rgb = { r: 120, g: 150, b: 220 };

export interface Lighting {
  /** 0 = night, 1 = full day */
  day: number;
  sunDir: [number, number, number];
  sunColor: [number, number, number];
  sunIntensity: number;
  ambientColor: [number, number, number];
  ambientIntensity: number;
  skyTop: string;
  skyBottom: string;
  /** 0..1 darkening applied to dynamic overlay colours at night */
  nightness: number;
}

const arr3 = (c: Rgb): [number, number, number] => [Math.round(c.r), Math.round(c.g), Math.round(c.b)];

/** Lighting for a sun position. `rain01` (0..1, from observed rain) greys the sky and dims the sun. */
export function lightingFor(p: SunPosition, rain01: number): Lighting {
  const day = smooth(DAWN_LO, DAY_HI, p.elevationDeg);
  const warmth = 1 - smooth(DAY_HI, WARM_HI, p.elevationDeg);
  const sunUp = p.elevationDeg > 0;
  const sunDir = sunUp ? sunVector(p) : sunVector({ azimuthDeg: (p.azimuthDeg + 180) % 360, elevationDeg: -p.elevationDeg });
  const sunCol = sunUp ? mix(SUN_WHITE, SUN_WARM, warmth) : MOON;
  const dimRain = 1 - 0.55 * clamp01(rain01);
  const sunI = (sunUp ? 0.25 + 1.9 * smooth(0, 30, p.elevationDeg) : 0.9 * (1 - day)) * dimRain;
  const topClear = mix(SKY.nightTop, SKY.dayTop, day);
  const botClear = mix(mix(SKY.nightBottom, SKY.duskBottom, smooth(DAWN_LO, 0, p.elevationDeg) * (1 - day)), SKY.dayBottom, day);
  const k = clamp01(rain01) * 0.7;
  const top = mix(topClear, mix(SKY.rainTop, SKY.nightTop, 1 - day), k);
  const bot = mix(botClear, mix(SKY.rainBottom, SKY.nightBottom, 1 - day), k);
  return {
    day, sunDir: [-sunDir[0], -sunDir[1], -sunDir[2]], sunColor: arr3(sunCol), sunIntensity: sunI,
    ambientColor: arr3(mix({ r: 120, g: 140, b: 190 }, { r: 235, g: 240, b: 250 }, day)), ambientIntensity: 0.62 + 0.55 * day,
    skyTop: rgbCss(top), skyBottom: rgbCss(bot), nightness: 1 - day,
  };
}
