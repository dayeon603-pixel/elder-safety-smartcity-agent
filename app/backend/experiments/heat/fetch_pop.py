"""Fetch year-end 시군구 x 5-year-age resident population (행정안전부 주민등록 인구통계, jumin.mois.go.kr, no login).

Snapshot for season year Y = December of Y-1 (published before the season starts, so no lookahead).
Raw CSV (cp949) responses are cached under data/cache/extra/mois_pop/ (resume-safe, never rewritten).
Run: uv run --project /Users/chloekang/kcf/app/backend python experiments/heat/fetch_pop.py
"""

from __future__ import annotations

import io
import logging
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

logger = logging.getLogger("heat.pop")

URL = "https://jumin.mois.go.kr/downloadCsvAge.do?searchYearMonth=month&xlsStats=1"
REFERER = "https://jumin.mois.go.kr/ageStatMonth.do"
POP_CACHE = Path("/Users/chloekang/kcf/data/cache/extra/mois_pop")
SNAPSHOT_YEARS = list(range(2018, 2025))  # December of these years -> season years 2019..2025
THROTTLE_S = 1.0
ROW_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<code>\d{10})\)$")


def _post(lvl1: str, lvl2: str, org_type: int, year: int, month: int) -> bytes:
    body = urllib.parse.urlencode({
        "sltOrgType": org_type, "sltOrgLvl1": lvl1, "sltOrgLvl2": lvl2, "gender": "gender", "sum": "sum",
        "sltUndefType": "", "searchYearStart": year, "searchMonthStart": f"{month:02d}", "searchYearEnd": year,
        "searchMonthEnd": f"{month:02d}", "sltOrderType": 1, "sltOrderValue": "ASC", "sltArgTypes": 5,
        "sltArgTypeA": 0, "sltArgTypeB": 100, "category": "month"}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Referer": REFERER})
    for attempt in range(4):
        try:
            return urllib.request.urlopen(req, timeout=90).read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("retry %d: %s", attempt, exc)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"jumin request failed {lvl1}/{lvl2}/{year}-{month}")


def cached(name: str, **kw) -> bytes:
    p = POP_CACHE / name
    if p.is_file() and p.stat().st_size > 0:
        return p.read_bytes()
    time.sleep(THROTTLE_S)
    raw = _post(**kw)
    POP_CACHE.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)
    return raw


def parse(raw: bytes) -> pd.DataFrame:
    """Rows -> (name, code, pop, pop65) from the 계(both sexes) block."""
    df = pd.read_csv(io.BytesIO(raw), encoding="cp949", thousands=",")
    cols = list(df.columns)
    total_col = next(c for c in cols if c.endswith("_계_총인구수"))
    age65 = [c for c in cols if "_계_" in c and re.search(r"_계_(65~69|70~74|75~79|80~84|85~89|90~94|95~99|100세 이상)", c)]
    assert len(age65) == 8, age65
    m = df.iloc[:, 0].str.extract(ROW_RE)
    out = pd.DataFrame({"name": m["name"], "code": m["code"], "pop": df[total_col], "pop65": df[age65].sum(axis=1)})
    return out.dropna(subset=["code"]).reset_index(drop=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    frames = []
    for y in SNAPSHOT_YEARS:
        top = parse(cached(f"sido_{y}12.csv", lvl1="A", lvl2="", org_type=1, year=y, month=12))
        sido = top[top.code != "0000000000"]
        for code in sido.code:
            child = parse(cached(f"sgg_{code}_{y}12.csv", lvl1=code, lvl2="A", org_type=2, year=y, month=12))
            child["snapshot"] = f"{y}-12"
            frames.append(child)
        s = sido.copy()
        s["snapshot"] = f"{y}-12"
        frames.append(s)
        logger.info("%d-12 done", y)
    allp = pd.concat(frames, ignore_index=True).drop_duplicates(["snapshot", "code"])
    out = POP_CACHE / "sgg_pop_dec_snapshots.csv"
    allp.to_csv(out, index=False, encoding="utf-8-sig")
    logger.info("rows=%d -> %s", len(allp), out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
