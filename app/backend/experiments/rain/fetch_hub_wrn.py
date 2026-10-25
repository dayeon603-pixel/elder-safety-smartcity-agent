"""Issued 호우특보 history from API허브 wrn_met_data.php (wrn=H). Runs when the 활용신청 is approved; exits 0 on 403.

Params (per coordinator): wrn=H, reg=0 (all), tmfc1/tmfc2=YYYYMMDDHHMM (발표시각), disp=0, help=1. Raw text cached by week.
Parsing is NOT written yet: the column layout is only known from the help=1 header once access works. [est., verify]
"""
from __future__ import annotations

import logging
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_kma import env_or_dotenv  # noqa: E402

CACHE = Path("/Users/chloekang/kcf/data/cache/extra/rain_kma/hub_wrn")
URL = "https://apihub.kma.go.kr/api/typ01/url/wrn_met_data.php?wrn=H&reg=0&tmfc1={a}&tmfc2={b}&disp=0&help=1&authKey={k}"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("hub_wrn")
    k = env_or_dotenv("KMA_APIHUB_KEY")
    if not k:
        log.warning("no KMA_APIHUB_KEY")
        return 0
    CACHE.mkdir(parents=True, exist_ok=True)
    t = datetime(2025, 5, 1)
    while t < datetime(2025, 10, 1):
        n = t + timedelta(days=7)
        out = CACHE / f"{t:%Y%m%d}.txt"
        if not out.exists():
            try:
                with urllib.request.urlopen(URL.format(a=f"{t:%Y%m%d}0000", b=f"{n:%Y%m%d}0000", k=k), timeout=30) as r:
                    out.write_bytes(r.read(5_000_000))
            except urllib.error.HTTPError as e:
                log.warning("HTTP %s (403 = 활용신청 not approved yet). Stopping, will succeed once approved.", e.code)
                return 0
            except (urllib.error.URLError, TimeoutError) as e:
                log.warning("%s", type(e).__name__)
                return 1
        t = n
    log.info("done; raw files in %s", CACHE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
