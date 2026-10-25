"""Hour-aware action panel for the twin: daytime heat actions (the four /actions channels), night-time AED 24h
conversion recommendations for sites closed at that hour, shelter/crosswalk notes; camera placement candidates.

Every action carries evidence rows, a stable `action_id` for POST /feedback (채택 -> audit record) and a CAP
generation endpoint (POST /twin/<sgg>/actions/<action_id>/cap -> XSD-validated, stored, generation_ms). Nothing new
is modelled: AED candidates come from the aed_candidate table (/aed/candidates), heat actions from app.actions.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path
from datetime import date as date_type
from datetime import datetime, timezone
from typing import Any

from ..actions import cbs, service
from ..actions import constants as AC
from ..actions.geometry import haversine_m
from ..agent import cap_audio, standards
from ..config import Settings
from ..heat import common as heat_common
from ..modules import repo as modules_repo
from . import gov
from . import state as twin_state

logger = logging.getLogger("twin.actions")
NIGHT_START_H, NIGHT_END_H = 22, 6
DAY_ACTIONS: tuple[str, ...] = ("cbs", "bit", "signals", "route")
ACTION_AED_CONVERT = "aed.convert"
ACTION_SHELTER_NIGHT = "shelter.night"
ACTION_SIGNALS = "signals"
CAMERA_RADIUS_M = 300.0
CAMERA_LIMIT = 8
CAP_ID_PREFIX = "zp-twin"
CAP_EVENT = {"cbs": "폭염 주의 (훈련)", ACTION_AED_CONVERT: "AED 야간 공백 안내 (훈련)", ACTION_SHELTER_NIGHT: "야간 무더위쉼터 안내 (훈련)",
             ACTION_SIGNALS: "보행신호 연장 안내 (훈련)", "bit": "정류장 안내 (훈련)", "route": "방문 점검 안내 (훈련)"}


def is_night(hour: int) -> bool:
    return hour >= NIGHT_START_H or hour < NIGHT_END_H


def _ev(label: str, value: Any, source: str, display: str | None = None) -> dict[str, Any]:
    """Evidence row: `source` is the technical provenance (audit page), `display_source` the judge-facing Korean label."""
    return {"label": label, "value": value, "source": source, "display_source": display or source}


DS_ASOS = "기상청 ASOS 관측 (서울 지점)"
DS_HEAT_MODEL = "폭염 위험 모델 (시군구별 예상 환자 수)"
DS_POP = "행정안전부 주민등록 인구 (행정동, 2026-09)"
DS_AED_HOURS = "국립중앙의료원 AED 운영시간표"
DS_AED_GAP = "국립중앙의료원 AED 운영시간 + 야간 공백 분석 (에이전트)"
DS_SHELTER = "지자체 무더위쉼터 공개자료"
DS_CROSSWALK = "전국 횡단보도 표준데이터"
DS_STOPS = "전국 버스정류장 위치정보 + 추천 정류장 분석 (에이전트)"
DS_TAAS = "TAAS 2024 노인 보행사고 다발지"
DS_SILVER = "TAAS 노인 보행사고 다발지 · 2023년까지 자료로 선정, 2024년으로 평가"


def _heat_actions(conn: sqlite3.Connection, settings: Settings, region: dict[str, Any], d: date_type) -> tuple[list[dict[str, Any]], str | None]:
    """The four /actions channels as panel actions (empty with a note when the day has no stored run / is off-season)."""
    if not heat_common.SEASON_START <= (d.month, d.day) <= heat_common.SEASON_END:
        return [], "폭염 조치는 폭염 평가 기간(%02d-%02d ~ %02d-%02d) 날짜에만 생성한다" % (*heat_common.SEASON_START, *heat_common.SEASON_END)
    try:
        resp = service.build_actions(conn, settings.data_dir, region["code"], d, cbs_limit=gov.CBS_PILOT_LIMIT)
        try:
            cbs90 = service.build_actions(conn, settings.data_dir, region["code"], d, cbs_limit=gov.CBS_STANDARD_LIMIT).cbs
        except cbs.MessageTooLongError as exc:
            cbs90 = None
            logger.info("90자 draft not possible for %s %s: %s", region["code"], d, exc)
    except service.NoRunError:
        return [], twin_state.HEAT_NOTE_NO_RUN
    except (service.DataUnavailableError, cbs.MessageTooLongError) as exc:
        return [], f"폭염 조치 생성 불가: {exc}"
    ctx = resp.context
    heat = twin_state.heat_block(conn, region, d.isoformat())
    feel = f"체감 최고 {ctx.apparent_max_c}℃" if ctx.apparent_max_c is not None else (f"최고기온 {ctx.tmax_c}℃" if ctx.tmax_c is not None else "기온 자료 없음")
    decision = (heat.get("decision_text") or {}).get("ours", gov.OURS_NO_ALERT_TEXT)
    expected = heat.get("expected_cases_text") or "예상 환자 자료 없음"
    heat_line = f"{feel} · {decision} · {expected}"
    shelter_ev = (_ev("쉼터 후보", len(resp.cbs.shelters), AC.SOURCES["cbs"], DS_SHELTER) if resp.cbs.shelters
                  else _ev("무더위쉼터", "구청 보유 목록 연계 필요 (공개자료 없음 → 문안에서 쉼터 문장 생략)", "shelter 테이블", DS_SHELTER))
    base_ev = [
        _ev("체감 최고 (℃)", ctx.apparent_max_c, "기상청 ASOS 시간자료 → 일 최고 체감", DS_ASOS),
        _ev("최고기온 (℃)", ctx.tmax_c, "기상청 ASOS", DS_ASOS),
        _ev("에이전트 경보", ctx.alerted, "저장된 OURS 실행 (variant F)", DS_HEAT_MODEL),
        _ev("예상 환자 λ", ctx.score, "Poisson GBM", DS_HEAT_MODEL),
        _ev("위험 순위", f"{ctx.rank}/{ctx.n_cells}" if ctx.rank is not None else None, "OURS 점수 순위", DS_HEAT_MODEL),
        _ev("65세 이상 인구", ctx.pop65, "행정동 인구 15097972", DS_POP),
    ]
    out: list[dict[str, Any]] = [
        {"id": "cbs", "kind": "heat", "title": "재난문자 초안", "body": resp.cbs.text, "easy_body": resp.cbs.easy_text,
         "chars": f"{resp.cbs.length}/{resp.cbs.limit}",
         "drafts": [{"limit": gov.CBS_STANDARD_LIMIT, "label": gov.CBS_STANDARD_LABEL, "text": cbs90.text if cbs90 else None,
                     "easy_text": cbs90.easy_text if cbs90 else None, "length": cbs90.length if cbs90 else None,
                     "note": None if cbs90 else "필수 문구가 90자를 넘어 90자 초안을 만들 수 없다"},
                    {"limit": gov.CBS_PILOT_LIMIT, "label": gov.CBS_PILOT_LABEL, "text": resp.cbs.text, "easy_text": resp.cbs.easy_text,
                     "length": resp.cbs.length, "note": None}],
         "limit_note": gov.CBS_LIMIT_NOTE, "flow_note": gov.CBS_FLOW_NOTE,
         "skip_regions_note": "에이전트 역할 = 우선순위 + 초안 + 보내지 않아도 되는 지역 표시 (피로도·중복 방지); 대상 아님 지역은 /heat/alerts 참조",
         "evidence": base_ev + [shelter_ev], "evidence_text": heat_line, "status": "draft"},
        {"id": "voice", "kind": "heat", "title": gov.VOICE_TITLE, "body": resp.cbs.easy_text,
         "audio_url": (f"/twin/{region['code']}/actions/voice/audio.wav?date={d.isoformat()}" if cap_audio.available() else None),
         "audio_note": "마을방송 WAV (macOS say · 16 kHz mono) · 단말/스피커 전달 대기" if cap_audio.available() else "이 호스트에 음성 합성기(say/ffmpeg)가 없다",
         "evidence": base_ev + [shelter_ev], "evidence_text": heat_line, "status": "draft"},
        {"id": "bit", "kind": "heat", "title": gov.BIT_TITLE, "body": " / ".join(resp.bit[0].message.lines) if resp.bit else "(후보 정류장 없음)",
         "stops": [{"stop_id": b.stop_id, "name": b.name, "lat": b.lat, "lon": b.lon, "lines": b.message.lines} for b in resp.bit],
         "evidence": base_ev + [_ev("반경 (m)", resp.bit_info.radius_m, AC.SOURCES["bit"], DS_STOPS), _ev("후보 정류장", resp.bit_info.n_candidate_stops, resp.bit_info.method, DS_STOPS)],
         "evidence_text": f"{heat_line} · 후보 정류장 {len(resp.bit)}곳", "status": "draft"},
        {"id": "route", "kind": "heat", "title": "방문팀 동선", "body": resp.route.note + f" 경로 {resp.route.km} km ({resp.route.distance_basis})",
         "stops": [{"order": s.order, "dong": s.dong, "lat": s.lat, "lon": s.lon, "pop65": s.pop65} for s in resp.route.stops],
         "evidence": base_ev + [_ev("배정 팀 수", resp.route.teams_in_region, resp.route.method, DS_HEAT_MODEL + " · 방문팀 배정")],
         "evidence_text": f"{heat_line} · 방문 지점 {len(resp.route.stops)}곳 {resp.route.km} km", "status": "draft"},
    ]
    if isinstance(resp.signals, list) and resp.signals:
        out.append({"id": ACTION_SIGNALS, "kind": "heat", "title": gov.SIGNALS_TITLE,
                    "body": f"녹색시간 부족 횡단보도 {len(resp.signals)}곳 (0.7 m/s 기준)",
                    "items": [s.model_dump() for s in resp.signals],
                    "speed_note": gov.SIGNALS_SPEED_NOTE,
                    "evidence": [_ev("판정 대상", resp.signals_coverage.n_evaluable, resp.signals_coverage.note, DS_CROSSWALK),
                                 _ev("보행속도 기준", "0.7 m/s", gov.SIGNALS_SPEED_NOTE, "서울시 보호구역 보행속도 기준 (2024-10)")],
                    "evidence_text": f"{heat_line} · 녹색시간 부족 횡단보도 {len(resp.signals)}곳 (0.7 m/s)", "status": "draft"})
    return out, None


WALK4_PICKS = Path(__file__).resolve().parents[2] / "experiments" / "walk4" / "out" / "convert_picks.csv"
UNKNOWN_HOURS_RULE = "운영시간이 등록되지 않은 AED는 닫힘으로 간주한다 (개방 여부 확인 뒤 전환 권고)"
GAIN_SCORE_LABEL = "닫힌 시간대의 구 전체 예상 공공장소 심정지 (월, 모형 추정)"
_PICKS: dict[str, dict[tuple[str, str], float]] = {}


def site_name(place: str | None, address: str | None, org: str) -> str:
    """Installation place for the card title: '<road>, <site> (<동>)' -> <site>; else the place text; else the managing org."""
    if address and "," in address:
        tail = address.split(",", 1)[1]
        name = tail.split("(")[0].strip()
        if name:
            return name
    if place:
        return place.strip()
    return org


def walk4_gains(path: Path = WALK4_PICKS) -> dict[tuple[str, str], float]:
    """(org, address) -> 65+ residents who gain a night-open AED within a 2-minute walk (experiments/walk4 convert picks)."""
    key = str(path)
    if key in _PICKS:
        return _PICKS[key]
    out: dict[tuple[str, str], float] = {}
    if path.is_file():
        import csv

        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    out[(row["org"], row["addr"])] = float(row["gain_p65"])
                except (KeyError, ValueError):
                    continue
    _PICKS[key] = out
    return out


def _aed_convert_actions(conn: sqlite3.Connection, settings: Settings, region: dict[str, Any], daytype: str, hour: int) -> list[dict[str, Any]]:
    """One action per top-5 conversion candidate of the 구 that is closed (or has no hours) at this clock reading.

    Known-closing sites rank above unknown-hours sites; the gain is shown as 65+ residents who gain a night-open AED within
    a 2-minute walk when the site is in the walk4 picks, else as the candidate score with its unit spelled out.
    """
    items, _ = twin_state.aed_items(conn, settings, region, daytype, hour)
    by_serial = {i["id"]: i for i in items}
    gains = walk4_gains()
    rows = conn.execute(
        "SELECT serial_seq, rank_in_gu AS rank, org, address, place, weekday_hours, night_open_weekday_share, gain_score, lat, lon "
        "FROM aed_candidate WHERE list_kind = ? AND gu = ? ORDER BY rank_in_gu", (modules_repo.LIST_TOP5, region["name"])).fetchall()
    cards: list[tuple[int, float, dict[str, Any]]] = []
    for c in rows:
        serial = str(c["serial_seq"])
        live = by_serial.get(serial)
        if live is not None and live["open"] is True:
            continue
        known = bool(c["weekday_hours"])
        site = site_name(c["place"], c["address"], c["org"])
        gain = gains.get((c["org"], c["address"]))
        if known:
            status = f"운영시간 {c['weekday_hours']} → {hour:02d}시 닫힘"
        else:
            status = "운영시간 미등록 (닫힘으로 간주) → 야간 개방 확인 뒤 전환 권고"
        benefit = (f" 전환 시 밤에 걸어서 2분 안에 열린 AED가 생기는 65세 이상 +{gain:,.0f}명." if gain is not None else "")
        evidence = [
            _ev("설치 장소", c["place"], "AED 공공 API", DS_AED_HOURS), _ev("관리 기관", c["org"], "AED 공공 API", DS_AED_HOURS),
            _ev("주소", c["address"], "AED 공공 API", DS_AED_HOURS),
            _ev("평일 운영시간", c["weekday_hours"] or "미등록", "AED 개방시간표", DS_AED_HOURS),
            _ev("평일 야간 개방 비율", c["night_open_weekday_share"], "AED 개방시간표", DS_AED_HOURS),
        ]
        if gain is not None:
            evidence.append(_ev("밤 2분 보행권 안에 열린 AED가 생기는 65세 이상 (명)", round(gain), "experiments/walk4 convert_picks gain_p65",
                                "보행망 분석 (에이전트) (OSM 보행망 · 0.8 m/s 2분 · 행정동 65+ 인구)"))
        evidence.append(_ev(GAIN_SCORE_LABEL, c["gain_score"], "experiments/aed 후보 산출 (sc = Σ λ(구,시) × 닫힘 비율)", DS_AED_GAP))
        if not known:
            evidence.append(_ev("판정 규칙", UNKNOWN_HOURS_RULE, "개방시간표 판정 규칙", DS_AED_HOURS))
        card = {
            "id": f"{ACTION_AED_CONVERT}:{c['rank']}", "kind": "aed", "title": f"{site} AED 24시간 개방 요청(초안)",
            "body": f"{status}.{benefit}", "site": site, "org": c["org"], "hours_known": known,
            "lat": c["lat"], "lon": c["lon"], "serial": serial, "rank_in_gu": c["rank"],
            "gain_p65_2min": None if gain is None else round(gain), "evidence": evidence, "status": "draft",
            "evidence_text": (f"평일 운영시간 {c['weekday_hours'] or '미등록'} · 평일 야간 개방 {round(100 * float(c['night_open_weekday_share']))}%"
                              + (f" · 밤 2분 보행권 65+ +{gain:,.0f}명" if gain is not None else "")),
        }
        cards.append((0 if known else 1, -(gain if gain is not None else float(c["gain_score"])), card))
    cards.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in cards]


def build_panel(conn: sqlite3.Connection, settings: Settings, region: dict[str, Any], date_: str, hour: int, daytype: twin_state.DayType
                ) -> dict[str, Any]:
    """Actions for the clock reading: heat channels by day, AED conversions + shelter notes by night (both when useful)."""
    d = date_type.fromisoformat(date_)
    night = is_night(hour)
    actions: list[dict[str, Any]] = []
    notes: list[str] = []
    heat_actions, heat_note = _heat_actions(conn, settings, region, d)
    cbs_night = hour >= gov.CBS_NIGHT_START_H or hour < gov.CBS_NIGHT_END_H
    for a in heat_actions:
        if a["id"] == "cbs" and cbs_night:  # 운영규정 별표 1: 폭염 재난문자는 야간(21~06시)에 보내지 않는다
            a["night_excluded"] = True
            a["deferred_to"] = gov.CBS_NIGHT_EXCLUDED_TEXT
            a["body"] = f"{gov.CBS_NIGHT_EXCLUDED_TEXT}. 초안은 보관: {a['body']}"
            a["evidence"] = [*a["evidence"], _ev("야간 발송 기준", gov.CBS_NIGHT_EXCLUDED_TEXT, "재난문자방송 기준 및 운영규정 별표 1", "재난문자방송 기준 및 운영규정 [1차]")]
        else:
            a["night_excluded"] = False
    if not night:
        actions.extend(heat_actions)
        if heat_note:
            notes.append(heat_note)
    elif cbs_night and any(a["id"] == "cbs" for a in heat_actions):
        notes.append(f"재난문자: {gov.CBS_NIGHT_EXCLUDED_TEXT}")
    aed_actions = _aed_convert_actions(conn, settings, region, daytype, hour)
    if night or not heat_actions:
        actions.extend(aed_actions)
    shelters = twin_state.shelter_items(conn, region["code"], daytype, hour)
    if shelters and not any(s["open"] is True for s in shelters):
        actions.append({"id": ACTION_SHELTER_NIGHT, "kind": "shelter", "title": "야간 개방 쉼터 없음",
                        "body": f"{hour:02d}시 기준 개방 중인 무더위쉼터가 없다 (등록 {len(shelters)}곳). 야간 개방 지정이 필요하다.",
                        "evidence": [_ev("등록 쉼터", len(shelters), "무더위쉼터 공개자료", DS_SHELTER), _ev("night_open=1", sum(1 for s in shelters if s["night_open"]), "공개자료 플래그", DS_SHELTER)],
                        "evidence_text": f"등록 쉼터 {len(shelters)}곳 · {hour:02d}시 개방 0곳", "status": "draft"})
    elif not shelters:
        notes.append(gov.SHELTER_ABSENT_TEXT)
    for a in actions:
        kind = a["id"].split(":", 1)[0]
        a["feedback_ref"] = {"sgg_code": region["code"], "date": date_, "action_id": kind}
        a["cap_url"] = f"/twin/{region['code']}/actions/{a['id']}/cap?date={date_}&hour={hour}"
        a["document_url"] = f"/twin/{region['code']}/actions/{a['id']}/document?date={date_}&hour={hour}"
        a["destination"] = gov.destination(kind)
        a["status"] = gov.STATUS_DRAFT  # the router overlays the live approval status (panels are cached)
        a["status_label"] = gov.STATUS_LABEL[gov.STATUS_DRAFT]
    return {"sgg_code": region["code"], "region": f"{region['sido']} {region['name']}", "date": date_, "hour": hour, "daytype": daytype,
            "night": night, "actions": actions, "notes": notes, "disclaimer": AC.DISCLAIMER, "never_sent": gov.NEVER_SENT,
            "statuses": gov.STATUS_LABEL,
            "feedback": "POST /feedback {sgg_code, date, action_id, decision: 채택} → audit {id, hash}",
            "sources": twin_state.sources(conn), "fetched_at": twin_state.fetched_at(conn)}


def voice_text(conn: sqlite3.Connection, settings: Settings, region: dict[str, Any], d: date_type) -> str | None:
    """The 쉬운 말 재난문자 of the day (what the voice card reads); None when no heat actions exist."""
    acts, _ = _heat_actions(conn, settings, region, d)
    voice = next((a for a in acts if a["id"] == "voice"), None)
    return None if voice is None else str(voice["body"])


def find_action(panel: dict[str, Any], action_id: str) -> dict[str, Any] | None:
    return next((a for a in panel["actions"] if a["id"] == action_id), None)


def cap_for_action(region: dict[str, Any], action: dict[str, Any], date_: str, hour: int) -> dict[str, Any]:
    """CAP 1.2 JSON (Exercise, Restricted) for one panel action, ready for routers.cap.publish_alert."""
    kind = action["id"].split(":", 1)[0]
    event = CAP_EVENT.get(kind, "생활안전 안내 (훈련)")
    body = str(action.get("easy_body") or action["body"])
    return standards.build_cap(
        identifier=f"{CAP_ID_PREFIX}-{region['code']}-{date_}-{hour:02d}-{kind.replace('.', '_')}-{uuid.uuid4().hex[:8]}",
        sent=datetime.now(timezone.utc), event=event, headline=f"{region['name']} {action['title']}"[:80],
        description=f"{date_} {hour:02d}시 기준. " + body, instruction="훈련 메시지입니다. 실제 발송되지 않았습니다.",
        area_desc=f"{region['sido']} {region['name']}", lat=float(action.get("lat") or region["lat"]), lon=float(action.get("lon") or region["lon"]),
    )


CAMERA_LABEL_PREFIX = "시연 카메라"
CAMERA_HONESTY = "시연 카메라: 실제 CCTV 위치 공공데이터 미확보 → 횡단보도 표준데이터의 소재지 주소를 쓴다 (실제 CCTV 관리번호가 아니다)"
CAMERA_SNAP_CROSSWALK_M = 150.0
CAMERA_SNAP_ANY_M = 300.0
PLACE_TYPE = {"crosswalk": "횡단보도", "hotspot": "사고다발지", "stop": "정류장"}


def camera_candidates(conn: sqlite3.Connection, settings: Settings, region: dict[str, Any]) -> dict[str, Any]:
    """Where a fall camera would go: crosswalks inside silver zones, 2024 hotspots, top recommended stops.

    Each candidate carries a control-room style `camera` block {id_label, address, road, place_type, lat, lon, source}. Without a
    public CCTV location dataset the id is "시연 카메라 #n" and the address is the crosswalk standard-data 소재지 (never a fake CCTV id).
    """
    silver = twin_state.silver_block(conn, region["code"])
    zones = silver["current"] + silver["ours"]
    out: list[dict[str, Any]] = []
    for cw in twin_state.crosswalk_items(settings, region):
        near = min((haversine_m(cw["lat"], cw["lon"], z["lat"], z["lon"]) for z in zones), default=None)
        if near is not None and near <= CAMERA_RADIUS_M:
            out.append({"id": f"crosswalk:{cw['id']}", "name": f"횡단보도 {cw['id']}", "lat": cw["lat"], "lon": cw["lon"], "kind": "crosswalk",
                        "why": f"실버존 {near:.0f} m 안의 횡단보도" + (" · 녹색시간 부족" if cw["short"] else ""), "source": "횡단보도 표준데이터 × 실버존",
                        "display_source": DS_CROSSWALK + " · 실버존 안", "address": cw["address"], "road": cw["road"], "address_kind": cw["address_kind"],
                        "crosswalk_id": cw["id"]})
    for h in silver["hotspots"]:
        out.append({"id": f"hotspot:{h['lat']:.5f},{h['lon']:.5f}", "name": "2024 노인 보행사고 다발지", "lat": h["lat"], "lon": h["lon"], "kind": "hotspot",
                    "why": f"사상자 {h['casualties']:.0f}명 (2024)", "source": "TAAS 15029185", "display_source": DS_TAAS, "address": None, "road": None})
    for s in twin_state.stop_items(conn, region["code"])[:CAMERA_LIMIT]:
        out.append({"id": f"stop:{s['stop_id']}", "name": s["name"], "lat": s["lat"], "lon": s["lon"], "kind": "stop",
                    "why": f"추천 정류장 {s['greedy_rank']}위 (도보권 65+ {s['p65_within_walk'] or 0:.0f}명)", "source": "mobility 추천 정류장",
                    "display_source": DS_STOPS, "address": None, "road": None})
    out = out[:CAMERA_LIMIT * 2]
    for n, c in enumerate(out, start=1):
        c["camera"] = camera_block(region, c, n)
    return {"sgg_code": region["code"], "region": f"{region['sido']} {region['name']}", "candidates": out, "radius_m": CAMERA_RADIUS_M,
            "camera_note": CAMERA_HONESTY, "sources": twin_state.sources(conn), "fetched_at": twin_state.fetched_at(conn)}


def camera_block(region: dict[str, Any], cand: dict[str, Any], n: int) -> dict[str, Any]:
    """Control-room camera fields for a candidate: 시연 카메라 #n at the candidate's standard-data address."""
    addr = cand.get("address") or cand.get("name") or ""
    return {"id_label": f"{CAMERA_LABEL_PREFIX} #{n}", "id": cand["id"], "address": addr, "road": cand.get("road"),
            "place_type": PLACE_TYPE.get(str(cand.get("kind")), "지점"), "lat": cand["lat"], "lon": cand["lon"],
            "source": cand["source"], "display_source": cand["display_source"], "real_cctv": False, "label": "시연 영상 · 횡단보도 표준데이터 위치",
            "note": CAMERA_HONESTY}


def camera_for_point(conn: sqlite3.Connection, settings: Settings, region: dict[str, Any], lat: float, lon: float) -> dict[str, Any] | None:
    """The demo camera that 'saw' a point: nearest crosswalk candidate within 150 m, else any candidate within 300 m, else None."""
    cands = camera_candidates(conn, settings, region)["candidates"]
    best: tuple[float, dict[str, Any]] | None = None
    for c in cands:
        d = haversine_m(lat, lon, c["lat"], c["lon"])
        limit = CAMERA_SNAP_CROSSWALK_M if c["kind"] == "crosswalk" else CAMERA_SNAP_ANY_M
        if d <= limit and (best is None or (c["kind"] == "crosswalk") > (best[1]["kind"] == "crosswalk") or
                           ((c["kind"] == "crosswalk") == (best[1]["kind"] == "crosswalk") and d < best[0])):
            best = (d, c)
    if best is None:
        return None
    return {**best[1]["camera"], "distance_m": round(best[0], 1)}


def fall_where(region: dict[str, Any], camera: dict[str, Any] | None) -> str:
    """'서울특별시 관악구 <도로명주소> (횡단보도) 시연 카메라 #2' (the address is not prefixed twice when it already names the 구)."""
    prefix = f"{region['sido']} {region['name']}"
    if camera is None:
        return f"{prefix} (카메라 후보 없음 · 좌표 기준)"
    addr = str(camera["address"])
    full = addr if addr.startswith(region["sido"]) or addr.startswith(region["name"]) else f"{prefix} {addr}"
    return f"{full} ({camera['place_type']}) {camera['id_label']}"


def fall_alert_text(region: dict[str, Any], camera: dict[str, Any] | None, hhmm: str) -> str:
    """'<where>에서 낙상 의심 감지 · 15:00 · 관제요원 확인 대기'."""
    return f"{fall_where(region, camera)}에서 낙상 의심 감지 · {hhmm} · 관제요원 확인 대기"


__all__ = ["ACTION_AED_CONVERT", "ACTION_SHELTER_NIGHT", "build_panel", "camera_candidates", "cap_for_action", "find_action", "is_night",
           "camera_block", "camera_for_point", "fall_alert_text", "fall_where", "site_name", "voice_text", "walk4_gains"]
