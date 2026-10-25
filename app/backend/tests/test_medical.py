"""NMC medical module: XML parsing, hours/holidays, TTL cache with stale/seed fallback, quota, endpoints, agent tool."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.agent.ollama import OllamaClient
from app.config import Settings, load_settings
from app.main import create_app
from app.medical import hours
from app.medical.cache import MedicalCache
from app.medical.client import DailyQuota, NmcClient, NmcError, parse_items
from app.medical.service import KST
from tests import heat_fixture as fx
from tests.conftest import TEST_SECRET

CODE, DATE = "11110", fx.DATES[1]  # 2024-07-02 is a Tuesday
FALL: dict[str, Any] = {"trigger": "fall", "context": {"code": CODE, "date": DATE, "lat": 37.57, "lon": 126.98}}
KEY = "k" * 40


def _xml(items: list[dict[str, str]], code: str = "00", total: int | None = None) -> bytes:
    body = "".join("<item>" + "".join(f"<{k}>{v}</{k}>" for k, v in it.items()) + "</item>" for it in items)
    return (f'<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>{code}</resultCode>'
            f"<resultMsg>{'NORMAL SERVICE.' if code == '00' else 'ERROR'}</resultMsg></header><body><items>{body}</items>"
            f"<numOfRows>1000</numOfRows><pageNo>1</pageNo><totalCount>{total if total is not None else len(items)}"
            "</totalCount></body></response>").encode("utf-8")


ER_LIST = [{"hpid": "A1", "dutyName": "종로응급병원", "dutyTel3": "02-760-0000", "dutyEmclsName": "지역응급의료기관",
            "wgs84Lat": "37.571", "wgs84Lon": "126.981"},
           {"hpid": "A2", "dutyName": "멀리병원", "dutyTel3": "02-999-0000", "dutyEmclsName": "응급실운영신고기관",
            "wgs84Lat": "37.60", "wgs84Lon": "127.05"}]
ER_BEDS = [{"hpid": "A1", "dutyName": "종로응급병원", "dutyTel3": "02-760-0000", "hvec": "5", "hvicc": "1", "hvoc": "2",
            "hvgc": "30", "hvidate": "20240702213000"}]
PHARM = [
    {"hpid": "C1", "dutyName": "낮약국", "dutyAddr": "a", "dutyTel1": "02-1", "wgs84Lat": "37.57", "wgs84Lon": "126.98",
     **{f"dutyTime{i}s": "0900" for i in range(1, 7)}, **{f"dutyTime{i}c": "1900" for i in range(1, 7)}},
    {"hpid": "C2", "dutyName": "밤약국", "dutyAddr": "b", "dutyTel1": "02-2", "wgs84Lat": "37.575", "wgs84Lon": "126.985",
     **{f"dutyTime{i}s": "0900" for i in range(1, 8)}, **{f"dutyTime{i}c": "0200" for i in range(1, 8)},
     "dutyTime8s": "1000", "dutyTime8c": "2300"},
    {"hpid": "C3", "dutyName": "시간미상약국", "dutyAddr": "c", "dutyTel1": "02-3", "wgs84Lat": "37.58", "wgs84Lon": "126.99"},
]
HOSP = [{"hpid": "H1", "dutyName": "야간의원", "dutyDivNam": "의원", "dutyEryn": "2", "dutyTel1": "02-4", "wgs84Lat": "37.57",
         "wgs84Lon": "126.98", **{f"dutyTime{i}s": "0900" for i in range(1, 8)}, **{f"dutyTime{i}c": "2400" for i in range(1, 8)}},
        {"hpid": "A1", "dutyName": "종로응급병원", "dutyDivNam": "병원", "dutyEryn": "1", "dutyTel1": "02-760-0000",
         "wgs84Lat": "37.571", "wgs84Lon": "126.981", **{f"dutyTime{i}s": "0000" for i in range(1, 9)},
         **{f"dutyTime{i}c": "2400" for i in range(1, 9)}}]
BY_PATH = {"getEmrrmRltmUsefulSckbdInfoInqire": ER_BEDS, "getEgytListInfoInqire": ER_LIST,
           "getParmacyListInfoInqire": PHARM, "getHsptlMdcncListInfoInqire": HOSP}


def _handler(calls: list[str] | None = None, fail: bool = False):  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(req.url.path.rsplit("/", 1)[-1])
        if fail:
            raise httpx.ConnectError("down")
        return httpx.Response(200, content=_xml(BY_PATH[req.url.path.rsplit("/", 1)[-1]]))
    return handler


def _settings(tmp_path: Path, **extra: str) -> Settings:
    env = {"KCF_SECRET_KEY": TEST_SECRET, "KCF_DB_PATH": str(tmp_path / "m.sqlite3"), "KCF_DATA_DIR": str(tmp_path / "cache"),
           "KCF_MEDICAL_CACHE_DIR": str(tmp_path / "medcache"), "KCF_DATA_GO_KR_KEY": KEY, "KCF_RATE_CAPACITY": "1000",
           "KCF_RATE_REFILL_PER_S": "1000", "KCF_AGENT_RATE_CAPACITY": "1000", "KCF_AGENT_RATE_REFILL_PER_S": "1000", **extra}
    (tmp_path / "cache").mkdir(exist_ok=True)
    return load_settings(env)


def _client(settings: Settings, tmp_path: Path, handler: Any) -> Iterator[TestClient]:
    c = db.connect(settings.db_path)
    db.migrate(c)
    fx.build_heat_db(c, tmp_path / "out")
    c.close()
    with TestClient(create_app(settings)) as client:
        client.app.state.medical_http = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[attr-defined]
        client.app.state.agent_http = client  # type: ignore[attr-defined]

        def down(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client.app.state.agent_ollama = OllamaClient(  # type: ignore[attr-defined]
            httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(down)))
        yield client


# ---------------------------------------------------------------- parsing / hours

def test_parse_items_and_error_codes() -> None:
    items, total = parse_items(_xml(PHARM, total=3))
    assert total == 3 and items[1]["dutyTime8c"] == "2300" and items[2].get("dutyTime1s") is None
    with pytest.raises(NmcError, match="resultCode 22"):
        parse_items(_xml([], code="22"))
    with pytest.raises(NmcError, match="malformed"):
        parse_items(b"<response><header>")


def test_hours_overnight_2400_holiday_and_unknown() -> None:
    tue_2330 = datetime(2024, 7, 2, 23, 30, tzinfo=KST)
    wed_0130 = datetime(2024, 7, 3, 1, 30, tzinfo=KST)
    wed_0230 = datetime(2024, 7, 3, 2, 30, tzinfo=KST)
    assert hours.is_open(PHARM[0], tue_2330) is False and hours.is_open(PHARM[1], tue_2330) is True
    assert hours.is_open(PHARM[1], wed_0130) is True  # Tuesday's hours spill past midnight
    assert hours.is_open(PHARM[1], wed_0230) is False
    assert hours.is_open(PHARM[2], tue_2330) is None  # no hours at all -> unknown, not closed
    assert hours.is_open(HOSP[0], datetime(2024, 7, 2, 23, 59, tzinfo=KST)) is True  # "2400" = end of day
    assert hours.parse_hhmm("2400") == 1440 and hours.parse_hhmm("2560") is None and hours.parse_hhmm("9") is None
    hol = datetime(2026, 2, 17, 12, 0, tzinfo=KST)  # 설날
    assert hours.is_holiday(hol.date()) and hours.day_index(hol.date()) == 8
    assert hours.is_open(PHARM[1], hol) is True and hours.is_open(PHARM[0], hol) is False  # no dutyTime8 -> closed
    assert hours.is_holiday(datetime(2026, 3, 2).date()) and hours.is_holiday(datetime(2026, 10, 9).date())
    assert not hours.is_holiday(datetime(2026, 10, 10).date())
    assert hours.is_night(datetime(2024, 7, 2, 22, 0, tzinfo=KST)) and not hours.is_night(datetime(2024, 7, 2, 6, 0, tzinfo=KST))


def test_hours_fix_midnight_edge() -> None:
    # A Wednesday-00:00 probe for a 0900-2400 place: Tuesday's span ends exactly at 24:00, so it is closed.
    assert hours.is_open(HOSP[0], datetime(2024, 7, 3, 0, 0, tzinfo=KST)) is False


# ---------------------------------------------------------------- cache / quota / client

def test_cache_memory_disk_live_stale_and_seed(tmp_path: Path) -> None:
    now = [datetime(2024, 7, 2, 12, 0, tzinfo=timezone.utc)]
    seed = tmp_path / "seed"
    cache = MedicalCache(tmp_path / "c", seed, ttl_s={"pharmacy": 60.0}, clock=lambda: now[0])
    cache.write_snapshot("pharmacy", "서울특별시", "종로구", PHARM[:1], directory=seed)
    calls = [0]

    def fetcher() -> list[dict[str, str]]:
        calls[0] += 1
        return PHARM

    f = cache.get("pharmacy", "서울특별시", "종로구", fetcher)
    assert f is not None and f.source == "live" and len(f.items) == 3 and not f.stale and calls[0] == 1
    assert len(f.sha256) == 64
    assert cache.get("pharmacy", "서울특별시", "종로구", fetcher).source == "memory" and calls[0] == 1  # type: ignore[union-attr]
    cache2 = MedicalCache(tmp_path / "c", seed, ttl_s={"pharmacy": 60.0}, clock=lambda: now[0])
    assert cache2.get("pharmacy", "서울특별시", "종로구", fetcher).source == "snapshot" and calls[0] == 1  # type: ignore[union-attr]
    now[0] += timedelta(hours=2)  # TTL over: live is tried; when it fails the stale snapshot is served with its age

    def broken() -> list[dict[str, str]]:
        raise NmcError("quota spent")

    stale = cache2.get("pharmacy", "서울특별시", "종로구", broken)
    assert stale is not None and stale.stale and stale.source == "snapshot" and stale.age_s == 7200 and stale.error == "quota spent"
    nothing = MedicalCache(tmp_path / "empty", seed, ttl_s={"pharmacy": 60.0}, clock=lambda: now[0])
    seeded = nothing.get("pharmacy", "서울특별시", "종로구", broken)
    assert seeded is not None and seeded.source == "seed" and seeded.stale and len(seeded.items) == 1
    assert nothing.get("pharmacy", "서울특별시", "중구", None) is None  # no live, no snapshot, no seed


def test_quota_and_key_never_in_errors_or_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    q = DailyQuota(tmp_path, 2)
    q.take()
    q.take()
    with pytest.raises(NmcError, match="quota"):
        q.take()
    assert q.used() == 2
    client = NmcClient(httpx.Client(transport=httpx.MockTransport(_handler(fail=True))), KEY, DailyQuota(tmp_path / "q2", 10))
    with pytest.raises(NmcError) as exc:
        client.fetch("pharmacy", "서울특별시", "종로구")
    assert KEY not in str(exc.value) and "ConnectError" in str(exc.value)
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info('HTTP Request: GET https://x/y?serviceKey=%s&Q0=a "HTTP/1.1 200 OK"', KEY)
    assert KEY not in caplog.text and "serviceKey=<KEY>" in caplog.text


def test_client_pagination_bounded(tmp_path: Path) -> None:
    pages: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        pages.append(int(req.url.params["pageNo"]))
        return httpx.Response(200, content=_xml(PHARM, total=5000))  # claims more than we will ever page

    client = NmcClient(httpx.Client(transport=httpx.MockTransport(handler)), KEY, DailyQuota(tmp_path / "q", 100))
    items = client.fetch("pharmacy", "서울특별시", "종로구")
    assert pages == [1, 2, 3, 4, 5] and len(items) == 15


# ---------------------------------------------------------------- endpoints

def test_er_and_open_now_endpoints(tmp_path: Path) -> None:
    calls: list[str] = []
    for c in _client(_settings(tmp_path), tmp_path, _handler(calls)):
        er = c.get(f"/medical/er?sgg_code={CODE}").json()
        assert er["count"] == 2 and er["reporting_count"] == 1 and er["er_beds_available_total"] == 5
        assert er["items"][0]["name"] == "종로응급병원" and er["items"][0]["er_beds_available"] == 5
        assert er["items"][0]["beds_updated_at"] == "2024-07-02T21:30:00+09:00" and er["beds_updated_at"] == er["items"][0]["beds_updated_at"]
        assert er["items"][0]["distance_km"] < er["items"][1]["distance_km"] and er["items"][1]["beds_reported"] is False
        assert er["fetched"]["er_beds"]["source"] == "live" and er["fetched"]["er_beds"]["stale"] is False
        night = c.get(f"/medical/open-now?sgg_code={CODE}&at={DATE}T23:30").json()
        assert night["is_night"] and not night["is_holiday"] and night["day_index"] == 2
        assert night["pharmacies"] == {**night["pharmacies"], "open": 1, "closed": 1, "unknown_hours": 1, "total": 3}
        assert night["pharmacies"]["items"][0]["name"] == "밤약국" and night["pharmacies"]["items"][0]["hours_today"] == "0900-0200"
        assert night["hospitals"]["open"] == 2 and night["hospitals"]["er_hospitals"] == 1
        assert "외래" in night["hospitals"]["hours_note"] and night["hospitals"]["items"][1]["has_er"] is True
        day = c.get(f"/medical/open-now?sgg_code={CODE}&at={DATE}T10:00&kind=pharmacy").json()
        assert day["pharmacies"]["open"] == 2 and "hospitals" not in day
        assert c.get(f"/medical/open-now?sgg_code={CODE}&at=2024-07-02").status_code == 422
        assert c.get("/medical/er?sgg_code=99999").status_code == 404
        assert c.get("/medical/status").json()["live_possible"] is True
        # the directories were fetched once each; a second call is served from cache
        assert calls.count("getParmacyListInfoInqire") == 1 and calls.count("getEgytListInfoInqire") == 1


def test_api_down_serves_snapshot_with_age_or_503(tmp_path: Path) -> None:
    seed = tmp_path / "cache" / "extra" / "nmc_snapshots"
    seed.mkdir(parents=True)
    settings = _settings(tmp_path)
    old = datetime.now(timezone.utc) - timedelta(days=3)
    MedicalCache(tmp_path / "x", None, clock=lambda: old).write_snapshot("pharmacy", "서울특별시", "종로구", PHARM, directory=seed)
    for c in _client(settings, tmp_path, _handler(fail=True)):
        r = c.get(f"/medical/open-now?sgg_code={CODE}&at={DATE}T23:30&kind=pharmacy")
        assert r.status_code == 200
        f = r.json()["fetched"]["pharmacy"]
        assert f["source"] == "seed" and f["stale"] and f["age_s"] >= 3 * 86400 - 5 and "ConnectError" in f["error"]
        assert r.json()["pharmacies"]["open"] == 1
        assert c.get(f"/medical/er?sgg_code={CODE}").status_code == 503  # no snapshot for ER at all
        empty = c.get("/medical/open-now?sgg_code=26110&at=2024-07-02T23:30&kind=pharmacy")
        assert empty.status_code == 503  # another 시군구 without any snapshot


def test_zero_results_is_자료없음_not_an_error(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_xml([]))

    for c in _client(_settings(tmp_path), tmp_path, handler):
        er = c.get(f"/medical/er?sgg_code={CODE}").json()
        assert er["count"] == 0 and er["note"] == "자료 없음" and er["er_beds_available_total"] is None
        now = c.get(f"/medical/open-now?sgg_code={CODE}&at={DATE}T23:30").json()
        assert now["pharmacies"]["note"] == "자료 없음" and now["pharmacies"]["open"] == 0


# ---------------------------------------------------------------- briefing / actions / agent

def test_briefing_and_actions_carry_night_gap_evidence(tmp_path: Path) -> None:
    for c in _client(_settings(tmp_path), tmp_path, _handler()):
        b = c.get(f"/briefing/{CODE}?date={DATE}")
        assert b.status_code in (200, 409)  # 409 when the module tables are not loaded in this fixture
        if b.status_code == 200:
            med = b.json()["medical"]
            assert med["pharmacies_open_night"] == 1 and med["pharmacies_total"] == 3 and med["er_beds_available_total"] == 5
            assert med["pharmacies_open_per_10k_pop65"] == 40.0  # 1 open / 250 pop65 * 10,000
            assert b.json()["heat"]["evidence"]["medical_night"] == med
            gap = next(a for a in b.json()["actions"] if a["action_id"] == "medical.gap")
            assert "열린 약국 1곳" in gap["text"] and "실시간 자료" in gap["reason"] and gap["evidence"] == med
        a = c.get(f"/actions/{CODE}?date={DATE}&teams=1")
        if a.status_code == 200:
            assert a.json()["evidence"]["medical_night"]["pharmacies_open_night"] == 1


def test_agent_get_medical_on_fall_run(tmp_path: Path) -> None:
    for c in _client(_settings(tmp_path), tmp_path, _handler()):
        rec = c.post("/agent/run", json=FALL).json()
        step = next(s for s in rec["steps"] if s["tool"] == "get_medical")
        assert step["ok"] and step["source"] == "deterministic"
        v = step["result"]
        assert v["nearest_er"]["name"] == "종로응급병원" and v["nearest_er"]["er_beds_available"] == 5
        assert v["er_count"] == 2 and v["data_stale"] is False and "가까운 응급실 종로응급병원 가용 병상 5" in step["summary"]
        assert "가까운 응급실 종로응급병원 가용 병상 5" in rec["briefing"]
        assert rec["grounding"]["template_check"] == []  # phone numbers / timestamps in the view are not data claims
        assert [s["tool"] for s in rec["steps"]].index("get_medical") < [s["tool"] for s in rec["steps"]].index("make_cap_alert")
        stored = json.loads(json.dumps(c.get(f"/agent/runs/{rec['run_id']}").json()))
        assert stored["steps"] == rec["steps"]
