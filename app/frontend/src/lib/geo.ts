/**
 * Map projection and boundary-to-path conversion for the heat map.
 * Equirectangular with longitude scaled by cos(reference latitude): at Korea's extent the distortion
 * versus a conic projection is a few percent, invisible for a background plus dots.
 */

export const MAP_BOUNDS = { lonMin: 124.5, lonMax: 131.0, latMin: 33.0, latMax: 38.7 } as const;
export const MAP_WIDTH = 500;
export const MAP_PADDING = 8;
const REFERENCE_LAT_DEG = 36;
const PATH_DECIMALS = 1;
export const BOUNDARY_URL = `${import.meta.env.BASE_URL}korea-municipalities.json`;

const KX = Math.cos((REFERENCE_LAT_DEG * Math.PI) / 180);
const LON_SPAN = MAP_BOUNDS.lonMax - MAP_BOUNDS.lonMin;
const LAT_SPAN = MAP_BOUNDS.latMax - MAP_BOUNDS.latMin;
const SCALE = (MAP_WIDTH - 2 * MAP_PADDING) / (LON_SPAN * KX);
export const MAP_HEIGHT = Math.round(LAT_SPAN * SCALE + 2 * MAP_PADDING);

export interface Point {
  x: number;
  y: number;
}

/** (lon, lat) in degrees -> SVG user units inside the 0..MAP_WIDTH x 0..MAP_HEIGHT viewBox. */
export function project(lon: number, lat: number): Point {
  return {
    x: MAP_PADDING + (lon - MAP_BOUNDS.lonMin) * KX * SCALE,
    y: MAP_PADDING + (MAP_BOUNDS.latMax - lat) * SCALE,
  };
}

type Ring = number[][];

export interface BoundaryCollection {
  features: { geometry: { type: string; coordinates: unknown } }[];
}

function isRing(v: unknown): v is Ring {
  return Array.isArray(v) && v.every((p) => Array.isArray(p) && p.length >= 2 && typeof p[0] === "number" && typeof p[1] === "number");
}

function polygonsOf(geometry: { type: string; coordinates: unknown }): Ring[][] {
  const c = geometry.coordinates;
  if (!Array.isArray(c)) return [];
  const asPolygon = (v: unknown): Ring[] | null => (Array.isArray(v) && v.every(isRing) ? (v as Ring[]) : null);
  if (geometry.type === "Polygon") {
    const p = asPolygon(c);
    return p === null ? [] : [p];
  }
  if (geometry.type === "MultiPolygon") {
    return c.flatMap((poly: unknown) => {
      const p = asPolygon(poly);
      return p === null ? [] : [p];
    });
  }
  return [];
}

/** One SVG path string (all rings, closed) for every polygon in the collection. Unknown geometry types are skipped. */
export function boundaryToPath(fc: BoundaryCollection): string {
  const fmt = (n: number): string => n.toFixed(PATH_DECIMALS);
  const parts: string[] = [];
  for (const f of fc.features) {
    for (const poly of polygonsOf(f.geometry)) {
      for (const ring of poly) {
        const pts = ring.map(([lon, lat]) => project(lon ?? 0, lat ?? 0));
        if (pts.length < 3) continue;
        parts.push(`M${pts.map((p) => `${fmt(p.x)} ${fmt(p.y)}`).join("L")}Z`);
      }
    }
  }
  return parts.join("");
}

let outlineRequest: Promise<string> | null = null;

/** Fetch the static boundary asset once per page load and return its SVG path. */
export function loadOutline(): Promise<string> {
  outlineRequest ??= fetch(BOUNDARY_URL)
    .then((res) => {
      if (!res.ok) throw new Error(`boundary asset: HTTP ${res.status}`);
      return res.json() as Promise<BoundaryCollection>;
    })
    .then((fc) => {
      if (!Array.isArray(fc.features)) throw new Error("boundary asset: not a FeatureCollection");
      return boundaryToPath(fc);
    })
    .catch((err: unknown) => {
      outlineRequest = null; // allow a retry on the next mount
      throw err;
    });
  return outlineRequest;
}
