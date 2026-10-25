# Basemap credits

gwanak.pmtiles / gwanak_basemap.json: © OpenStreetMap contributors, Protomaps (ODbL 1.0). Protomaps daily build 20261024, extract bbox 126.885,37.425,127.003,37.506 (관악구 + ~1 km), max zoom 15.
Rebuild: pmtiles extract https://build.protomaps.com/<date>.pmtiles public/basemap/gwanak.pmtiles --bbox=126.885,37.425,127.003,37.506 --maxzoom=16 ; node scripts/bake_basemap.mjs
Satellite ground (optional): ⓒ 브이월드, fetched live through the backend proxy, never cached or bundled.
