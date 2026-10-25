"""Message builders and validators for the simulators.

These are *like* the standards, not conformant encodings: JSON instead of ASN.1 UPER/XML
signatures, no security envelope. Fields that mirror the standards:

ETSI EN 302 637-3 (DENM) -> `denm`: header{protocolVersion,messageId=1,stationId};
  management{actionId{originatingStationId,sequenceNumber}, detectionTime, eventPosition,
  validityDuration[s], stationType}; situation{informationQuality, eventType{causeCode,subCauseCode}}.
  causeCode 12 = humanPresenceOnTheRoad, 17 = adverseWeatherCondition-ExtremeWeatherCondition
  (ETSI TS 102 894-2). Time fields are ISO 8601 with offset (DENM uses ms since 2004-01-01).
SAE J2735 TIM -> `tim`: msgCnt, timeStamp [MinuteOfTheYear], dataFrames[]{frameType, startTime
  [MinuteOfTheYear], durationTime [minutes], priority, regions[].anchor{lat,long},
  content.advisory[].item.text}. Relaxations: degrees as floats (J2735: 1/10 microdegree ints),
  text up to 80 chars UTF-8 (J2735 text is IA5String 1..16).
OASIS CAP 1.2 -> `cap`: identifier, sender, sent (numeric offset, never "Z"), status, msgType,
  scope (+ restriction when Restricted), info{category,event,urgency,severity,certainty,area}.
HL7 FHIR R4 -> `fhir`: Bundle(type=collection) with Patient, Encounter, Location, Observation;
  entry.fullUrl unique, internal references resolve inside the bundle.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import constants as C

KST = timezone(timedelta(minutes=C.KST_OFFSET_MIN))
_CAP_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
SYS_ACT_REASON = "http://terminology.hl7.org/CodeSystem/v3-ActReason"
SYS_ACT_CODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
SYS_DAR = "http://terminology.hl7.org/CodeSystem/data-absent-reason"
SYS_UCUM = "http://unitsofmeasure.org"
DRILL_TAG = {"system": SYS_ACT_REASON, "code": "HTEST", "display": "test health data"}
PERFORMER_NAME = "고령자 안전 스마트도시 에이전트 (시뮬레이션, 훈련)"  # Observation.performer -> this Organization
_URN_UUID_RE = re.compile(r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
OBS_STATUSES = ("registered", "preliminary", "final", "amended", "corrected", "cancelled",
                "entered-in-error", "unknown")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DenmHeader(_Strict):
    protocolVersion: int = Field(ge=0, le=255)
    messageId: Literal[1]
    stationId: int = Field(ge=0, le=2**32 - 1)


class ActionId(_Strict):
    originatingStationId: int = Field(ge=0, le=2**32 - 1)
    sequenceNumber: int = Field(ge=0, le=65535)


class EventPosition(_Strict):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class DenmManagement(_Strict):
    actionId: ActionId
    detectionTime: datetime
    eventPosition: EventPosition
    validityDuration: int = Field(ge=0, le=86400)
    stationType: int = Field(ge=0, le=255)


class EventType(_Strict):
    causeCode: int = Field(ge=0, le=255)
    subCauseCode: int = Field(ge=0, le=255)


class DenmSituation(_Strict):
    informationQuality: int = Field(ge=0, le=7)
    eventType: EventType


class Denm(_Strict):
    header: DenmHeader
    management: DenmManagement
    situation: DenmSituation


class TimAnchor(_Strict):
    lat: float = Field(ge=-90, le=90)
    long: float = Field(ge=-180, le=180)


class TimRegion(_Strict):
    anchor: TimAnchor


class TimAdvisoryItem(_Strict):
    text: str = Field(min_length=1, max_length=C.RSU_TEXT_MAX)


class TimAdvisory(_Strict):
    item: TimAdvisoryItem


class TimContent(_Strict):
    advisory: list[TimAdvisory] = Field(min_length=1)


class TimFrame(_Strict):
    frameType: Literal["advisory"]
    startTime: int = Field(ge=0, le=C.MINUTES_PER_YEAR_MAX)
    durationTime: int = Field(ge=0, le=32000)
    priority: int = Field(ge=0, le=7)
    regions: list[TimRegion] = Field(min_length=1)
    content: TimContent


class Tim(_Strict):
    msgCnt: int = Field(ge=0, le=127)
    timeStamp: int = Field(ge=0, le=C.MINUTES_PER_YEAR_MAX)
    dataFrames: list[TimFrame] = Field(min_length=1)


class RsuEnvelope(_Strict):
    """Body of POST /sim/rsu."""

    simulator: Literal["RSU 시뮬레이터"]
    related_cap_identifier: str = Field(min_length=1, max_length=128, pattern=r"^[^\s,<&]+$")
    denm: Denm
    tim: Tim


def minute_of_year(t: datetime) -> int:
    """J2735 MinuteOfTheYear (UTC)."""
    u = t.astimezone(timezone.utc)
    start = datetime(u.year, 1, 1, tzinfo=timezone.utc)
    return int((u - start).total_seconds() // 60)


def cap_datetime(t: datetime) -> str:
    """CAP dateTime in KST with numeric offset."""
    return t.astimezone(KST).isoformat(timespec="seconds")


def build_rsu_envelope(
    *, cap_identifier: str, now: datetime, lat: float, lon: float, cause_code: int, text: str,
    sequence: int, validity_s: int = C.DENM_VALIDITY_S,
) -> dict[str, Any]:
    """DENM-like + TIM-like body for the RSU simulator."""
    moy = minute_of_year(now)
    return {
        "simulator": C.SIM_RSU_LABEL,
        "related_cap_identifier": cap_identifier,
        "denm": {
            "header": {"protocolVersion": 2, "messageId": 1, "stationId": C.DENM_STATION_ID},
            "management": {
                "actionId": {"originatingStationId": C.DENM_STATION_ID, "sequenceNumber": sequence},
                "detectionTime": now.astimezone(KST).isoformat(timespec="seconds"),
                "eventPosition": {"latitude": lat, "longitude": lon},
                "validityDuration": validity_s,
                "stationType": C.DENM_STATION_TYPE_RSU,
            },
            "situation": {
                "informationQuality": C.DENM_INFO_QUALITY,
                "eventType": {"causeCode": cause_code, "subCauseCode": 0},
            },
        },
        "tim": {
            "msgCnt": sequence % 128,
            "timeStamp": moy,
            "dataFrames": [{
                "frameType": "advisory", "startTime": moy, "durationTime": C.TIM_DURATION_MIN,
                "priority": C.TIM_PRIORITY,
                "regions": [{"anchor": {"lat": lat, "long": lon}}],
                "content": {"advisory": [{"item": {"text": text}}]},
            }],
        },
    }


def build_cap(
    *, identifier: str, sent: datetime, event: str, headline: str, description: str,
    instruction: str, area_desc: str, lat: float, lon: float,
) -> dict[str, Any]:
    """CAP 1.2 JSON (same shape as frontend/src/lib/cap.ts), status=Exercise, scope=Restricted."""
    return {"alert": {
        "xmlns": C.CAP_NAMESPACE, "identifier": identifier, "sender": C.CAP_SENDER,
        "sent": cap_datetime(sent), "status": "Exercise", "msgType": "Alert", "scope": "Restricted",
        "restriction": "시뮬레이션 훈련 메시지 · 외부 전송 안 함",
        "note": "This is an exercise message generated by a simulation; it was not transmitted.",
        "info": {
            "language": C.CAP_LANGUAGE, "category": ["Health", "Safety"], "event": event,
            "urgency": "Immediate", "severity": "Severe", "certainty": "Possible",
            "headline": headline, "description": description, "instruction": instruction,
            "area": {"areaDesc": area_desc, "circle": f"{lat},{lon} {C.CAP_RADIUS_KM}"},
        },
    }}


def validate_cap(doc: Any) -> list[str]:
    """Required-field self-check of a CAP 1.2-shaped JSON alert (not schema conformance); returns errors."""
    errs: list[str] = []
    alert = doc.get("alert") if isinstance(doc, dict) else None
    if not isinstance(alert, dict):
        return ["cap.alert: required object"]
    for key in ("identifier", "sender", "sent", "status", "msgType", "scope"):
        if not isinstance(alert.get(key), str) or not alert[key]:
            errs.append(f"cap.alert.{key}: required string")
    ident = alert.get("identifier")
    if isinstance(ident, str) and re.search(r"[\s,<&]", ident):
        errs.append("cap.alert.identifier: must not contain whitespace, comma, < or &")
    sent = alert.get("sent")
    if isinstance(sent, str) and not _CAP_TIME_RE.match(sent):
        errs.append("cap.alert.sent: ISO 8601 with numeric offset required ('Z' not allowed)")
    if alert.get("status") not in ("Actual", "Exercise", "System", "Test", "Draft"):
        errs.append("cap.alert.status: invalid")
    if alert.get("msgType") not in ("Alert", "Update", "Cancel", "Ack", "Error"):
        errs.append("cap.alert.msgType: invalid")
    if alert.get("scope") not in ("Public", "Restricted", "Private"):
        errs.append("cap.alert.scope: invalid")
    if alert.get("scope") == "Restricted" and not alert.get("restriction"):
        errs.append("cap.alert.restriction: required when scope=Restricted")
    info = alert.get("info")
    if not isinstance(info, dict):
        errs.append("cap.alert.info: required object")
        return errs
    for key in ("event", "urgency", "severity", "certainty"):
        if not isinstance(info.get(key), str) or not info[key]:
            errs.append(f"cap.alert.info.{key}: required string")
    if not isinstance(info.get("category"), list) or not info["category"]:
        errs.append("cap.alert.info.category: required non-empty list")
    area = info.get("area")
    if not isinstance(area, dict) or not area.get("areaDesc"):
        errs.append("cap.alert.info.area.areaDesc: required")
    return errs


def _quantity(v: float, unit: str, code: str) -> dict[str, Any]:
    return {"value": round(v, 2), "unit": unit, "system": SYS_UCUM, "code": code}


def build_fhir_bundle(
    *, incident_id: str, at: datetime, uuid_fn: Any, address: str, lat: float, lon: float,
    tmax_c: float | None, torso_angle_deg: float | None, est_speed_mps: float | None,
    stage_label: str,
) -> dict[str, Any]:
    """FHIR R4 collection Bundle for a drill, port of frontend/src/lib/fhir.ts (HTEST tag, preliminary)."""
    def fresh() -> str:
        """RFC 4122 text form of whatever `uuid_fn` yields (hex or dashed); fails on a non-UUID."""
        raw = uuid_fn()
        try:
            return str(uuid.UUID(raw))
        except ValueError as exc:
            raise ValueError(f"uuid_fn must return a UUID, got {raw!r}") from exc

    patient, encounter, location, performer = fresh(), fresh(), fresh(), fresh()
    when = at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    ref = {"reference": ""}

    def r(u: str) -> dict[str, str]:
        return {**ref, "reference": f"urn:uuid:{u}"}

    def narrative(text: str) -> dict[str, str]:
        # dom-6 (best practice): every DomainResource carries a generated narrative.
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return {"status": "generated", "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{safe}</div>'}

    def obs(text: str, value: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        u = fresh()
        body: dict[str, Any] = {
            "resourceType": "Observation", "meta": {"tag": [DRILL_TAG]}, "text": narrative(f"{text}. Drill."),
            "status": "preliminary",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                      "code": "survey"}]}],
            "code": {"text": text}, "subject": r(patient), "encounter": r(encounter),
            "effectiveDateTime": when, "performer": [r(performer)],
        }
        body.update(value if value is not None else
                    {"dataAbsentReason": {"coding": [{"system": SYS_DAR, "code": "not-performed"}]}})
        return u, body

    observations = [
        obs("Fall detected (prototype rule, not validated)", {"valueBoolean": True}),
        obs("Estimated gait speed (camera, needs calibration)",
            None if est_speed_mps is None else {"valueQuantity": _quantity(est_speed_mps, "m/s", "m/s")}),
        obs("Torso angle from vertical",
            None if torso_angle_deg is None else {"valueQuantity": _quantity(torso_angle_deg, "degree", "deg")}),
        obs("Daily maximum air temperature (public weather data)",
            None if tmax_c is None else {"valueQuantity": _quantity(tmax_c, "degree Celsius", "Cel")}),
        obs("Heat stage (daily max temperature proxy)", {"valueString": stage_label}),
    ]

    def entry(u: str, res: dict[str, Any]) -> dict[str, Any]:
        return {"fullUrl": f"urn:uuid:{u}", "resource": res}

    return {
        "resourceType": "Bundle", "id": incident_id, "meta": {"tag": [DRILL_TAG]},
        "type": "collection", "timestamp": when,
        "entry": [
            entry(patient, {"resourceType": "Patient", "meta": {"tag": [DRILL_TAG]}, "active": True,
                            "text": {"status": "generated",
                                     "div": '<div xmlns="http://www.w3.org/1999/xhtml">Anonymous subject. Drill.</div>'}}),
            entry(performer, {"resourceType": "Organization", "meta": {"tag": [DRILL_TAG]},
                              "text": narrative(PERFORMER_NAME), "active": True, "name": PERFORMER_NAME}),
            entry(location, {"resourceType": "Location", "meta": {"tag": [DRILL_TAG]}, "status": "active",
                             "text": narrative(f"{address}. Drill."), "name": address, "mode": "instance",
                             "position": {"longitude": lon, "latitude": lat}}),
            entry(encounter, {"resourceType": "Encounter", "meta": {"tag": [DRILL_TAG]},
                              "text": narrative("Emergency encounter opened by a drill."),
                              "status": "in-progress",
                              "class": {"system": SYS_ACT_CODE, "code": "EMER", "display": "emergency"},
                              "subject": r(patient), "period": {"start": when},
                              "location": [{"location": r(location)}]}),
            *[entry(u, res) for u, res in observations],
        ],
    }


def validate_fhir(doc: Any) -> list[str]:
    """Required-field and reference-integrity self-check of a FHIR R4-shaped collection Bundle.

    This is not conformance validation against the FHIR profiles; see the module docstring."""
    if not isinstance(doc, dict):
        return ["fhir: required object"]
    errs: list[str] = []
    if doc.get("resourceType") != "Bundle":
        errs.append("fhir.resourceType: must be Bundle")
    if doc.get("type") != "collection":
        errs.append("fhir.type: must be collection")
    entries = doc.get("entry")
    if not isinstance(entries, list) or not entries:
        return [*errs, "fhir.entry: required non-empty list"]
    urls: set[str] = set()
    kinds: set[str] = set()
    refs: list[str] = []
    for i, e in enumerate(entries):
        res = e.get("resource") if isinstance(e, dict) else None
        url = e.get("fullUrl") if isinstance(e, dict) else None
        if not isinstance(res, dict) or not isinstance(res.get("resourceType"), str):
            errs.append(f"fhir.entry[{i}].resource.resourceType: required")
            continue
        if not isinstance(url, str) or not url:
            errs.append(f"fhir.entry[{i}].fullUrl: required")
        elif not _URN_UUID_RE.match(url):
            errs.append(f"fhir.entry[{i}].fullUrl: must be urn:uuid:<RFC 4122 uuid>")
        elif url in urls:
            errs.append(f"fhir.entry[{i}].fullUrl: duplicate")
        else:
            urls.add(url)
        kinds.add(res["resourceType"])
        if res["resourceType"] == "Observation":
            if res.get("status") not in OBS_STATUSES:
                errs.append(f"fhir.entry[{i}].status: invalid Observation.status")
            if "code" not in res:
                errs.append(f"fhir.entry[{i}].code: required")
        for key in ("subject", "encounter"):
            sub = res.get(key)
            if isinstance(sub, dict) and isinstance(sub.get("reference"), str):
                refs.append(sub["reference"])
        for perf in res.get("performer", []) if isinstance(res.get("performer"), list) else []:
            if isinstance(perf, dict) and isinstance(perf.get("reference"), str):
                refs.append(perf["reference"])
    errs.extend(f"fhir: missing resource {k}" for k in ("Patient", "Encounter", "Location") if k not in kinds)
    errs.extend(f"fhir: unresolved reference {x}" for x in refs if x not in urls)
    return errs
