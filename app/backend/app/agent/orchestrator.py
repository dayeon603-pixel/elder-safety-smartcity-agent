"""LLM-planned, server-scheduled action run with guardrails and a deterministic fallback.

One structured-output call asks the model for the whole plan (`{"steps":[{"tool","args"}]}`,
constrained by a JSON schema). The server validates every proposed step (whitelist per trigger,
prerequisites, no repeats, argument schema, context binding), then executes the validated plan in
dependency waves: independent tools run in parallel (reads ∥ VLM, CAP ∥ FHIR, RSU ∥ 119) and
read-only tools are prefetched while the model is still planning. Anything the plan omitted is
completed deterministically and labelled as such. The final briefing is streamed from the model and
must pass a numeric grounding check, else a template is used. A monotonic run budget bounds every
LLM call; after the first LLM failure or timeout no further LLM call is attempted.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx

from . import constants as C
from .guard import bind_context, check_briefing, collect_evidence, validate_schema
from .ollama import OllamaClient, OllamaUnavailable
from .tools import PLANS, TOOLS, ToolContext, ToolError, ToolResult, canonical_args

logger = logging.getLogger(__name__)

EventSink = Callable[[str, dict[str, Any]], None]
PREFETCH_TOOLS: frozenset[str] = frozenset({"get_heat_region", "get_actions", "get_heat_plan", "get_medical",
                                             "describe_scene"})
_TOOL_BUGS = (KeyError, TypeError, ValueError, AttributeError, IndexError)

SYSTEM_PROMPT = (
    "당신은 고령자 안전 스마트도시 행동 에이전트의 계획기입니다. 주어진 트리거와 허용 도구 목록을 보고, "
    "실행할 도구 목록을 순서대로 JSON으로만 답합니다: {\"steps\":[{\"tool\":\"<도구명>\",\"args\":{}}]}.\n"
    "규칙: 허용된 도구만, 각 도구는 한 번만. args는 비워 두면 서버가 신뢰할 수 있는 context 값으로 채웁니다; "
    "직접 쓰려면 context 값만 그대로 씁니다. send_rsu_message는 make_cap_alert 뒤에, send_119_report는 "
    "make_cap_alert와 make_fhir_bundle 뒤에 둡니다. 도구 설명:\n"
)
BRIEF_SYSTEM = (
    "당신은 고령자 안전 스마트도시 행동 에이전트입니다. 도구 결과만 근거로 한국어 합니다체 브리핑을 씁니다. "
    "규칙: 5줄 이내, 한 줄에 숫자 하나 이하, 번호 목록 금지, 도구 결과에 있는 숫자만 그대로 쓰고 계산하거나 추측하지 "
    "않습니다. 진단하지 않습니다. RSU와 119는 시뮬레이터라고 밝힙니다. 표준 적합성을 주장하지 않습니다."
)
BRIEF_USER = (
    "지역 이름과 최고기온을 쓰고, RSU/119 전송 결과가 있으면 '시뮬레이터 수신'과 상태 코드를 그대로 쓰십시오. "
    "브리핑 본문만 쓰십시오."
)


@dataclass(slots=True)
class AgentEnv:
    """Injected collaborators (real in production, fakes in tests)."""

    http: httpx.Client
    ollama: OllamaClient | None
    now: Callable[[], datetime]
    uuid_hex: Callable[[], str]
    demo_token: str | None = None
    on_event: EventSink | None = None
    budget_s: float = C.RUN_BUDGET_S
    workers: int = C.TOOL_WORKERS


@dataclass(slots=True)
class Deadline:
    """Monotonic run budget."""

    budget_s: float
    t0: float = field(default_factory=time.perf_counter)

    def remaining(self) -> float:
        return max(0.0, self.budget_s - (time.perf_counter() - self.t0))

    def llm_timeout(self) -> float | None:
        """Timeout for one LLM call, or None when too little budget is left to start one."""
        left = self.remaining()
        return None if left < C.MIN_LLM_CALL_S else min(C.OLLAMA_CHAT_TIMEOUT_S, left)


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)


def redact(obj: Any) -> Any:
    """Shorten long strings (e.g. a base64 image) before logging arguments."""
    if isinstance(obj, str) and len(obj) > 200:
        return f"<{len(obj)} chars>"
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def _fmt(v: float, places: int) -> str:
    return str(Decimal(repr(v)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def temperature_phrase(region: dict[str, Any]) -> str | None:
    """'체감 최고 N℃' when the apparent maximum is known, else '최고기온 N℃', else None."""
    apparent = region.get("apparent_max_c")
    if isinstance(apparent, (int, float)):
        return f"체감 최고 {apparent}℃"
    tmax = region.get("tmax_c")
    if isinstance(tmax, (int, float)):
        return f"오늘 최고기온 {tmax}℃"
    return None


def template_briefing(trigger: str, ctx: dict[str, Any], results: dict[str, ToolResult],
                      failed: dict[str, str]) -> str:
    """Deterministic briefing whose every number is copied (or rounded) from a tool result.

    A line is written only when its value exists: no "None" ever reaches the text.
    """
    region = results["get_heat_region"].view if "get_heat_region" in results else None
    head = "낙상 의심" if trigger == "fall" else "폭염 일일 브리핑"
    where = f"{region['sido']} {region['name']}" if region else f"시군구 {ctx['code']}"
    lines = [f"{head} · {where} · {ctx['date']}"]
    if region:
        temp = temperature_phrase(region)
        lines.append(temp if temp else "최고기온 자료 없음")
        if isinstance(region.get("ours_rank"), int):
            lines.append(f"경보 순위 {region['ours_rank']}위")
        if isinstance(region.get("old_share"), (int, float)):
            lines.append(f"65세 이상 비중 {_fmt(region['old_share'] * 100, 1)}%")
    elif "get_heat_region" in failed:
        lines.append("지역 지표 조회 실패")
    if "get_heat_plan" in results:
        p = results["get_heat_plan"].view
        if isinstance(p.get("teams"), int) and isinstance(p.get("region_teams"), int):
            lines.append(f"방문 팀 {p['teams']}개 중 이 지역 배정 {p['region_teams']}개")
        if isinstance(p.get("b1_reached"), int) and isinstance(p.get("ours_reached"), int):
            lines.append(f"환자 도달 기존 기준 {p['b1_reached']} → 우리 {p['ours_reached']}")
    cap = results.get("make_cap_alert")
    if cap is not None and cap.skipped:
        lines.append(f"경보 생략: {cap.view['reason']}")
    med = results["get_medical"].view if "get_medical" in results else None
    if med and med.get("nearest_er"):
        er = med["nearest_er"]
        beds = er.get("er_beds_available")
        tail = f" 가용 병상 {beds}" if isinstance(beds, int) else " 병상 미보고"
        lines.append(f"가까운 응급실 {er['name']}{tail}{' (스냅샷)' if med.get('data_stale') else ''}")
        if med.get("is_night") and isinstance(med.get("pharmacies_open"), int):
            lines.append(f"지금 열린 약국 {med['pharmacies_open']}곳")
    for tool, label in (("send_rsu_message", C.SIM_RSU_LABEL), ("send_119_report", C.SIM_119_LABEL)):
        if tool in results:
            v = results[tool].view
            lines.append(f"{label} 수신 {v['http_status']} (receipt {v['receipt_id']})")
        elif tool in failed:
            lines.append(f"{label} 전송 실패")
    return "\n".join(lines)


def plan_schema(allowed: list[str]) -> dict[str, Any]:
    """JSON schema handed to Ollama `format`: decoding is constrained to a plan over `allowed`."""
    return {
        "type": "object", "required": ["steps"],
        "properties": {"steps": {
            "type": "array", "maxItems": C.MAX_PLAN_STEPS,
            "items": {"type": "object", "required": ["tool", "args"],
                      "properties": {"tool": {"type": "string", "enum": allowed},
                                     "args": {"type": "object"}}}}},
    }


def parse_plan(content: str) -> list[tuple[str, Any]] | None:
    """(tool, args) pairs from the model's JSON, or None when it is not a plan object at all."""
    try:
        obj = json.loads(content)
    except ValueError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("steps"), list):
        return None
    out: list[tuple[str, Any]] = []
    for item in obj["steps"][: C.MAX_PLAN_STEPS]:
        if isinstance(item, dict):
            out.append((str(item.get("tool", "")), item.get("args", {})))
        else:
            out.append((str(item), {}))
    return out


def briefing_digest(results: dict[str, ToolResult], failed: dict[str, str]) -> str:
    """Compact, numbers-only view of the results the briefing may cite (keeps the prompt short)."""
    d: dict[str, Any] = {}
    if "get_heat_region" in results:
        r = results["get_heat_region"].view
        d["지역"] = {k: r.get(k) for k in ("sido", "name", "tmax_c", "apparent_max_c", "ours_rank", "old_share",
                                           "cases_today") if r.get(k) is not None}
    if "get_heat_plan" in results:
        p = results["get_heat_plan"].view
        d["방문계획"] = {k: p.get(k) for k in ("teams", "region_teams", "b1_reached", "ours_reached")}
    if "get_actions" in results:
        a = results["get_actions"].view
        d["조치"] = {k: a.get(k) for k in ("alerted", "cbs_length", "bit_stop_count", "signal_count") if k in a}
    if "get_medical" in results:
        m = results["get_medical"].view
        d["의료"] = {"가까운_응급실": (m.get("nearest_er") or {}).get("name"),
                   "가용_병상": (m.get("nearest_er") or {}).get("er_beds_available"),
                   "열린_약국": m.get("pharmacies_open"), "밤": m.get("is_night"), "스냅샷": m.get("data_stale")}
    for tool, label in (("make_cap_alert", "CAP"), ("make_fhir_bundle", "FHIR"),
                        ("send_rsu_message", "RSU"), ("send_119_report", "119")):
        if tool in results:
            v = results[tool].view
            d[label] = ({"skipped": True, "reason": v.get("reason")} if results[tool].skipped else
                        {k: v[k] for k in ("simulator", "http_status", "status", "resource_count") if k in v})
        elif tool in failed:
            d[label] = {"failed": True}
    return json.dumps(d, ensure_ascii=False)[: C.LLM_VIEW_MAX_CHARS]


class _Run:
    """Mutable state of one run; methods are called from worker threads under `lock`."""

    def __init__(self, env: AgentEnv, trigger: str, raw_ctx: dict[str, Any]) -> None:
        self.env = env
        self.trigger = trigger
        self.deadline = Deadline(env.budget_s)
        self.created_at = env.now().isoformat(timespec="milliseconds")
        self.image_b64: str | None = raw_ctx.get("image_base64")
        self.ctx = {k: v for k, v in raw_ctx.items() if k != "image_base64" and v is not None}
        self.ctx_vals = {**self.ctx, "kind": "fall" if trigger == "fall" else "heat"}
        self.run_id = f"run-{env.uuid_hex()[:12]}"
        self.lock = threading.Lock()
        self.results: dict[str, ToolResult] = {}
        self.failed: dict[str, str] = {}
        self.steps: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self.evidence: list[float] = collect_evidence(self.ctx)
        self.llm_dead = False
        self.llm_turns: list[dict[str, Any]] = []
        self.models: dict[str, str] | None = None
        self.live = False
        self.vlm_ok = False
        self.reason = ""
        self.required: list[str] = []
        self.planned: dict[str, tuple[dict[str, Any], list[str]]] = {}  # validated LLM plan: tool -> (args, notes)
        self.tc: ToolContext

    # ------------------------------------------------------------------ events
    def emit(self, kind: str, data: dict[str, Any]) -> None:
        if self.env.on_event is not None:
            try:
                self.env.on_event(kind, data)
            except (RuntimeError, ValueError) as exc:  # a closed stream must not abort the run
                logger.warning("event sink failed: %s", exc)

    def note(self, text: str) -> None:
        with self.lock:
            self.notes.append(text)
        self.emit("note", {"text": text})

    # ------------------------------------------------------------------ probe
    def probe(self) -> None:
        if self.env.ollama is None:
            self.reason = "Ollama 클라이언트 없음"
        else:
            try:
                self.models = self.env.ollama.models()
            except OllamaUnavailable as exc:
                self.reason = f"Ollama 응답 없음 ({exc})"
        self.live = self.models is not None and C.PLANNER_MODEL in self.models
        if self.models is not None and not self.live:
            self.reason = f"모델 {C.PLANNER_MODEL} 없음"
        self.vlm_ok = self.models is not None and C.VLM_MODEL in self.models
        self.required = [t for t in PLANS[self.trigger]
                         if t != "describe_scene" or (self.image_b64 and self.vlm_ok)]
        if self.image_b64 and not self.vlm_ok:
            self.note(f"VLM {C.VLM_MODEL} 없음 또는 Ollama 꺼짐: describe_scene 생략")
        if not self.live:
            self.note(f"결정론적 플래너 사용: {self.reason}")
        self.tc = ToolContext(
            http=self.env.http, ollama=self.env.ollama if self.models is not None else None, ctx=self.ctx_vals,
            image_b64=self.image_b64, run_id=self.run_id, now=self.env.now, uuid_fn=self.env.uuid_hex,
            vlm_model=C.VLM_MODEL if self.vlm_ok else None,
            vlm_digest=self.models.get(C.VLM_MODEL) if self.models and self.vlm_ok else None,
            demo_token=self.env.demo_token, evidence_fn=self.snapshot_evidence)

    def snapshot_evidence(self) -> list[float]:
        with self.lock:
            return list(self.evidence)

    # ------------------------------------------------------------------ validation
    def check(self, name: str, args: Any) -> tuple[dict[str, Any] | None, list[str], str, bool]:
        """(bound args, correction notes, rejection reason or '', raw_schema_invalid)."""
        tool = TOOLS.get(name)
        if tool is None or name not in self.required:
            return None, [], f"허용되지 않은 도구: {name}", False
        if not isinstance(args, dict):
            return None, [], "arguments가 객체가 아님", True
        canonical = canonical_args(name, self.ctx, self.tc)
        if not args:
            args = canonical
        raw_invalid = bool(validate_schema(args, tool.parameters))
        bound, corr = bind_context(args, self.ctx_vals)
        if name == "send_119_report" and bound != canonical:
            # The only bundle this run may report is the one it makes itself; the id is server-issued.
            bound, corr = canonical, [*corr, "$.bundle.bundle_id: replaced with this run's bundle id"]
        errs = validate_schema(bound, tool.parameters)
        if errs:
            return None, corr, "스키마 오류: " + "; ".join(errs[:3]), raw_invalid
        return bound, corr, "", raw_invalid

    # ------------------------------------------------------------------ execution
    def attempted(self, name: str) -> bool:
        return name in self.results or name in self.failed

    def ready(self, name: str) -> bool:
        tool = TOOLS[name]
        if any(p not in self.results or self.results[p].skipped for p in tool.prerequisites):
            return False
        return all(self.attempted(p) for p in tool.after if p in self.required)

    def blocked_reason(self, name: str) -> str:
        tool = TOOLS[name]
        missing = [p for p in tool.prerequisites if p not in self.results or self.results[p].skipped]
        return f"선행 도구 결과 없음: {missing}" if missing else "선행 도구 미완료"

    def execute(self, name: str, args: dict[str, Any], source: str, corr: list[str]) -> ToolResult | None:
        """Run one tool on the calling thread; record the measured step; emit it."""
        self.tc.tls.log = []
        t0 = time.perf_counter()
        res: ToolResult | None = None
        err = ""
        try:
            res = TOOLS[name].run(self.tc, args)
        except ToolError as exc:
            err = str(exc)
        except _TOOL_BUGS as exc:  # a tool bug is a failed step, never a crashed run
            logger.exception("tool %s raised %s", name, type(exc).__name__)
            err = f"도구 내부 오류 ({type(exc).__name__})"
        ms = _ms(t0)
        status = "failed" if res is None else "skipped" if res.skipped else "ok"
        with self.lock:
            rec: dict[str, Any] = {
                "n": len(self.steps) + 1, "tool": name, "source": source, "args": redact(args),
                "arg_corrections": corr, "ok": res is not None, "status": status, "ms": ms,
                "http": list(self.tc.tls.log), "summary": res.summary if res else err,
            }
            if res is not None:
                self.results[name] = res
                rec["result"] = res.view
                collect_evidence(res.evidence, self.evidence)  # a skip reason is server text, hence evidence
            else:
                self.failed[name] = err
            self.steps.append(rec)
        self.emit("step", {k: rec[k] for k in ("n", "tool", "source", "status", "ok", "ms", "summary")})
        return res

    def execute_planned(self, name: str, source: str) -> ToolResult | None:
        """Run with the validated LLM arguments when the plan has them, else canonical ones."""
        if name in self.planned:
            planned_args, planned_corr = self.planned[name]
            return self.execute(name, planned_args, source, planned_corr)
        args, corr, why, _ = self.check(name, {})
        if args is None:
            with self.lock:
                self.failed[name] = f"결정론적 인자 오류: {why}"
                self.steps.append({"n": len(self.steps) + 1, "tool": name, "source": source, "ok": False,
                                   "status": "failed", "ms": 0.0, "http": [], "summary": self.failed[name],
                                   "args": {}, "arg_corrections": []})
            return None
        return self.execute(name, args, source, corr)

    # ------------------------------------------------------------------ LLM calls
    def llm(self) -> OllamaClient | None:
        """The planner client when it is live, within budget and has not failed yet; else None."""
        if not self.live or self.llm_dead or self.env.ollama is None:
            return None
        if self.deadline.llm_timeout() is None:
            self.llm_dead = True
            self.note(f"실행 예산 {self.env.budget_s:.0f}초 소진: 이후 LLM 호출 생략")
            return None
        return self.env.ollama

    def llm_failed(self, what: str, exc: OllamaUnavailable) -> None:
        self.llm_dead = True
        self.note(f"{what} LLM 호출 실패 ({exc}): 이후 LLM 호출 생략")

    def plan_call(self) -> tuple[list[tuple[str, Any]] | None, str]:
        """One structured-output call; returns (parsed plan or None, failure reason or '')."""
        client = self.llm()
        if client is None:
            return None, self.reason or "LLM 사용 불가"
        timeout = self.deadline.llm_timeout()
        assert timeout is not None
        tools_doc = "\n".join(f"- {t}: {TOOLS[t].description}" for t in self.required)
        user = (f"trigger: {self.trigger}\ncontext: {json.dumps(self.ctx, ensure_ascii=False)}\n"
                f"스냅샷: {'있음' if 'describe_scene' in self.required else '없음'}\n"
                f"허용 도구와 권장 순서: {' → '.join(self.required)}")
        t0 = time.perf_counter()
        try:
            msg = client.chat(C.PLANNER_MODEL, [{"role": "system", "content": SYSTEM_PROMPT + tools_doc},
                                                 {"role": "user", "content": user}],
                              num_predict=C.LLM_NUM_PREDICT_PLAN, fmt=plan_schema(self.required),
                              timeout_s=timeout)
        except OllamaUnavailable as exc:
            self.llm_turns.append({"kind": "plan", "ms": _ms(t0), "ok": False})
            self.llm_failed("계획", exc)
            return None, f"LLM 호출 실패 ({exc})"
        self.llm_turns.append({"kind": "plan", "ms": _ms(t0), "ok": True})
        plan = parse_plan(str(msg.get("content", "")))
        if plan is None:
            return None, "LLM 계획이 JSON 계획 형식이 아님"
        return plan, ""

    def brief_call(self) -> str | None:
        client = self.llm()
        if client is None:
            return None
        timeout = self.deadline.llm_timeout()
        assert timeout is not None
        digest = briefing_digest(self.results, self.failed)
        t0 = time.perf_counter()
        try:
            text = client.chat_stream(
                C.PLANNER_MODEL,
                [{"role": "system", "content": BRIEF_SYSTEM},
                 {"role": "user", "content": f"trigger: {self.trigger}\n도구 결과: {digest}\n{BRIEF_USER}"}],
                num_predict=C.LLM_NUM_PREDICT_BRIEF, timeout_s=timeout,
                on_delta=lambda d: self.emit("briefing_delta", {"text": d}))
        except OllamaUnavailable as exc:
            self.llm_turns.append({"kind": "brief", "ms": _ms(t0), "ok": False})
            self.llm_failed("브리핑", exc)
            return None
        self.llm_turns.append({"kind": "brief", "ms": _ms(t0), "ok": True})
        return text.strip()


def _wave(run: _Run, pool: ThreadPoolExecutor, todo: list[str], sources: dict[str, str]) -> None:
    """Execute `todo` in dependency waves, each wave in parallel, until every tool was attempted."""
    pending = list(todo)
    while pending:
        ready = [t for t in pending if run.ready(t)]
        if not ready:
            for t in pending:
                with run.lock:
                    run.failed[t] = run.blocked_reason(t)
                    run.steps.append({"n": len(run.steps) + 1, "tool": t, "source": sources[t], "ok": False,
                                      "status": "failed", "ms": 0.0, "http": [], "summary": run.failed[t],
                                      "args": {}, "arg_corrections": []})
                run.emit("step", {"n": run.steps[-1]["n"], "tool": t, "source": sources[t], "status": "failed",
                                  "ok": False, "ms": 0.0, "summary": run.failed[t]})
            return
        futures = [pool.submit(run.execute_planned, t, sources[t]) for t in ready]
        wait(futures)
        for f in futures:
            f.result()  # re-raise programming errors
        pending = [t for t in pending if t not in ready]


def run_agent(env: AgentEnv, trigger: str, raw_ctx: dict[str, Any]) -> dict[str, Any]:
    """Plan (LLM or deterministic), execute, ground, and return the full run record.

    The record contains no image; only its sha256. Persisting it is the caller's job.
    """
    t_total = time.perf_counter()
    run = _Run(env, trigger, raw_ctx)
    run.probe()
    planner = "llm" if run.live else "deterministic"
    fallback_reason = "" if run.live else run.reason
    proposed: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    stats = {"raw_invalid": 0, "corrections": 0}
    planned = run.planned
    run.emit("status", {"run_id": run.run_id, "planner": planner, "required": run.required,
                        "reason": fallback_reason})

    with ThreadPoolExecutor(max_workers=max(2, env.workers)) as pool:
        # Phase 1: the model plans while read-only tools are prefetched with trusted arguments.
        plan_future: Future[tuple[list[tuple[str, Any]] | None, str]] | None = (
            pool.submit(run.plan_call) if run.live else None)
        prefetch = [t for t in run.required if t in PREFETCH_TOOLS]
        base_source = "prefetch" if run.live else "deterministic"
        _wave(run, pool, prefetch, dict.fromkeys(prefetch, base_source))

        # Phase 2: validate the plan (every proposal, whether or not it was prefetched).
        if plan_future is not None:
            plan, why = plan_future.result()
            if plan is None:
                fallback_reason = why
            else:
                seen: set[str] = set()
                for name, args in plan:
                    proposed.append({"tool": name, "args": redact(args)})
                    if name in seen:
                        rejections.append({"tool": name, "reason": f"이미 계획한 도구: {name}"})
                        continue
                    bound, corr, reason, raw_invalid = run.check(name, args)
                    stats["raw_invalid"] += int(raw_invalid)
                    if bound is None:
                        rejections.append({"tool": name, "reason": reason})
                        continue
                    seen.add(name)
                    stats["corrections"] += len(corr)
                    planned[name] = (bound, corr)
            with run.lock:
                for rec in run.steps:
                    rec["planned_by_llm"] = rec["tool"] in planned
        missing_from_plan = [t for t in run.required if t not in planned]
        if run.live and missing_from_plan and not fallback_reason:
            fallback_reason = f"LLM 계획에 빠진 필수 도구: {missing_from_plan}"
        if run.live and fallback_reason:
            planner = "llm+fallback"
            run.note(f"결정론적 대체 실행: {fallback_reason}")

        # Phase 3: the rest of the plan, in dependency waves.
        rest = [t for t in run.required if not run.attempted(t)]
        sources = {t: ("llm" if t in planned else "deterministic_fallback" if run.live else "deterministic")
                   for t in rest}
        _wave(run, pool, rest, sources)

    first_try_valid = (planner == "llm" and not rejections and not stats["raw_invalid"]
                       and all(t in planned for t in run.required))

    # --- briefing
    final_text = run.brief_call() if planner != "deterministic" else None
    sent = any(t in run.results for t in ("send_rsu_message", "send_119_report"))
    must_mention = ("시뮬레이터",) if sent else ()
    evidence = run.snapshot_evidence()
    grounding: dict[str, Any] = {"checked": False, "ok": None, "reasons": [], "rejected_llm_text": None}
    text, source = "", "template"
    if final_text:
        grounding["checked"] = True
        reasons = check_briefing(final_text, evidence, must_mention)
        grounding["ok"] = not reasons
        grounding["reasons"] = reasons
        if not reasons:
            text, source = final_text, "llm"
        else:
            grounding["rejected_llm_text"] = final_text
    if source == "template":
        text = template_briefing(trigger, run.ctx, run.results, run.failed)
        residual = check_briefing(text, evidence, must_mention)
        if residual:
            logger.error("template briefing failed its own check: %s", residual)
        grounding["template_check"] = residual
    scene = run.results.get("describe_scene")
    if scene is not None and scene.view["description"]:
        text += f"\n{C.VLM_LABEL}: {scene.view['description']}"

    models, tc = run.models, run.tc
    return {
        "run_id": run.run_id, "trigger": trigger, "context": run.ctx, "planner": planner,
        "planner_reason": fallback_reason, "notes": run.notes,
        "plan": {"prompt_version": C.PROMPT_VERSION, "required": run.required, "proposed": proposed,
                 "rejections": rejections, "first_try_valid": first_try_valid,
                 "raw_schema_invalid": stats["raw_invalid"], "arg_corrections": stats["corrections"],
                 "llm_turns": run.llm_turns, "budget_s": env.budget_s,
                 "budget_left_s": round(run.deadline.remaining(), 2)},
        "steps": run.steps, "briefing": text, "briefing_source": source, "grounding": grounding,
        "model_name": C.PLANNER_MODEL if models and run.live else None,
        "model_digest": models.get(C.PLANNER_MODEL) if models and run.live else None,
        "vlm_name": tc.vlm_model if scene is not None else None,
        "vlm_digest": tc.vlm_digest if scene is not None else None,
        "image_sha256": tc.image_sha256, "total_ms": _ms(t_total), "created_at": run.created_at,
        "cap_feed_url": C.CAP_FEED_PATH,
        "log": [f"[{s['n']}] {s['tool']} ({s['source']}) {s['ms']} ms · {s['summary']}" for s in run.steps],
    }
