"""Download + cache the OSM pedestrian network and buildings for Seoul (+1 km buffer).
Data (c) OpenStreetMap contributors, ODbL 1.0. Cached under data/cache/extra/osm/."""
from __future__ import annotations
import json, logging, sys
from pathlib import Path
import osmnx as ox
from shapely.geometry import shape
from shapely.ops import unary_union

log = logging.getLogger("walk4.osm")
OSM = Path("/Users/chloekang/kcf/data/cache/extra/osm")
DONG = Path("/Users/chloekang/kcf/data/cache/extra/boundaries/HangJeongDong_ver20261001.geojson")


def seoul_poly():
    j = json.loads(DONG.read_text(encoding="utf-8"))
    gs = [shape(f["geometry"]) for f in j["features"] if f["properties"]["sido"] == "11"]
    return unary_union(gs)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ox.settings.cache_folder = str(OSM / "http_cache"); ox.settings.use_cache = True
    ox.settings.requests_timeout = 600; ox.settings.max_query_area_size = 50_000_000_000
    poly = seoul_poly().buffer(0.01)  # ~1 km so edge AEDs/nodes are not truncated
    gpath = OSM / "seoul_walk.graphml"
    if not gpath.exists():
        G = ox.graph_from_polygon(poly, network_type="walk", simplify=True, retain_all=True)
        ox.save_graphml(G, gpath)
        log.info("graph nodes=%d edges=%d", len(G), G.number_of_edges())
    bpath = OSM / "seoul_buildings.parquet"
    if not bpath.exists():
        b = ox.features_from_polygon(poly.simplify(0.0005), tags={"building": True})
        b = b[b.geometry.type.isin(["Polygon", "MultiPolygon"])][["building", "building:levels", "geometry"]]
        b.to_parquet(bpath); log.info("buildings=%d", len(b))

if __name__ == "__main__":
    main()
