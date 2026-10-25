/**
 * Bake public/basemap/gwanak.pmtiles (Protomaps extract of OpenStreetMap, ODbL) into one compact JSON in the scene's metre
 * frame (equirectangular around the scene centre, same frame as the building scene). No network at run time.
 * Run: node scripts/bake_basemap.mjs [sceneCenterLon sceneCenterLat]
 * Output: public/basemap/gwanak_basemap.json
 */
import { PMTiles } from "pmtiles";
import fs from "node:fs";
import Pbf from "pbf";
import vt from "@mapbox/vector-tile";

const { VectorTile } = vt;
const ZOOM = 15;
const BBOX = { w: 126.885, s: 37.425, e: 127.003, n: 37.506 };
const CENTER = [Number(process.argv[2] ?? 126.9440445), Number(process.argv[3] ?? 37.4653561)];
const R = 6371008.8 * (Math.PI / 180);
const KX = R * Math.cos((CENTER[1] * Math.PI) / 180);
const toM = (lon, lat) => [Math.round((lon - CENTER[0]) * KX * 10) / 10, Math.round((lat - CENTER[1]) * R * 10) / 10];
const tileX = (lon) => Math.floor(((lon + 180) / 360) * 2 ** ZOOM);
const tileY = (lat) => Math.floor(((1 - Math.log(Math.tan((lat * Math.PI) / 180) + 1 / Math.cos((lat * Math.PI) / 180)) / Math.PI) / 2) * 2 ** ZOOM);

const buf = fs.readFileSync("public/basemap/gwanak.pmtiles");
const pm = new PMTiles({ getKey: () => "f", getBytes: async (o, l) => ({ data: buf.buffer.slice(buf.byteOffset + o, buf.byteOffset + o + l) }) });

const LAND = new Set(["park", "forest", "wood", "grass", "grassland", "meadow", "scrub", "garden", "village_green", "farmland", "cemetery", "golf_course", "playground", "pitch", "dog_park", "bare_rock", "sand", "school", "college", "university", "hospital", "industrial", "commercial", "residential", "military", "pedestrian", "railway", "platform", "kindergarten"]);
const LABEL_POIS = new Set(["station", "university", "townhall", "peak", "hospital", "college", "stadium", "museum", "library", "park"]);
const out = { source: "© OpenStreetMap contributors, Protomaps (ODbL)", build: "20261024", center: CENTER, land: [], water: [], waterLines: [], roads: [], rail: [], pois: [], places: [] };
const flat = (ring) => ring.flatMap(([lon, lat]) => toM(lon, lat));
const nameOf = (p) => p["name:ko"] ?? p.name ?? null;

for (let x = tileX(BBOX.w); x <= tileX(BBOX.e); x++) for (let y = tileY(BBOX.n); y <= tileY(BBOX.s); y++) {
  const t = await pm.getZxy(ZOOM, x, y);
  if (!t) continue;
  const tile = new VectorTile(new Pbf(Buffer.from(t.data)));
  const each = (layer, fn) => { const l = tile.layers[layer]; if (l) for (let i = 0; i < l.length; i++) { const f = l.feature(i); fn(f.properties, f.toGeoJSON(x, y, ZOOM).geometry); } };
  const polys = (g) => (g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : []);
  const lines = (g) => (g.type === "LineString" ? [g.coordinates] : g.type === "MultiLineString" ? g.coordinates : []);
  each("landuse", (p, g) => { if (LAND.has(p.kind)) for (const pg of polys(g)) out.land.push({ k: p.kind, n: nameOf(p) ?? undefined, r: pg.map(flat) }); });
  each("natural", (p, g) => { for (const pg of polys(g)) out.land.push({ k: p.kind, r: pg.map(flat) }); });
  each("water", (p, g) => {
    for (const pg of polys(g)) if (p.kind !== "swimming_pool" && p.kind !== "fountain") out.water.push({ r: pg.map(flat) });
    for (const ln of lines(g)) out.waterLines.push({ k: p.kind_detail ?? p.kind, n: nameOf(p) ?? undefined, p: flat(ln) });
  });
  each("roads", (p, g) => {
    for (const ln of lines(g)) {
      if (p.kind === "rail") out.rail.push({ d: p.kind_detail, p: flat(ln) });
      else if (p.kind !== "aerialway") out.roads.push({ k: p.kind, d: p.kind_detail, n: nameOf(p) ?? undefined, p: flat(ln) });
    }
  });
  each("pois", (p, g) => { const n = nameOf(p); if (n && LABEL_POIS.has(p.kind) && g.type === "Point") out.pois.push({ k: p.kind, n, ...(() => { const [mx, my] = toM(g.coordinates[0], g.coordinates[1]); return { x: mx, y: my }; })(), e: p.elevation }); });
  each("places", (p, g) => { const n = nameOf(p); if (n && g.type === "Point") { const [mx, my] = toM(g.coordinates[0], g.coordinates[1]); out.places.push({ k: p.kind, n, x: mx, y: my, r: p["pmap:min_zoom"] }); } });
}
fs.writeFileSync("public/basemap/gwanak_basemap.json", JSON.stringify(out));
const c = (a) => a.length;
console.log(`land ${c(out.land)} water ${c(out.water)} waterLines ${c(out.waterLines)} roads ${c(out.roads)} rail ${c(out.rail)} pois ${c(out.pois)} places ${c(out.places)} size ${(fs.statSync("public/basemap/gwanak_basemap.json").size / 1e6).toFixed(2)} MB`);
console.log("land names", [...new Set(out.land.filter((l) => l.n && ["university", "college", "park", "forest", "wood", "hospital"].includes(l.k)).map((l) => `${l.k}:${l.n}`))].join(", "));
console.log("streams", [...new Set(out.waterLines.map((l) => l.n).filter(Boolean))].join(", "));
console.log("peaks", out.pois.filter((p) => p.k === "peak").map((p) => p.n).join(", "), "gu", out.pois.filter((p) => /구청|서울대/.test(p.n)).map((p) => `${p.k}:${p.n}`).join(", "));
console.log("stations", out.pois.filter((p) => p.k === "station").map((p) => p.n).join(", "));
console.log("univ/town/peak", out.pois.filter((p) => ["university", "townhall", "peak", "college", "hospital"].includes(p.k)).map((p) => `${p.k}:${p.n}`).slice(0, 60).join(", "));
console.log("places", out.places.map((p) => `${p.k}:${p.n}`).join(", "));
console.log("roadnames", [...new Set(out.roads.filter((r) => r.n && ["highway", "major_road"].includes(r.k)).map((r) => r.n))].join(", "));
