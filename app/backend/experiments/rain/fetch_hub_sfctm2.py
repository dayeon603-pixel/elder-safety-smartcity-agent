"""Hourly 지상관측 (API허브 kma_sfctm2.php), all stations per call, 2025-06-01..09-30 KST. Resume-safe raw-text cache.

Verified header (help=1): col16 RN = 강수량(mm), 4~10월 = 1시간 강수량; -9/-99 = missing. Key never printed.
"""
from __future__ import annotations

import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_kma import env_or_dotenv  # noqa: E402

CACHE = Path("/Users/chloekang/kcf/data/cache/extra/rain_kma/hub_sfctm2")
URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php?tm={tm}&stn=0&help=0&authKey={k}"
LOG = logging.getLogger("hub")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    k = env_or_dotenv("KMA_APIHUB_KEY")
    if not k:
        LOG.warning("KMA_APIHUB_KEY not found (env/keychain/.env); nothing fetched")
        return 0
    CACHE.mkdir(parents=True, exist_ok=True)
    a = sys.argv[1:]
    t = datetime.strptime(a[0], "%Y%m%d") if a else datetime(2025, 6, 1)
    end = datetime.strptime(a[1], "%Y%m%d").replace(hour=23) if len(a) > 1 else datetime(2025, 9, 30, 23)
    n, bad = 0, 0
    while t <= end:
        out = CACHE / f"{t:%Y%m%d%H}.txt"
        if not out.exists():
            for i in range(3):
                try:
                    with urllib.request.urlopen(URL.format(tm=f"{t:%Y%m%d%H}00", k=k), timeout=30) as r:
                        body = r.read(3_000_000)
                    if b"#START7777" not in body and b"2025" not in body[:200]:
                        raise ValueError("unexpected body")
                    out.with_suffix(".tmp").write_bytes(body)
                    out.with_suffix(".tmp").replace(out)
                    n += 1
                    break
                except (urllib.error.URLError, TimeoutError, ValueError) as e:
                    msg = str(e).replace(k, "***")
                    LOG.warning("%s try %d: %s", t, i, type(e).__name__ + " " + msg[:80])
                    time.sleep(2 * (i + 1))
            else:
                bad += 1
            time.sleep(0.2)
        t += timedelta(hours=1)
    LOG.info("fetched %d new, %d failed", n, bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
