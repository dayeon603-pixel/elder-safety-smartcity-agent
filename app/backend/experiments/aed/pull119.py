"""Pull 소방청 getEmgencyActivityInfo for Seoul's 25 소방서 (resume-safe, throttled).

Cache: one gz JSON per (station, yyyymm) under data/cache/extra/ems/api/act/.
Key comes from the macOS keychain, never printed/logged; scrubbed from errors.
Responses are untrusted: parsed as JSON only, never evaluated.
"""
from __future__ import annotations
import gzip, json, logging, subprocess, sys, time, threading, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CACHE = Path("/Users/chloekang/kcf/data/cache/extra/ems/api/act")
BASE = "https://apis.data.go.kr/1661000/EmergencyInformationService/getEmgencyActivityInfo"
STATIONS = ["강남","강동","강북","강서","관악","광진","구로","금천","노원","도봉","동대문","동작","마포",
            "서대문","서초","성동","성북","송파","양천","영등포","용산","은평","종로","중부","중랑"]
THROTTLE_S = 0.12
import os
ROWS = int(os.environ.get("EMS_ROWS", "1000"))
WORKERS = 3
log = logging.getLogger("pull119")
_K = subprocess.check_output(["security","find-generic-password","-a","kcf","-s","DATA_GO_KR_KEY","-w"]).decode().strip()
_lock = threading.Lock(); _last = [0.0]
quota_hit = threading.Event()

def scrub(s: str) -> str:
    return s.replace(_K, "<KEY>").replace(urllib.parse.quote(_K, safe=""), "<KEY>")

def _throttle() -> None:
    with _lock:
        w = _last[0] + THROTTLE_S - time.time()
        if w > 0: time.sleep(w)
        _last[0] = time.time()

def call(station: str, ym: str, page: int) -> dict:
    q = urllib.parse.urlencode({"gutYm": ym, "rsacGutFsttOgidNm": station + "소방서", "pageNo": page,
                                "numOfRows": ROWS, "resultType": "json"})
    u = BASE + "?serviceKey=" + urllib.parse.quote(_K, safe="") + "&" + q
    for att in range(4):
        _throttle()
        try:
            r = urllib.request.urlopen(u, timeout=60).read().decode("utf-8", "replace")
            return json.loads(r)
        except urllib.error.HTTPError as e:
            msg = scrub(str(e))
            if e.code in (429,): quota_hit.set()
            log.warning("http %s %s %s %s", e.code, station, ym, msg[:80])
        except Exception as e:  # network/json
            log.warning("err %s %s %s", type(e).__name__, station, ym)
        time.sleep(2 ** att)
    raise RuntimeError(f"failed {station} {ym}")

def body(j: dict) -> tuple[list[dict], int]:
    b = j.get("body") or j.get("response", {}).get("body", {})
    if not isinstance(b, dict): return [], 0
    it = b.get("items") or {}
    if isinstance(it, dict): it = it.get("item", [])
    if isinstance(it, dict): it = [it]
    if not isinstance(it, list): it = []
    it = [x for x in it if isinstance(x, dict)]
    try: tc = int(j.get("totalCount") or b.get("totalCount") or 0)
    except (TypeError, ValueError): tc = 0
    return it, tc

def fetch(station: str, ym: str) -> int:
    f = CACHE / f"{station}_{ym}.json.gz"
    if f.exists(): return -1
    if quota_hit.is_set(): return -2
    out: list[dict] = []; p = 1; tc = 0
    while True:
        it, t = body(call(station, ym, p))
        if p == 1: tc = t
        out += it
        if not it or len(out) >= tc: break
        p += 1
    if tc == 0 and out: raise RuntimeError("totalCount unparsed")
    if len(out) != tc: log.warning("count mismatch %s %s got %d tc %d", station, ym, len(out), tc)
    tmp = f.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as g: json.dump({"totalCount": tc, "items": out}, g, ensure_ascii=False)
    tmp.rename(f)
    return len(out)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    CACHE.mkdir(parents=True, exist_ok=True)
    years = [int(y) for y in sys.argv[1].split(",")] if len(sys.argv) > 1 else [2024, 2025, 2023, 2022, 2021, 2020, 2019]
    jobs = [(s, f"{y}{m:02d}") for y in years for m in range(1, 13) for s in STATIONS]
    done = [0]; rows = [0]
    def run(j):
        n = fetch(*j)
        if n >= 0:
            rows[0] += n
        done[0] += 1
        if done[0] % 25 == 0: log.info("jobs %d/%d rows %d", done[0], len(jobs), rows[0])
    with ThreadPoolExecutor(WORKERS) as ex: list(ex.map(run, jobs))
    log.info("DONE rows %d", rows[0])
