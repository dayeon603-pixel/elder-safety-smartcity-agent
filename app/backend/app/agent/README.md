# 영점 action agent (backend/app/agent)

Local LLM plans, the server schedules and guards, simulators receive. Nothing leaves the machine.

## Run flow (one `POST /agent/run`)

1. Probe Ollama (`/api/tags`, 2 s). No daemon / no model -> deterministic planner, labelled.
2. **One structured plan call** (`/api/chat` with `format` = JSON schema whose `tool` enum is the
   trigger's whitelist; temperature 0, seed 42). The model answers `{"steps":[{"tool","args"}]}`.
   While it thinks, read-only tools (`get_heat_region`, `get_actions`, `get_heat_plan`,
   `describe_scene`) are already prefetched with context-bound arguments (`source: "prefetch"`).
3. **Validation** of every proposed step: whitelist for the trigger, no repeats, JSON-schema
   subset, context binding (code/date/lat/lon/measurements always come from the trusted trigger
   context; the 119 bundle id is server-issued). Rejected steps are listed in `plan.rejections`.
4. **Execution in dependency waves**, each wave in parallel threads: CAP ∥ FHIR, then RSU ∥ 119.
   Tools the plan omitted run as `deterministic_fallback` and the run is labelled `llm+fallback`.
   A tool bug (KeyError etc.) is a failed step, never a crashed run. `heat_daily` makes a CAP only
   when the region is alerted or Tmax ≥ 33 ℃ (else `status: "skipped"` with the reason).
5. **Briefing** streamed from the model (`briefing_delta` events), then the numeric grounding
   check: every number must equal (after rounding; `%` needs a fraction ×100, `만` a count ×1e-4)
   a number in the tool results or the context. Coordinates, latencies and identifiers are not
   evidence. Banned: diagnoses, `None`/`nan`, conformance claims. Failing text is kept in
   `grounding.rejected_llm_text` and a template whose every line is value-guarded is used.
6. Persist (`agent_run`, immutable), then answer.

Budget: `KCF_AGENT_RUN_BUDGET_S` (40 s) is a monotonic deadline; each LLM call gets
`min(30 s, remaining)`, connect timeout 2 s. After the first LLM failure or timeout no further
LLM call is made in that run (`notes` says so). Self HTTP calls: 20 s each, loopback base URL
only (`KCF_AGENT_SELF_URL` or `http://127.0.0.1:<bound port>`; the Host header is never used).
Concurrency: `KCF_AGENT_MAX_CONCURRENT` (3) runs at once, more get `503 {"code":"busy"}` with
`Retry-After`. Rate limits: `/agent/run` 6 burst / 0.5 per s, `/sim/*` 30 burst / 2 per s.
Body caps: `/sim/*` 64 KB, `/agent/run` 6 MB (413), JSON nesting ≤ 32.

## Demo token

When `KCF_DEMO_TOKEN` (≥ 16 chars) is set, every `/agent/*` and `/sim/*` request needs the
header `X-Demo-Token: <token>` (401 otherwise; constant-time compare). The agent forwards it on
its own simulator calls. Unset = open, with a startup warning.

## Server-Sent Events (`Accept: text/event-stream`)

Same request body as the JSON call. Response `Content-Type: text/event-stream`,
`X-Accel-Buffering: no`; a `: keep-alive` comment every 5 s while idle. Events, in order:

| event | data | when |
|---|---|---|
| `status` | `{run_id, planner: "llm"\|"deterministic", required: [tool...], reason}` | once, at start |
| `note` | `{text}` | fallbacks, skipped VLM, budget exhaustion |
| `step` | `{n, tool, source, status: "ok"\|"failed"\|"skipped", ok, ms, summary}` | each tool completion (parallel steps arrive in completion order) |
| `briefing_delta` | `{text}` | each streamed token chunk of the LLM briefing (provisional; may be rejected) |
| `result` | the full run record (same JSON as the non-streaming response: `run_id, planner, steps[], briefing, briefing_source, grounding, total_ms, ...`) | once, last, after the run was persisted |
| `error` | `{detail}` | only if the run itself raised; no `result` follows |

`source` values: `llm` (validated LLM plan), `prefetch` (read tool started before the plan
arrived; the final record adds `planned_by_llm`), `deterministic`, `deterministic_fallback`.
Clients must replace any `briefing_delta` text with `result.briefing`: a briefing that fails
grounding is not the one shown.

Without the Accept header the response is the unchanged JSON record.

## Measured (10 fall runs, code 11620, 2025-07-22, qwen2.5:3b, non-reloading server, M-series laptop)

| | before (6 tool turns + briefing) | after (1 plan call ∥ prefetch, waves, streamed briefing) |
|---|---|---|
| p50 / p95 total | 16,226 / 21,908 ms | 1,949 / 3,009 ms (SSE: 2,663 / 2,802; first step at 79 ms) |
| first-try valid plan | 0/10 (model stopped after 4 tools) | 10/10 |
| grounding pass (LLM briefing kept) | 10/10 | 10/10 |

Reproduce: start the server without `--reload`, then run the benchmark script against it
(`tools`/scratch script in the report); numbers depend on the machine and on whether the model
is resident (`make demo` preloads it with `keep_alive`).

## CAP 1.2 distribution channel (nothing is sent; consumers subscribe)

`make_cap_alert` builds the alert, self-checks it, then `POST /cap/alerts` (demo token) renders CAP 1.2
XML (`cap_xml.py`), validates it against the official OASIS schema shipped in `app/agent/xsd/CAP-v1.2.xsd`
(sha256 in `xsd/README.md`) and stores it in the immutable `cap_alert` table. Only a 201 with
`xsd_valid: true` earns the step summary "OASIS CAP 1.2 XSD 검증 통과"; a schema failure is a failed step
(422 with the schema's own messages). Published documents:

| route | media type | purpose |
|---|---|---|
| `GET /cap/feed.atom?limit=` | `application/atom+xml` | Atom 1.0 feed, newest first, one `<entry>` per alert with `<link type="application/cap+xml">` (the OASIS / Google Public Alerts pattern); honours `X-Forwarded-Prefix` so links work behind nginx `/api` |
| `GET /cap/alerts/{identifier}.xml` | `application/cap+xml` | the stored CAP 1.2 XML |

Every alert keeps `status=Exercise`, `scope=Restricted`. The run record carries `cap_feed_url` and the
`make_cap_alert` result carries `xml_url` / `feed_url` for a "CAP 피드" link in the UI.

Reach without a smartphone: `GET /cap/alerts/{id}.json` adds `audio_url` (마을방송 WAV, `say -v Yuna` +
ffmpeg, <= 20 s, cached; 503 where TTS is absent) and `bit_lines` (정류장 안내단말 2 lines, chosen by the agent
from fixed templates: fall -> "횡단보도 낙상 의심 / 차량 서행 바랍니다", heat -> `actions.bit.display_message` with
체감/최고 temperature). The feed entry links the audio as `rel="enclosure"`.

## FHIR R4 bundle: official validator result

`make_fhir_bundle` still labels its output "필수 필드 자체 점검 통과" at run time (the official validator is a
Java tool, too slow per request). Offline, the generated bundle was run through the official HL7 validator
(`validator_cli.jar`, hapifhir/org.hl7.fhir.core latest release, cached at
`data/cache/extra/standards/validator_cli.jar`, `-version 4.0.1`, OpenJDK 27 via Homebrew):

- first run: 0 errors, 12 warnings (best-practice `dom-6` narrative on Location/Encounter/Observation,
  "observations should have a performer");
- fix: every resource now carries a generated narrative and an `Organization` performer
  ("영점 생활안전 에이전트 (시뮬레이션, 훈련)") referenced by each Observation;
- re-run (2026-10-24, same validator, `-version 4.0.1`): **"All OK"**: 0 errors, 0 warnings, 1 information;
  the test `tests/test_cap.py::test_official_hl7_validator_reports_no_errors` repeats it
  (runs only with `KCF_RUN_FHIR_VALIDATOR=1`; errors fail the test).

## Temperature wording

`get_heat_region` exposes `tmax_c` and `apparent_max_c` (기상청 ASOS 여름철 체감온도 daily max). The template,
the CAP description and the FHIR stage label say "체감 최고 N℃" when the apparent maximum exists and fall back
to "최고기온 N℃"; the `heat_daily` CAP gate uses the apparent maximum when present (Tmax otherwise).

## Files

`constants.py` tunables · `ollama.py` client (timeouts, `format`, streaming, warm-up) ·
`guard.py` schema subset, context binding, grounding · `tools.py` registry and executors ·
`standards.py` CAP/FHIR/DENM/TIM-shaped builders and required-field self-checks ·
`orchestrator.py` plan, waves, briefing · `store.py` persistence · `../ops.py` health and warm-up.
