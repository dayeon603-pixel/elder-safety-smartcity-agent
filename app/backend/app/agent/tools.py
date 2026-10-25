"""Tool registry: typed JSON-schema tools whose executors return real data (our HTTP endpoints,
the local simulators, the local VLM). Nothing here fabricates a value."""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from ..actions import bit
from . import constants as C
from . import standards
from .guard import JsonSchema
from .ollama import OllamaClient, OllamaUnavailable

logger = logging.getLogger(__name__)

CODE_PATTERN = r"[0-9]{5}"
DATE_PATTERN = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")
_DATA_URL_PREFIX = re.compile(r"^data:image/[a-z]+;base64,", re.IGNORECASE)
_B64_WS = re.compile(r"\s+")
_VLM_STRIP = re.compile(r"https?://\S+|[0-9]+(?:\.[0-9]+)?")
IMAGE_MAGIC: tuple[bytes, ...] = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
SELFCHECK_LABEL = "필수 필드 자체 점검 통과"
XSD_LABEL = "OASIS CAP 1.2 XSD 검증 통과"

VLM_SYSTEM_PROMPT = (
    "당신은 현장 사진을 객관적으로 묘사하는 도구입니다. 한국어로 최대 2문장만 쓰십시오. "
    "보이는 자세와 주변 환경만 묘사하십시오. 사람의 신원, 이름, 나이, 성별 추정, 건강 상태 진단, "
    "원인 추정은 쓰지 마십시오. 보이지 않는 것은 쓰지 마십시오."
)
VLM_USER_PROMPT = "이 사진의 자세와 주변 환경을 2문장 이내로 묘사하십시오."


class ToolError(RuntimeError):
    """A tool could not complete; the message is shown in the step log."""


@dataclass(slots=True)
class ToolContext:
    """Everything a tool may touch. `ctx` is the trusted trigger context (image excluded)."""

    http: httpx.Client
    ollama: OllamaClient | None
    ctx: dict[str, Any]
    image_b64: str | None
    run_id: str
    now: Callable[[], datetime]
    uuid_fn: Callable[[], str]
    vlm_model: str | None = None
    vlm_digest: str | None = None
    demo_token: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    http_log: list[dict[str, Any]] = field(default_factory=list)
    image_sha256: str | None = None
    evidence_fn: Callable[[], list[float]] = field(default=lambda: [])
    lock: threading.Lock = field(default_factory=threading.Lock)  # guards `state` (tools run in parallel)
    tls: threading.local = field(default_factory=threading.local)  # per-thread HTTP log of the running tool

    def call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        """One real HTTP round trip to ourselves; timed and logged. Transport errors -> ToolError.

        The client is bound to a loopback base URL by the router; the demo token (if any) is
        forwarded so the simulators accept our own calls.
        """
        headers = dict(kw.pop("headers", {}) or {})
        if self.demo_token:
            headers[C.DEMO_TOKEN_HEADER] = self.demo_token
        t0 = time.perf_counter()
        try:
            resp = self.http.request(method, path, timeout=C.SELF_HTTP_TIMEOUT_S, headers=headers, **kw)
        except httpx.HTTPError as exc:
            self._log(method, path, None, t0)
            raise ToolError(f"{method} {path} 연결 실패 ({type(exc).__name__})") from exc
        self._log(method, path, resp.status_code, t0)
        return resp

    def _log(self, method: str, path: str, status: int | None, t0: float) -> None:
        entry = {"method": method, "path": path, "status": status,
                 "ms": round((time.perf_counter() - t0) * 1000, 2)}
        log: list[dict[str, Any]] = getattr(self.tls, "log", None) or []
        log.append(entry)
        self.tls.log = log
        self.http_log.append(entry)


@dataclass(slots=True)
class ToolResult:
    """`view` is what the model sees; `evidence` is what briefing numbers may be grounded in."""

    view: dict[str, Any]
    summary: str
    evidence: Any = None
    skipped: bool = False  # the tool decided (by rule) that there was nothing to do; not a failure

    def __post_init__(self) -> None:
        if self.evidence is None:
            self.evidence = self.view


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: JsonSchema
    run: Callable[[ToolContext, dict[str, Any]], ToolResult]
    prerequisites: tuple[str, ...] = ()  # must have SUCCEEDED (artifacts the call consumes)
    after: tuple[str, ...] = ()  # must have been ATTEMPTED (data it uses if available)

    def spec(self) -> dict[str, Any]:
        """Ollama / OpenAI-style function spec."""
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": dict(self.parameters)}}


EVENT_SCHEMA: JsonSchema = {
    "type": "object", "additionalProperties": False, "required": ["kind", "code", "date"],
    "properties": {
        "kind": {"type": "string", "enum": ["fall", "heat"]},
        "code": {"type": "string", "pattern": CODE_PATTERN},
        "date": {"type": "string", "pattern": DATE_PATTERN},
        "lat": {"type": "number", "minimum": -90, "maximum": 90},
        "lon": {"type": "number", "minimum": -180, "maximum": 180},
        "est_speed": {"type": "number", "minimum": 0, "maximum": 30},
        "torso_angle": {"type": "number", "minimum": 0, "maximum": 180},
    },
}
_CODE_DATE: JsonSchema = {
    "code": {"type": "string", "pattern": CODE_PATTERN, "description": "시군구 코드 5자리"},
    "date": {"type": "string", "pattern": DATE_PATTERN, "description": "YYYY-MM-DD"},
}


def _json(resp: httpx.Response, what: str) -> Any:
    try:
        return resp.json()
    except ValueError as exc:
        raise ToolError(f"{what}: 응답이 JSON이 아님") from exc


def _detail(resp: httpx.Response) -> str:
    """Only the error *code* of a structured detail (never a message that may echo server paths)."""
    try:
        d = resp.json().get("detail")
    except (ValueError, AttributeError):
        return ""
    if isinstance(d, dict) and isinstance(d.get("code"), str):
        return str(d["code"])
    return ""


def _heat_region(tc: ToolContext, a: dict[str, Any]) -> ToolResult:
    path = f"/heat/region/{a['code']}"
    resp = tc.call("GET", path, params={"date": a["date"]})
    if resp.status_code != 200:
        raise ToolError(f"GET {path} → {resp.status_code} {_detail(resp)}".strip())
    d = _json(resp, path)
    r, s, dr = d["region"], d["scores"], d["drivers"]
    view = {
        "name": r["name"], "sido": r["sido"], "lat": r["lat"], "lon": r["lon"], "pop65": r["pop65"],
        "tmax_c": dr["tmax"], "apparent_max_c": dr.get("apparent_max"), "old_share": dr["old_share"],
        "cases_today": d["cases_today"], "ours_score": s["ours"], "ours_rank": s["rank"],
        "shelter_coverage": d["shelters"]["coverage"],
    }
    with tc.lock:
        tc.state["region"] = view
    if view["apparent_max_c"] is not None:
        temp = f"체감 최고 {view['apparent_max_c']}℃"
    else:
        temp = "최고기온 자료 없음" if dr["tmax"] is None else f"최고기온 {dr['tmax']}℃"
    return ToolResult(view, f"GET {path} → 200 ({r['name']}, {temp})")


def _heat_plan(tc: ToolContext, a: dict[str, Any]) -> ToolResult:
    teams = int(a.get("teams", C.DEFAULT_TEAMS))
    resp = tc.call("GET", "/heat/plan", params={"date": a["date"], "teams": teams})
    if resp.status_code != 200:
        raise ToolError(f"GET /heat/plan → {resp.status_code} {_detail(resp)}".strip())
    d = _json(resp, "/heat/plan")
    mine = [x for x in d["allocations"] if x["code"] == a["code"]]
    view = {
        "date": d["date"], "teams": d["teams"], "region_in_plan": bool(mine),
        "region_teams": mine[0]["teams"] if mine else 0,
        "allocations_top": [
            {"name": x["name"], "teams": x["teams"], "expected_cases": x["expected_cases"],
             "reason": x["reason"]} for x in d["allocations"][: C.PLAN_TOP_N]],
        "ours_reached": d["compare"]["ours_reached"], "b1_reached": d["compare"]["b1_reached"],
        "shelter_extension_count": len(d["shelter_extensions"]),
    }
    with tc.lock:
        tc.state["plan"] = view
    return ToolResult(view, f"GET /heat/plan → 200 (팀 {d['teams']}개, 배정 {len(d['allocations'])}곳)")


def _actions(tc: ToolContext, a: dict[str, Any]) -> ToolResult:
    path = f"/actions/{a['code']}"
    try:
        resp = tc.call("GET", path, params={"date": a["date"]})
    except ToolError as exc:
        return ToolResult({"available": False, "reason": str(exc)}, f"{path} 사용 불가: {exc}")
    if resp.status_code != 200:
        view = {"available": False, "http_status": resp.status_code}
        return ToolResult(view, f"GET {path} → {resp.status_code} (사용 불가)")
    d = _json(resp, path)
    try:
        view = {
            "available": True, "alerted": d["context"]["alerted"],
            "team_allocated": d["context"]["team_allocated"], "cbs_text": d["cbs"]["text"],
            "cbs_length": d["cbs"]["length"], "bit_stop_count": len(d["bit"]),
            "signal_count": len(d["signals"]), "route_stop_count": len(d["route"]["stops"]),
            "route_km": d["route"]["km"], "disclaimer": d["disclaimer"],
        }
    except (KeyError, TypeError) as exc:
        raise ToolError(f"{path}: 예상 밖 응답 형태 ({type(exc).__name__})") from exc
    with tc.lock:
        tc.state["actions"] = view
    return ToolResult(view, f"GET {path} → 200 (CBS 초안 {d['cbs']['length']}자, 버스정류장 {len(d['bit'])}곳)")


def _geo(tc: ToolContext, event: dict[str, Any]) -> tuple[float, float]:
    if "lat" in event and "lon" in event:
        return float(event["lat"]), float(event["lon"])
    region = tc.state.get("region")
    if region:
        return float(region["lat"]), float(region["lon"])
    raise ToolError("좌표 없음: event.lat/lon 도, get_heat_region 결과도 없음")


def _place(tc: ToolContext, event: dict[str, Any]) -> str:
    region = tc.state.get("region")
    return f"{region['sido']} {region['name']}" if region else f"시군구 {event['code']}"


def _incident_id(tc: ToolContext, event: dict[str, Any]) -> str:
    return f"zp-{tc.run_id}-{event['code']}"


def heat_cap_condition(tc: ToolContext) -> str | None:
    """Reason a heat CAP is *not* warranted today (None = warranted).

    A heat CAP needs a real trigger: the region was alerted by our model (get_actions says so) or
    the observed Tmax reaches HEAT_CAP_THRESHOLD_C. Nothing is alerted on a mild day.
    """
    actions = tc.state.get("actions") or {}
    region = tc.state.get("region") or {}
    if actions.get("alerted") is True:
        return None
    apparent, tmax = region.get("apparent_max_c"), region.get("tmax_c")
    felt = apparent if isinstance(apparent, (int, float)) else tmax  # 폭염특보 criterion is on 체감온도
    if isinstance(felt, (int, float)) and felt >= C.HEAT_CAP_THRESHOLD_C:
        return None
    label = "체감 최고" if isinstance(apparent, (int, float)) else "최고기온"
    seen = "자료 없음" if felt is None else f"{felt}℃"
    return f"경보 대상 아님 ({label} {seen} < {C.HEAT_CAP_THRESHOLD_C}℃, 모델 경보 없음)"


def bit_lines_for(tc: ToolContext, kind: str) -> list[str]:
    """The 2-line 버스정류장 안내단말 text matching this alert (fixed templates; temperatures from get_heat_region)."""
    if kind == "fall":
        return [C.BIT_FALL_LINE1, C.BIT_FALL_LINE2]
    region = tc.state.get("region") or {}
    apparent, tmax = region.get("apparent_max_c"), region.get("tmax_c")
    hot = heat_cap_condition(tc) is None
    return list(bit.display_message(tmax, hot, apparent).lines)


def _cap(tc: ToolContext, a: dict[str, Any]) -> ToolResult:
    ev = a["event"]
    lat, lon = _geo(tc, ev)
    place = _place(tc, ev)
    if ev["kind"] == "fall":
        parts = []
        if "torso_angle" in ev:
            parts.append(f"몸통 각도 {ev['torso_angle']}도")
        if "est_speed" in ev:
            parts.append(f"추정 보행 속도 {ev['est_speed']} m/s")
        event, headline = "보행자 낙상 의심", f"{place} 보행자 낙상 의심"
        description = "카메라 규칙 기반 낙상 의심(검증 전 시제품). " + ", ".join(parts)
        instruction = "인근 차량 서행. 119 연계는 시뮬레이션으로만 수행."
    else:
        why_not = heat_cap_condition(tc)
        if why_not is not None:
            return ToolResult({"skipped": True, "reason": why_not}, f"CAP 생략: {why_not}", skipped=True)
        event, headline = "폭염 위험 지역", f"{place} 폭염 위험 지역"
        region = tc.state.get("region") or {}
        apparent, tmax = region.get("apparent_max_c"), region.get("tmax_c")
        if apparent is not None:
            description = f"{ev['date']} 일 체감 최고 {apparent}℃ (기상청 ASOS 관측, 여름철 체감온도)."
        elif tmax is not None:
            description = f"{ev['date']} 일 최고기온 {tmax}℃ (관측)."
        else:
            description = f"{ev['date']} 폭염 위험 (모델 경보)."
        instruction = "독거 노인 방문 점검. 시뮬레이션 훈련 메시지."
    doc = standards.build_cap(
        identifier=_incident_id(tc, ev), sent=tc.now(), event=event, headline=headline,
        description=description.strip(), instruction=instruction, area_desc=place, lat=lat, lon=lon)
    errs = standards.validate_cap(doc)
    if errs:
        raise ToolError("CAP 자체 점검 실패: " + "; ".join(errs))
    # Publish: the server renders CAP XML, validates it against the OASIS CAP 1.2 XSD and stores it for the
    # Atom feed (+ 마을방송 audio and the BIT 2-line text for people without a smartphone).
    # Only a 201 with xsd_valid=true earns the "XSD 검증 통과" label; 422 fails the step.
    bit_lines = bit_lines_for(tc, ev["kind"])
    resp = tc.call("POST", "/cap/alerts", json={"cap": doc, "run_id": tc.run_id, "bit_lines": bit_lines})
    if resp.status_code != 201:
        raise ToolError(f"CAP 발행 거부 {resp.status_code} {_detail(resp)}".strip())
    pub = _json(resp, "/cap/alerts")
    xsd_ok = pub.get("xsd_valid") is True
    with tc.lock:
        tc.state.setdefault("caps", {})[doc["alert"]["identifier"]] = doc
    view = {"cap_id": doc["alert"]["identifier"], "status": "Exercise", "headline": headline,
            "selfcheck_ok": True, "xsd_valid": xsd_ok, "xml_url": pub.get("xml_url"), "feed_url": pub.get("feed_url"),
            "audio_url": pub.get("audio_url"), "audio_script": pub.get("audio_script"), "bit_lines": bit_lines}
    label = XSD_LABEL if xsd_ok else SELFCHECK_LABEL
    return ToolResult(view, f"CAP 1.2 경보 발행 (status=Exercise, {label}, 피드 {pub.get('feed_url')})")


def _fhir(tc: ToolContext, a: dict[str, Any]) -> ToolResult:
    ev = a["event"]
    lat, lon = _geo(tc, ev)
    region = tc.state.get("region")
    tmax = region["tmax_c"] if region else None
    apparent = region.get("apparent_max_c") if region else None
    label = (f"일 체감 최고 {apparent}℃ 기준(기상청 ASOS)" if apparent is not None
             else f"일 최고기온 {tmax}℃ 기준(관측)" if tmax is not None else "기온 자료 없음")
    doc = standards.build_fhir_bundle(
        incident_id=_incident_id(tc, ev), at=tc.now(), uuid_fn=tc.uuid_fn, address=_place(tc, ev),
        lat=lat, lon=lon, tmax_c=tmax, torso_angle_deg=ev.get("torso_angle"),
        est_speed_mps=ev.get("est_speed"), stage_label=label)
    errs = standards.validate_fhir(doc)
    if errs:
        raise ToolError("FHIR 자체 점검 실패: " + "; ".join(errs))
    with tc.lock:
        tc.state.setdefault("bundles", {})[doc["id"]] = doc
    view = {"bundle_id": doc["id"], "resource_count": len(doc["entry"]), "selfcheck_ok": True}
    return ToolResult(view, f"FHIR R4 형식 Bundle 생성 (리소스 {len(doc['entry'])}개, {SELFCHECK_LABEL})")


def _post_sim(tc: ToolContext, path: str, body: dict[str, Any], label: str) -> tuple[int, str]:
    resp = tc.call("POST", path, json=body)
    if resp.status_code != 202:
        raise ToolError(f"{label} 수신 거부 {resp.status_code} {_detail(resp)}".strip())
    receipt = str(_json(resp, path)["receipt_id"])
    return resp.status_code, receipt


def _rsu(tc: ToolContext, a: dict[str, Any]) -> ToolResult:
    msg = a["msg"]
    ev = msg["event"]
    lat, lon = _geo(tc, ev)
    caps = tc.state.get("caps", {})
    cap_id = _incident_id(tc, ev)
    if cap_id not in caps:
        raise ToolError("해당 이벤트의 CAP 경보가 없음 (make_cap_alert 먼저)")
    # Driver-facing text comes from fixed templates only; the model cannot author it.
    text = C.RSU_TEXT_FALL if ev["kind"] == "fall" else C.RSU_TEXT_HEAT
    cause = C.DENM_CAUSE_HUMAN_PRESENCE_ON_ROAD if ev["kind"] == "fall" else C.DENM_CAUSE_EXTREME_WEATHER
    seq = int(hashlib.sha256(tc.run_id.encode()).hexdigest()[:4], 16)
    body = standards.build_rsu_envelope(
        cap_identifier=cap_id, now=tc.now(), lat=lat, lon=lon, cause_code=cause, text=text,
        sequence=seq, validity_s=C.DENM_VALIDITY_S)
    status, receipt = _post_sim(tc, "/sim/rsu", body, C.SIM_RSU_LABEL)
    view = {"channel": "rsu", "simulator": C.SIM_RSU_LABEL, "http_status": status,
            "receipt_id": receipt, "text": text}
    return ToolResult(view, f"{C.SIM_RSU_LABEL} 수신 {status} (receipt {receipt})")


def _report119(tc: ToolContext, a: dict[str, Any]) -> ToolResult:
    bid = a["bundle"]["bundle_id"]
    bundle = tc.state.get("bundles", {}).get(bid)
    cap = tc.state.get("caps", {}).get(bid)
    if bundle is None or cap is None:
        raise ToolError(f"bundle_id {bid!r}: 이 실행에서 만든 Bundle/CAP 쌍이 없음")
    body = {"simulator": C.SIM_119_LABEL, "cap": cap, "fhir": bundle}
    status, receipt = _post_sim(tc, "/sim/119", body, C.SIM_119_LABEL)
    view = {"channel": "119", "simulator": C.SIM_119_LABEL, "http_status": status, "receipt_id": receipt}
    return ToolResult(view, f"{C.SIM_119_LABEL} 수신 {status} (receipt {receipt})")


def _medical(tc: ToolContext, a: dict[str, Any]) -> ToolResult:
    """Nearest 응급의료기관 with 실시간 가용 병상 and what is open now (약국·병의원); read-only, snapshot-labelled."""
    params: dict[str, Any] = {"sgg_code": a["code"]}
    if tc.ctx.get("lat") is not None and tc.ctx.get("lon") is not None:
        params.update(lat=tc.ctx["lat"], lon=tc.ctx["lon"])
    resp = tc.call("GET", "/medical/er", params=params)
    if resp.status_code != 200:  # no live data and no snapshot: not a failure of the run, just no evidence
        return ToolResult({"available": False, "http_status": resp.status_code},
                          f"GET /medical/er → {resp.status_code} (의료 자료 사용 불가)")
    er = _json(resp, "/medical/er")
    now_kst = tc.now().astimezone(standards.KST)
    at = f"{a['date']}T{now_kst.strftime('%H:%M')}"
    resp2 = tc.call("GET", "/medical/open-now", params={**params, "at": at})
    if resp2.status_code != 200:
        return ToolResult({"available": False, "http_status": resp2.status_code},
                          f"GET /medical/open-now → {resp2.status_code} (의료 자료 사용 불가)")
    now = _json(resp2, "/medical/open-now")
    nearest = next((r for r in er["items"] if r.get("er_beds_available") is not None), er["items"][0] if er["items"] else None)
    stale = any((f or {}).get("stale") for f in (*er["fetched"].values(), *now["fetched"].values()))
    view = {
        "available": True, "er_count": er["count"], "er_beds_available_total": er["er_beds_available_total"],
        "beds_updated_at": er["beds_updated_at"],
        "nearest_er": None if nearest is None else {
            "name": nearest["name"], "tel": nearest["tel"], "distance_km": nearest.get("distance_km"),
            "er_beds_available": nearest["er_beds_available"], "beds_updated_at": nearest["beds_updated_at"]},
        "at": now["at"], "is_night": now["is_night"], "is_holiday": now["is_holiday"],
        "pharmacies_open": (now.get("pharmacies") or {}).get("open"),
        "hospitals_open": (now.get("hospitals") or {}).get("open"),
        "er_hospitals": (now.get("hospitals") or {}).get("er_hospitals"),
        "data_stale": stale, "note": er.get("note") or (now.get("pharmacies") or {}).get("note"),
    }
    with tc.lock:
        tc.state["medical"] = view
    if nearest is None:
        summary = f"GET /medical/er → 200 (응급실 {C.NO_DATA_LABEL})"
    else:
        beds = nearest["er_beds_available"]
        summary = (f"GET /medical/er → 200 (가까운 응급실 {nearest['name']} "
                   f"{'가용 병상 ' + str(beds) if beds is not None else '병상 미보고'}"
                   f"{', 스냅샷' if stale else ''}; 열린 약국 {view['pharmacies_open']}곳)")
    return ToolResult(view, summary)


def decode_image(b64: str) -> bytes:
    """Decode and sanity-check a snapshot (size, JPEG/PNG magic).

    Raises:
        ToolError: if it is not valid base64, too large, or not JPEG/PNG.
    """
    if len(b64) > C.MAX_IMAGE_B64_CHARS:
        raise ToolError("이미지가 너무 큼")
    b64 = _B64_WS.sub("", _DATA_URL_PREFIX.sub("", b64.strip()))
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("image_base64가 올바른 base64가 아님") from exc
    if len(raw) > C.MAX_IMAGE_BYTES or not raw.startswith(IMAGE_MAGIC):
        raise ToolError("JPEG/PNG만 허용 (또는 크기 초과)")
    return raw


def clean_vlm_text(text: str) -> tuple[str, bool]:
    """Keep at most VLM_MAX_SENTENCES sentences, strip digits and URLs (a VLM's numbers are not
    measurements); drop the text entirely if it contains a banned term."""
    sentences = [s for s in _SENTENCE_END.split(text.strip()) if s]
    out = " ".join(sentences[: C.VLM_MAX_SENTENCES])
    if any(t in out for t in C.VLM_BANNED_TERMS):
        return "", True
    return re.sub(r"[ ]{2,}", " ", _VLM_STRIP.sub("", out)).strip(), False


def _describe(tc: ToolContext, a: dict[str, Any]) -> ToolResult:
    b64 = tc.image_b64 if a["image_base64"] == C.SNAPSHOT_PLACEHOLDER else a["image_base64"]
    if not b64:
        raise ToolError("스냅샷이 제공되지 않음")
    raw = decode_image(b64)
    tc.image_sha256 = hashlib.sha256(raw).hexdigest()
    b64 = base64.b64encode(raw).decode("ascii")
    if tc.ollama is None or tc.vlm_model is None:
        raise ToolError("VLM 사용 불가 (모델 없음 또는 Ollama 꺼짐)")
    try:
        msg = tc.ollama.chat(
            tc.vlm_model,
            [{"role": "system", "content": VLM_SYSTEM_PROMPT},
             {"role": "user", "content": VLM_USER_PROMPT, "images": [b64]}],
            num_predict=C.VLM_NUM_PREDICT)
    except OllamaUnavailable as exc:
        raise ToolError(f"VLM 호출 실패: {exc}") from exc
    text, filtered = clean_vlm_text(str(msg.get("content", "")))
    view = {"label": C.VLM_LABEL, "description": text, "filtered": filtered,
            "model": tc.vlm_model, "image_sha256": tc.image_sha256}
    # Numbers a VLM mentions are not measurements, so they are not evidence for the briefing.
    evidence = {k: v for k, v in view.items() if k != "description"}
    return ToolResult(view, f"{C.VLM_LABEL}: {text or '(필터링됨)'}", evidence)


_PLAN_PARAMS: JsonSchema = {
    "type": "object", "additionalProperties": False, "required": ["code", "date"],
    "properties": {**_CODE_DATE, "teams": {"type": "integer", "minimum": 1, "maximum": 100}},
}
_CD_PARAMS: JsonSchema = {
    "type": "object", "additionalProperties": False, "required": ["code", "date"], "properties": _CODE_DATE,
}
_EVENT_PARAMS: JsonSchema = {
    "type": "object", "additionalProperties": False, "required": ["event"], "properties": {"event": EVENT_SCHEMA},
}
_RSU_PARAMS: JsonSchema = {
    "type": "object", "additionalProperties": False, "required": ["msg"],
    "properties": {"msg": {
        "type": "object", "additionalProperties": False, "required": ["event"],
        "properties": {"event": EVENT_SCHEMA}}},
}
_119_PARAMS: JsonSchema = {
    "type": "object", "additionalProperties": False, "required": ["bundle"],
    "properties": {"bundle": {
        "type": "object", "additionalProperties": False, "required": ["bundle_id"],
        "properties": {"bundle_id": {"type": "string", "minLength": 1, "maxLength": 128,
                                     "description": "make_fhir_bundle이 돌려준 bundle_id"}}}},
}
_DESCRIBE_PARAMS: JsonSchema = {
    "type": "object", "additionalProperties": False, "required": ["image_base64"],
    "properties": {"image_base64": {
        "type": "string", "minLength": 1,
        "description": f"스냅샷을 쓰려면 정확히 '{C.SNAPSHOT_PLACEHOLDER}' 를 넣는다"}},
}

TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("get_heat_region", "시군구의 그날 폭염 지표(최고기온, 경보 순위, 65세 이상 비중, 환자 수)를 조회한다.",
         _CD_PARAMS, _heat_region),
    Tool("get_heat_plan", "그날 방문 팀 배치 계획과 그 시군구가 포함되는지 조회한다.", _PLAN_PARAMS, _heat_plan),
    Tool("get_actions", "그 시군구의 권고 조치 목록을 조회한다 (엔드포인트가 없으면 사용 불가로 응답).",
         _CD_PARAMS, _actions),
    Tool("describe_scene", "현장 스냅샷을 VLM으로 2문장 이내 묘사한다 (참고용, 신원/진단 없음).",
         _DESCRIBE_PARAMS, _describe),
    Tool("get_medical", "가까운 응급의료기관의 실시간 가용 병상과 지금 열린 약국·병의원 수를 조회한다 (국립중앙의료원 자료, 읽기 전용).",
         _CD_PARAMS, _medical),
    Tool("make_cap_alert", "이벤트로 CAP 1.2 형식 경보(훈련)를 만든다. get_heat_region 뒤에 호출한다.", _EVENT_PARAMS,
         _cap, (), ("get_heat_region", "get_actions")),
    Tool("make_fhir_bundle", "이벤트로 FHIR R4 형식 Bundle(훈련)을 만든다. get_heat_region 뒤에 호출한다.", _EVENT_PARAMS,
         _fhir, (), ("get_heat_region",)),
    Tool("send_rsu_message", "노변기지국(RSU 시뮬레이터)에 고정 문구의 운전자 경고를 보낸다. CAP 경보가 먼저 있어야 한다.",
         _RSU_PARAMS, _rsu, ("make_cap_alert",), ("get_heat_region",)),
    Tool("send_119_report", "CAP와 FHIR Bundle을 119 시뮬레이터에 보낸다. 둘 다 먼저 만들어져 있어야 한다.",
         _119_PARAMS, _report119, ("make_cap_alert", "make_fhir_bundle")),
)}

PLANS: dict[str, tuple[str, ...]] = {
    "fall": ("get_heat_region", "get_actions", "get_medical", "describe_scene", "make_cap_alert", "make_fhir_bundle",
             "send_rsu_message", "send_119_report"),
    "heat_daily": ("get_heat_region", "get_heat_plan", "get_actions", "make_cap_alert"),
}


def canonical_args(tool: str, ctx: dict[str, Any], tc: ToolContext) -> dict[str, Any]:
    """Arguments the deterministic planner uses, built only from the trusted context."""
    event: dict[str, Any] = {k: ctx[k] for k in ("code", "date", "lat", "lon", "est_speed", "torso_angle")
                             if ctx.get(k) is not None}
    event["kind"] = "fall" if tc.ctx.get("kind") == "fall" else "heat"
    if tool in ("get_heat_region", "get_actions", "get_medical"):
        return {"code": ctx["code"], "date": ctx["date"]}
    if tool == "get_heat_plan":
        return {"code": ctx["code"], "date": ctx["date"], "teams": C.DEFAULT_TEAMS}
    if tool == "describe_scene":
        return {"image_base64": C.SNAPSHOT_PLACEHOLDER}
    if tool in ("make_cap_alert", "make_fhir_bundle"):
        return {"event": event}
    if tool == "send_rsu_message":
        return {"msg": {"event": event}}
    return {"bundle": {"bundle_id": f"zp-{tc.run_id}-{ctx['code']}"}}
