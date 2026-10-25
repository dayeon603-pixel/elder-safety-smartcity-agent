"""Step 3: pull hourly ASOS rain + issued 호우특보 history for 2025 Jun-Sep. READY TO RUN; needs keys.

    DATA_GO_KR_KEY=...  uv run --project /Users/chloekang/kcf/app/backend python fetch_kma.py [--dry-run]
    (optional) KMA_APIHUB_KEY=...  KMA_APIHUB_WRN_URL=<full 특보 조회 URL from your API허브 page>

Endpoints (field names verified against data.go.kr pages + the official 활용가이드 docx, 2026-10-24):
 1. ASOS hourly  : https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList      (data.go.kr 15057210)
      ServiceKey, pageNo, numOfRows, dataType=JSON, dataCd=ASOS, dateCd=HR, startDt=YYYYMMDD, startHh=HH,
      endDt=YYYYMMDD (<= D-1), endHh=HH, stnIds=<ASOS stn no, e.g. 108>.   Response item: tm, stnId, stnNm, rn (mm), rnQcflag ...
 2. 호우특보 history (zone level): https://apis.data.go.kr/1360000/WthrWrnInfoService/getPwnCd   (data.go.kr 15000415, op 특보코드조회)
      ServiceKey, pageNo, numOfRows, dataType=JSON, fromTmFc=YYYYMMDD, toTmFc=YYYYMMDD, warningType=2 (호우), [areaCode, stnId]
      Response item: tmFc, tmSeq, areaCode, areaName, warnVar(2=호우), warnStress(0 주의보/1 경보/2 중대경보),
      command(1발표 2해제 3연장 6정정 7변경발표 8변경해제), startTime, endTime, allEndTime, cancel(1=취소).
    Also cached: getWthrWrnList / getWthrWrnMsg (bulletin text, fallback to parse 해당구역 t2 by regex).
 3. API허브 특보 조회 (15139476 is a LINK to apihub.kma.go.kr): the exact URL is NOT published on the data.go.kr page, so it is
    taken from KMA_APIHUB_WRN_URL and called with authKey=KMA_APIHUB_KEY. Skipped if unset. [UNVERIFIED endpoint]
Unverified: how far back getPwnCd serves (2025 should be inside; if resultCode != 00 on old dates the script logs and moves on).
Raw JSON is cached under /Users/chloekang/kcf/data/cache/extra/rain_kma/ (re-runs skip existing files; keys are never written).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

LOG = logging.getLogger("fetch_kma")
CACHE = Path("/Users/chloekang/kcf/data/cache/extra/rain_kma")
METEOSTAT_IDS = Path("/Users/chloekang/kcf/data/cache/extra/meteostat_daily/ids.txt")
ASOS_URL = "https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"
WRN_BASE = "https://apis.data.go.kr/1360000/WthrWrnInfoService"
START, END = date(2025, 6, 1), date(2025, 9, 30)
PAGE_ROWS = 999
MAX_BYTES = 5_000_000   # untrusted responses: refuse anything larger
SLEEP_S = 0.25
RETRIES = 3


def _key_param(raw: str) -> str:
    """data.go.kr gives both an encoded and a decoded key; normalise to decoded, then urlencode once."""
    return urllib.parse.unquote(raw)


ENV_FILE = Path("/Users/chloekang/kcf/app/.env")


def env_or_dotenv(name: str) -> str:
    """Read a key from the process env, then the macOS keychain (tools/set_key.sh), then app/.env. Never logged."""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        out = subprocess.run(["security", "find-generic-password", "-a", "kcf", "-s", name, "-w"],
                             capture_output=True, text=True, timeout=10, check=False)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() == name:
            return v.strip().strip("'\"")
    return ""


def get_json(url: str, params: dict[str, str]) -> dict:
    q = urllib.parse.urlencode(params)
    last: Exception | None = None
    for i in range(RETRIES):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{url}?{q}", headers={"User-Agent": "kcf-rain/0.1"}), timeout=30) as r:
                body = r.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("response too large")
            return json.loads(body.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as e:  # network / bad JSON (XML error pages land here)
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"giving up: {type(last).__name__}: {last}")


def items_of(resp: dict) -> tuple[str, list[dict], int]:
    h = resp.get("response", {}).get("header", {})
    code = str(h.get("resultCode", "")).lstrip("0") or "0"
    body = resp.get("response", {}).get("body", {}) or {}
    it = (body.get("items") or {})
    it = it.get("item", []) if isinstance(it, dict) else []
    it = [it] if isinstance(it, dict) else it
    return code, it, int(body.get("totalCount", 0) or 0)


def paged(url: str, base: dict[str, str], out: Path, dry: bool) -> int:
    """Fetch all pages into one cached JSON {params-without-key, items}. Returns item count."""
    if out.exists():
        return -1
    if dry:
        LOG.info("DRY would fetch %s -> %s", url.rsplit("/", 1)[-1], out.name)
        return 0
    allit: list[dict] = []
    page = 1
    while True:
        code, it, total = items_of(get_json(url, {**base, "pageNo": str(page), "numOfRows": str(PAGE_ROWS), "dataType": "JSON"}))
        if code not in ("0",):   # '00'/'0' = NORMAL; 03 = NODATA is also benign
            if code == "3":
                break
            LOG.error("API result code %s for %s page %d (see error table in the 활용가이드)", code, out.name, page)
            return -2
        allit += it
        if len(allit) >= total or not it:
            break
        page += 1
        time.sleep(SLEEP_S)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps({"url": url, "params": {k: v for k, v in base.items() if k.lower() not in ("servicekey", "authkey")},
                               "items": allit}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)   # atomic
    time.sleep(SLEEP_S)
    return len(allit)


def asos_station_numbers() -> list[str]:
    """KMA ASOS numbers = last 3 digits of WMO 47xxx ids in the Meteostat cache [est., verify against the guide's station table]."""
    ids = METEOSTAT_IDS.read_text().split()
    return sorted({i[2:] for i in ids if i.startswith("47") and len(i) == 5})


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list requests without calling the API (still needs no key)")
    a = ap.parse_args()
    dkey, hub = env_or_dotenv("DATA_GO_KR_KEY"), env_or_dotenv("KMA_APIHUB_KEY")
    if not dkey and not hub and not a.dry_run:
        LOG.warning("No DATA_GO_KR_KEY / KMA_APIHUB_KEY in environment. Nothing fetched. "
                    "Get keys: data.go.kr -> 활용신청 on 15057210 (ASOS hourly) and 15000415 (기상특보 조회서비스); "
                    "API허브: apihub.kma.go.kr sign-up -> 특보 조회 -> authKey. Then export and rerun.")
        return 0
    n_ok = n_fail = 0
    if dkey or a.dry_run:
        k = {"ServiceKey": _key_param(dkey) if dkey else "DRYRUN"}
        for stn in asos_station_numbers():   # 1) hourly ASOS, one station-month per file
            d = START
            while d <= END:
                e = min(date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1), END)
                out = CACHE / "asos_hourly" / f"{stn}_{d:%Y%m}.json"
                r = paged(ASOS_URL, {**k, "dataCd": "ASOS", "dateCd": "HR", "startDt": f"{d:%Y%m%d}", "startHh": "00",
                                     "endDt": f"{e:%Y%m%d}", "endHh": "23", "stnIds": stn}, out, a.dry_run)
                n_ok += r >= 0
                n_fail += r == -2
                d = e + timedelta(days=1)
        d = START          # 2) zone-level 호우특보 issuance, one month per file (warningType=2)
        while d <= END:
            e = min(date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1), END)
            for op, extra in (("getPwnCd", {"warningType": "2"}), ("getWthrWrnList", {})):
                out = CACHE / "wrn" / f"{op}_{d:%Y%m}.json"
                r = paged(f"{WRN_BASE}/{op}", {**k, "fromTmFc": f"{d:%Y%m%d}", "toTmFc": f"{e:%Y%m%d}", **extra}, out, a.dry_run)
                n_ok += r >= 0
                n_fail += r == -2
            d = e + timedelta(days=1)
    url = env_or_dotenv("KMA_APIHUB_WRN_URL")
    if hub and not url:
        LOG.warning("KMA_APIHUB_KEY set but KMA_APIHUB_WRN_URL unset; the 특보 조회 URL is only on your API허브 page. Skipping API허브.")
    elif hub and url and not a.dry_run:
        r = paged(url, {"authKey": hub, "tmfc1": "20250601", "tmfc2": "20250930"}, CACHE / "apihub_wrn_2025.json", False)  # param names [est., verify]
        n_ok += r >= 0
        n_fail += r == -2
    LOG.info("done: %d files ok/cached, %d failed", n_ok, n_fail)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
