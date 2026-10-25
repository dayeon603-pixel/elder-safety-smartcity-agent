"""data.go.kr client for the NMC services: key resolution, daily quota, XML parsing. The key is never logged."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from lxml import etree

from . import constants as C

logger = logging.getLogger(__name__)
_KEY_RE = re.compile(r"(serviceKey=)[^&\s\"']+")


class _RedactKey(logging.Filter):
    """httpx logs every request URL at INFO; the data.go.kr key travels in the query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "serviceKey=" in msg:
            record.msg, record.args = _KEY_RE.sub(r"\1<KEY>", msg), ()
        return True


for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_RedactKey())


class NmcError(RuntimeError):
    """The API could not be used: no key, quota spent, transport error, non-00 result, malformed XML."""


KEYCHAIN_SENTINEL = "keychain"


def resolve_key(env_value: str | None) -> str | None:
    """`KCF_DATA_GO_KR_KEY`: the key itself, or the word `keychain` to read the macOS keychain item the
    fetch scripts use (opt-in, so tests and other machines never touch the keychain). None = snapshots only."""
    if not env_value:
        return None
    if env_value != KEYCHAIN_SENTINEL:
        return env_value
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv
            ["security", "find-generic-password", "-a", C.KEYCHAIN_ACCOUNT, "-s", C.KEYCHAIN_SERVICE, "-w"],
            check=True, capture_output=True, timeout=C.KEYCHAIN_TIMEOUT_S, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


class DailyQuota:
    """Requests-per-day counter persisted in `dir/quota.json`; `take()` refuses beyond `limit`."""

    def __init__(self, directory: Path, limit: int) -> None:
        self._path = directory / C.QUOTA_FILE
        self._limit = limit
        self._lock = threading.Lock()

    def _read(self, today: str) -> int:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        return int(data.get("count", 0)) if data.get("date") == today else 0

    def used(self) -> int:
        return self._read(datetime.now(timezone.utc).date().isoformat())

    def take(self) -> None:
        """Consume one request or raise NmcError when today's quota is spent."""
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            n = self._read(today)
            if n >= self._limit:
                raise NmcError(f"daily quota {self._limit} spent")
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"date": today, "count": n + 1}), encoding="utf-8")
            tmp.replace(self._path)


def parse_items(xml: bytes) -> tuple[list[dict[str, str]], int]:
    """(items, totalCount) from a data.go.kr XML body.

    Raises:
        NmcError: malformed XML or resultCode != 00.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        root = etree.fromstring(xml, parser)
    except etree.XMLSyntaxError as exc:
        raise NmcError(f"malformed XML ({exc.msg[:60]})") from exc
    code = root.findtext("header/resultCode")
    if code != C.RESULT_OK:
        raise NmcError(f"resultCode {code}: {(root.findtext('header/resultMsg') or '')[:80]}")
    items = [{child.tag: (child.text or "").strip() for child in item} for item in root.findall("body/items/item")]
    total_txt = root.findtext("body/totalCount") or str(len(items))
    return items, int(total_txt) if total_txt.isdigit() else len(items)


class NmcClient:
    """Fetch one service for one 시군구, following pagination (bounded), counting against the quota."""

    def __init__(self, http: httpx.Client, key: str, quota: DailyQuota) -> None:
        self._http = http
        self._key = key
        self._quota = quota

    def fetch(self, service: str, sido: str, sgg: str) -> list[dict[str, str]]:
        """All items of `service` for (sido, sgg).

        Raises:
            NmcError: quota, transport, HTTP status, or API error (the key never appears in the message).
        """
        path, (p_sido, p_sgg), rows = C.SERVICES[service]
        out: list[dict[str, str]] = []
        page = 1
        while page <= C.MAX_PAGES:
            self._quota.take()
            params: dict[str, Any] = {"serviceKey": self._key, p_sido: sido, p_sgg: sgg, "pageNo": page, "numOfRows": rows}
            try:
                resp = self._http.get(f"{C.BASE_URL}/{path}", params=params,
                                      timeout=httpx.Timeout(connect=C.CONNECT_TIMEOUT_S, read=C.READ_TIMEOUT_S,
                                                            write=C.READ_TIMEOUT_S, pool=C.READ_TIMEOUT_S))
            except httpx.HTTPError as exc:  # message may embed the URL (and key): keep only the type
                raise NmcError(f"transport error ({type(exc).__name__})") from exc
            if resp.status_code != 200:
                raise NmcError(f"HTTP {resp.status_code}")
            items, total = parse_items(resp.content)
            out.extend(items)
            if len(out) >= total or not items:
                break
            page += 1
        logger.info("nmc %s %s %s: %d items", service, sido, sgg, len(out))
        return out


__all__ = ["DailyQuota", "NmcClient", "NmcError", "parse_items", "resolve_key"]
