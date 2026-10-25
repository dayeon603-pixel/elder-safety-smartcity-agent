"""KMA API허브 fetcher (ASOS hourly, all stations per call) with a resume-safe raw-response cache.

The key is read from the macOS keychain at call time, passed only in the request URL, and scrubbed from
any error text. It is never logged and never written to disk (cache files hold the response body only).
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger("heat.kma_hub")

BASE = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php"
CACHE_DIR = Path("/Users/chloekang/kcf/data/cache/extra/kma_hub/sfctm2")
THROTTLE_S = 0.2
TIMEOUT_S = 60
RETRIES = 4
OK_MARK = "#7777END"  # the hub ends a complete response with this footer


class HubError(RuntimeError):
    """Hub request failed (message is already key-scrubbed)."""


def _key() -> str:
    r = subprocess.run(["security", "find-generic-password", "-a", "kcf", "-s", "KMA_APIHUB_KEY", "-w"],
                       capture_output=True, text=True, check=False)
    k = r.stdout.strip()
    if not k:
        raise HubError("KMA_APIHUB_KEY not found in keychain")
    return k


def cache_path(tm: str) -> Path:
    return CACHE_DIR / f"{tm}.txt"


_LOCK = threading.Lock()


def fetch_hour(tm: str, key: str, last_call: list[float]) -> str:
    """Return the raw euc-kr-decoded body for tm='YYYYMMDDHH00' (cached; resume-safe)."""
    p = cache_path(tm)
    if p.is_file() and p.stat().st_size > 0:
        return p.read_text(encoding="utf-8")
    url = f"{BASE}?tm={tm}&stn=0&help=0&authKey={key}"
    err = ""
    for attempt in range(RETRIES):
        with _LOCK:  # global throttle: calls are *started* at most every THROTTLE_S across all threads
            wait = THROTTLE_S - (time.monotonic() - last_call[0])
            if wait > 0:
                time.sleep(wait)
            last_call[0] = time.monotonic()
        try:
            body = urllib.request.urlopen(url, timeout=TIMEOUT_S).read().decode("euc-kr", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            err = str(exc).replace(key, "***")
            time.sleep(1.5 * (attempt + 1))
            continue
        if "#START7777" in body and OK_MARK in body:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(p)
            return body
        err = "incomplete response: " + body[:120].replace(key, "***")
        time.sleep(1.5 * (attempt + 1))
    raise HubError(f"{tm}: {err}")
