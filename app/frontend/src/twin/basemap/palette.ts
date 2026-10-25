/** Daylight palette of the offline basemap and the building use colours. Night = the same colours darkened and cooled. */
export type Rgb = readonly [number, number, number];

export const GROUND: Rgb = [238, 235, 227];
export const WATER: Rgb = [150, 198, 232];
export const RAIL: Rgb = [150, 152, 162];
export const ROAD_CASING: Rgb = [196, 198, 206];
export const ROAD_CASING_MAJOR: Rgb = [222, 190, 120];
export const ROAD_CASING_HIGHWAY: Rgb = [214, 160, 80];
export const ROAD_FILL: Rgb = [255, 255, 255];
export const ROAD_FILL_MAJOR: Rgb = [255, 246, 214];
export const ROAD_FILL_HIGHWAY: Rgb = [255, 226, 150];
export const PATH: Rgb = [226, 221, 210];
export const LABEL: Rgb = [58, 66, 80];
export const LABEL_HALO: Rgb = [255, 255, 255];

const GREEN_PARK: Rgb = [196, 224, 168];
const GREEN_FOREST: Rgb = [160, 200, 146];
const GREEN_GRASS: Rgb = [208, 228, 182];
export const LAND_COLOR: Readonly<Record<string, Rgb>> = {
  park: GREEN_PARK, garden: GREEN_PARK, village_green: GREEN_PARK, golf_course: GREEN_PARK, dog_park: GREEN_PARK,
  forest: GREEN_FOREST, wood: GREEN_FOREST, scrub: [180, 208, 154],
  grass: GREEN_GRASS, grassland: GREEN_GRASS, meadow: GREEN_GRASS, farmland: [224, 226, 192],
  cemetery: [208, 222, 198], playground: [200, 224, 176], pitch: [186, 220, 170],
  school: [240, 228, 206], college: [240, 228, 206], university: [238, 226, 204], kindergarten: [240, 228, 206],
  hospital: [244, 222, 222], industrial: [224, 220, 228], commercial: [240, 228, 216], residential: [240, 237, 229],
  military: [226, 226, 206], pedestrian: [246, 244, 238], railway: [226, 224, 224], platform: [226, 224, 224],
  bare_rock: [214, 210, 202], sand: [240, 232, 204],
};
/** Draw order of land kinds (low first): big built-up areas under greens, greens under special areas. */
export const LAND_ORDER: readonly string[] = ["residential", "commercial", "industrial", "farmland", "military", "railway", "platform", "pedestrian", "scrub", "grass", "grassland", "meadow", "garden", "village_green", "park", "forest", "wood", "cemetery", "golf_course", "bare_rock", "sand", "school", "college", "university", "kindergarten", "hospital", "playground", "pitch", "dog_park"];

/** Brightness factor of the map: 1 at full day, NIGHT_MIN at night (cooler blue shift). */
const NIGHT_MIN = 0.46;
export const dayFactor = (day: number): number => NIGHT_MIN + (1 - NIGHT_MIN) * Math.max(0, Math.min(1, day));
export function shade(c: Rgb, day: number): [number, number, number, number] {
  const k = dayFactor(day);
  const cool = 1 - Math.max(0, Math.min(1, day));
  return [Math.round(c[0] * k * (1 - 0.1 * cool)), Math.round(c[1] * k * (1 - 0.04 * cool)), Math.round(Math.min(255, c[2] * k * (1 + 0.1 * cool))), 255];
}

/** Building use codes from the scene (`buildings.use`): 0 단독·다가구, 1 공동주택, 2 근린생활·상업·업무, 3 교육·연구·공공·의료, 4 종교·문화·기타, 5 미상. */
const USE_COLOR: readonly Rgb[] = [
  [226, 200, 166], // 단독·다가구: warm beige
  [214, 216, 220], // 공동주택: light grey
  [168, 184, 204], // 근린생활·업무: blue-grey
  [208, 150, 128], // 교육·공공: muted terracotta
  [200, 186, 212], // 종교·문화·기타: soft lilac
  [208, 206, 200], // 미상: neutral
];
export const USE_LEGEND: readonly { code: number; label: string }[] = [
  { code: 0, label: "단독·다가구" }, { code: 1, label: "공동주택" }, { code: 2, label: "근린생활·업무" }, { code: 3, label: "교육·공공·의료" }, { code: 4, label: "종교·문화·기타" }, { code: 5, label: "용도 미상" },
];
const NEUTRAL_LOW: Rgb = [220, 212, 200];
const NEUTRAL_HIGH: Rgb = [196, 202, 212];

/**
 * Colour of one building. `use` is null when the scene carries no use (neutral warm-to-cool height ramp).
 * Taller buildings are slightly cooler/darker so the skyline keeps its depth.
 */
export function buildingRgb(use: number | null, heightFrac: number): [number, number, number] {
  const f = Math.max(0, Math.min(1, heightFrac));
  const base = use === null ? ([0, 1, 2].map((i) => (NEUTRAL_LOW[i] as number) + ((NEUTRAL_HIGH[i] as number) - (NEUTRAL_LOW[i] as number)) * f) as unknown as Rgb) : (USE_COLOR[use] ?? USE_COLOR[5] as Rgb);
  const k = 1 - 0.14 * f;
  return [Math.round((base[0] as number) * k), Math.round((base[1] as number) * k), Math.round((base[2] as number) * k)];
}
