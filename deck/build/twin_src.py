"""Numbers for the /twin demo slides, read from the demo backend and cached so the build also works offline.

Source of truth is the running demo API (GET /twin/11620/scene?meta=1 and /twin/11620/actions). The first successful read
writes twin_meta.json next to this file; later builds without the API reuse that file and say so in the ledger.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LOG = logging.getLogger("twin_src")
BASE = "http://localhost:8010"
SGG = "11620"
DATE = "2025-07-22"
HOUR = 17
CACHE = Path(__file__).resolve().parent / "twin_meta.json"
TIMEOUT_S = 10
CBS_ID = "cbs"
DAY_HOUR = 14
NIGHT_HOUR = 23


def _get(path: str) -> Any:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=TIMEOUT_S) as r:  # noqa: S310 (fixed localhost URL)
        return json.loads(r.read())


def load() -> tuple[dict[str, Any], str]:
    """Return ({aed_*, buildings, measured_pct, cbs_std_len, cbs_std_limit, cbs_pilot_len, cbs_pilot_limit}, provenance)."""
    try:
        meta = _get(f"/twin/{SGG}/scene?meta=1")
        acts = _get(f"/twin/{SGG}/actions?date={DATE}&hour={HOUR}")["actions"]
        cbs = next(a for a in acts if a["id"] == CBS_ID)
        std, pilot = cbs["drafts"][0], cbs["drafts"][1]
        day, night = _get(f"/twin/{SGG}/state?date={DATE}&hour={DAY_HOUR}"), _get(f"/twin/{SGG}/state?date={DATE}&hour={NIGHT_HOUR}")
        nacts = _get(f"/twin/{SGG}/actions?date={DATE}&hour={NIGHT_HOUR}")["actions"]
        conv = next(a for a in nacts if a["id"].startswith("aed.convert"))
        gain = int(re.search(r"\+(\d+)명", conv["evidence_text"]).group(1))
        zon = day["silver_eval_seoul"]
        out = {"zones_cur": zon["seoul_zones_current"], "zones_ours": zon["seoul_zones_ours"], "aed_total": day["summary"]["aed_total"], "aed_pct_day": day["summary"]["aed_open_pct"], "aed_pct_night": night["summary"]["aed_open_pct"],
               "aed_unreg": sum(1 for a in night["aed"] if not a.get("hours")), "aed_gain65": gain,
               "buildings": meta["buildings"], "measured_pct": meta["measured_pct"],
               "cbs_std_len": std["length"], "cbs_std_limit": std["limit"], "cbs_pilot_len": pilot["length"], "cbs_pilot_limit": pilot["limit"]}
        CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        return out, f"live {BASE}"
    except (urllib.error.URLError, OSError, KeyError, StopIteration, ValueError) as exc:
        if not CACHE.is_file():
            raise RuntimeError(f"demo API unreachable and no cache {CACHE.name}: {exc}") from exc
        LOG.warning("demo API unavailable (%s); using cached %s", exc, CACHE.name)
        return json.loads(CACHE.read_text(encoding="utf-8")), f"cache {CACHE.name}"
