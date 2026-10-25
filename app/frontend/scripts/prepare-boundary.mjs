/**
 * Writes public/korea-municipalities.json from the cached southkorea-maps simplified boundary file.
 * Keeps only code + name and rounds coordinates to 3 decimals (~100 m), which is far below the
 * resolution of a 500-unit-wide map. The 2013 boundaries are background only; dots use 2026 centroids.
 *
 * Usage: node scripts/prepare-boundary.mjs [source.json] [dest.json]
 */
import { readFileSync, writeFileSync } from "node:fs";

const DEFAULT_SRC = "../../data/cache/extra/boundaries/skorea_municipalities_geo_simple.json";
const DEFAULT_DEST = "public/korea-municipalities.json";
const DECIMALS = 3;

const [src = DEFAULT_SRC, dest = DEFAULT_DEST] = process.argv.slice(2);
const factor = 10 ** DECIMALS;
const round = (n) => Math.round(n * factor) / factor;
const roundCoords = (c) => (typeof c[0] === "number" ? [round(c[0]), round(c[1])] : c.map(roundCoords));

const input = JSON.parse(readFileSync(src, "utf8"));
const features = input.features.map((f) => ({
  type: "Feature",
  properties: { code: f.properties.code, name: f.properties.name },
  geometry: { type: f.geometry.type, coordinates: roundCoords(f.geometry.coordinates) },
}));
writeFileSync(dest, JSON.stringify({ type: "FeatureCollection", features }));
console.log(`${features.length} features -> ${dest}`);
