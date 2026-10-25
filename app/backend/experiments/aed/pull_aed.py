"""Pull NMC getAedFullDown (all pages), cache raw pages as gz JSON. Key from keychain, never printed."""
from __future__ import annotations
import gzip, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pull119 as P  # reuses key, throttle, scrub (importing pulls key only)
import urllib.parse, urllib.request, time

CACHE = Path("/Users/chloekang/kcf/data/cache/extra/ems/api/aed"); CACHE.mkdir(parents=True, exist_ok=True)
BASE = "https://apis.data.go.kr/B552657/AEDInfoInqireService/getAedFullDown"
ROWS = 1000

def get(page: int) -> dict:
    f = CACHE / f"full_{page:03d}.json.gz"
    if f.exists(): return json.load(gzip.open(f, "rt", encoding="utf-8"))
    q = urllib.parse.urlencode({"pageNo": page, "numOfRows": ROWS, "_type": "json"})
    u = BASE + "?serviceKey=" + urllib.parse.quote(P._K, safe="") + "&" + q
    for att in range(4):
        P._throttle()
        try:
            r = urllib.request.urlopen(u, timeout=90).read().decode("utf-8", "replace")
            break
        except Exception as e:
            print("retry", type(e).__name__, P.scrub(str(e))[:80]); time.sleep(2 ** att)
    else:
        raise RuntimeError("fail")
    f.with_suffix(".raw").write_text(r[:0])  # placeholder to keep atomic style simple
    f.with_suffix(".raw").unlink()
    with gzip.open(f, "wt", encoding="utf-8") as g: g.write(r)
    return json.loads(r)

if __name__ == "__main__":
    p = 1; n = 0
    while True:
        try: j = get(p)
        except json.JSONDecodeError:
            f = CACHE / f"full_{p:03d}.json.gz"; txt = gzip.open(f, "rt").read()[:400]; print("non-json (XML?)", P.scrub(txt)); break
        r = j.get("response", j); b = r.get("body", {}); it = b.get("items", {})
        it = it.get("item", []) if isinstance(it, dict) else it
        it = [it] if isinstance(it, dict) else it
        if p == 1: print("totalCount", b.get("totalCount"))
        n += len(it)
        if not it or n >= int(b.get("totalCount") or 0): break
        p += 1
    print("rows", n, "pages", p)
