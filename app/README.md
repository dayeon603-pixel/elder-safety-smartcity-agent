# KCF starter

Topic-agnostic hackathon scaffold: FastAPI + plain sqlite3, experiment harness (baseline vs ours with bootstrap CIs), ICASSP-style figures, React/Tailwind UI. Runs offline once installed.

## Run

```bash
make install     # uv sync + npm ci (needs network once)
make env         # creates .env with a random KCF_SECRET_KEY
make exp         # worked example (UCI WDBC via sklearn) -> runs table + results JSON
make dev         # backend :8000, frontend :5190 (BACKEND_PORT=8010 make dev if 8000 is busy)
make test        # pytest
make figs        # figures/out/*.png|pdf from experiments/results/wdbc_smoke.json
make demo        # production-like: preflight self-test (migrations, data, Ollama warm-up) -> uvicorn (no reload, 1 worker) + vite preview
make demo-check  # the self-test alone; exit 1 = fix before the demo. DEMO_VLM=1 also preloads the VLM
make docker      # backend + nginx frontend on http://localhost:8190 (API under /api); run make docker-seed-db once first
make docker-smoke  # build, up -d, probe /api/health/deep and the page, down
make reset-db
make seed-admin EMAIL=you@example.com   # admins are never created by registration order
make heat-ingest   # idea A: regions, weather (Meteostat), asos (기상청 ASOS 95 + 체감 최고), cases, shelters -> SQLite (idempotent)
make heat-eval     # idea A: panel -> ingest -> evaluate -> figures -> persist runs/alerts/metrics (409 from /heat/* until done)
```

### Heat endpoints (read-only, `/heat/*`)

`meta`, `eval?variant=F`, `curve?year=&variant=`, `days?year=&sort=gap|date`, `regions` (ETag), `alerts?date=&variant=`, `region/{code}?date=`.
404 unknown region, 422 date outside 05-20..09-25 or year without a test run, 409 `no_completed_run` (run `make heat-eval`).
`region/{code}` drivers: `tmax`, `apparent_max` (기상청 ASOS 여름철 체감온도 daily max, KST 10-19h, the quantity the
evaluation and 폭염특보 use) and `station` (nearest ASOS station, 95 in total; `python -m app.heat.heat_all --only asos`
re-maps `region_station`). `/actions` and the agent say "체감 최고 N℃" whenever `apparent_max` exists.

### Silver-zone source (`KCF_SILVER_SOURCE`)

`/silver/*` serves the promoted optimisation run by default (`backend/experiments/silver/out/opt/promoted/`,
method `ours_opt`, pool `OPT`; NEW stratum n_hot = 73); the baselines (`current`, `pop65_k`, `past_k`) are the
pool-C rows of the same file. `KCF_SILVER_SOURCE=v1` falls back to `out/*.csv` (`ours_full`, pool C). Both source
sets are registered in `datasets` with their sha256 (`silver_promoted_*` vs `silver_*`). Reload: `make modules-load`
(migration 0009 widened the pool CHECK and clears the silver `module_run` rows so the next load is not skipped).

### CAP 1.2 feed (`/cap/*`)

The agent publishes every CAP alert to an Atom feed instead of sending it anywhere: `GET /cap/feed.atom`
(Atom 1.0, `application/atom+xml`) and `GET /cap/alerts/{id}.xml` (`application/cap+xml`). Each alert was validated
against the official OASIS CAP 1.2 XSD (`backend/app/agent/xsd/`) before storage; `POST /cap/alerts` (demo token)
is the agent's own publish call. Details and the HL7 FHIR validator results: `backend/app/agent/README.md`.

### 기존 행정 시스템 연동 (webhook + 공문 초안)

- Approved actions (`POST /feedback` with `decision: 채택`) are delivered to `KCF_WEBHOOK_URL` as JSON signed with
  HMAC-SHA256 (`X-Zero-Point-Signature: sha256=…` over `"<timestamp>.<body>"`, `X-Zero-Point-Timestamp`,
  `X-Zero-Point-Event: action.approved`), retried on transport/5xx errors (3 attempts, 0.5/1/2 s backoff), and the
  outcome is appended to the HMAC audit chain (`actor=webhook`). `POST /sim/webhook` is a local receiver double that
  verifies the signature (401 otherwise) and stores the event in `sim_inbox` (channel `webhook`). Nothing leaves the
  machine unless you point the URL elsewhere. Required pair: `KCF_WEBHOOK_URL` + `KCF_WEBHOOK_SECRET` (>= 16 chars).
- `GET /documents/{action_id}.docx?sgg_code=&date=[&teams=]` (demo token) renders a 공문 초안 (신호 연장 협조 요청,
  실버존 지정 검토 요청, 재난문자 송출 요청, BIT 표출 요청, 방문 점검 협조, 경보 대상 추가 검토, 방문팀 배치, AED 24시간 전환)
  as DOCX from templates in `backend/app/documents/templates.py`, filled with the recommendation's `evidence` numbers and
  sources; generated locally with the standard library only. Every draft says it has no effect before review.
- Next step (not implemented, not claimed): 스마트도시 통합플랫폼 연계(TTA 연계규격). The webhook is a plain signed
  JSON push, not that specification.

### 스마트폰 없는 어르신 도달 (CAP → 마을방송 + 정류장 안내단말)

Each published CAP alert exposes `GET /cap/alerts/{id}.json` with `audio_url` (`/cap/alerts/{id}/audio.wav`: a <= 20 s
plain-Korean 마을방송 script read by macOS `say -v Yuna`, converted with ffmpeg to 16 kHz mono WAV, cached under
`KCF_AUDIO_DIR`; 503 `tts_unavailable` where `say`/ffmpeg are absent, e.g. inside Docker) and `bit_lines` (the
버스정류장 안내단말 2-line text, <= 15 chars per line). The Atom feed carries the audio as an `enclosure` link.

### Ops-map layers and the parsed-source cache

- First `/actions` call used to parse the 35 MB 행정동 GeoJSON twice plus two large CSVs (58 s cold). The parsed
  tables (행정동 rings, bus stops, crosswalks, AED points) are now pickled under `KCF_ACTIONS_CACHE_DIR`
  (backend/var/actions_cache), keyed by source size/mtime and a code version. `make demo-check` and `KCF_WARMUP=true`
  build them (≈28 s once); afterwards a cold process answers `/actions` in ≈0.5 s, a warm one in ≈0.1–0.4 s.
- `GET /crosswalks?sgg_code=&speed=` (crosswalks inside the region's 행정동: lat/lon, length, green, address,
  `need_s` and `deficit_s` at `speed` m/s, default 0.7), `GET /aed?sgg_code=&day=weekday|sat|sun|holiday&hour=`
  (all AEDs of the 시도/구군 from the 전국 AED API cache with posted hours and `open_now`; rules in the response;
  manager names/phones are never loaded), `GET /shelters?sgg_code=&at=` (무더위쉼터 with flags and `open_now`; the
  rules are returned verbatim: weekday daytime assumed open, night needs `night_open`, weekend/holiday needs
  `weekend_open`), `GET /integrations/status` (webhook configured? trigger = `POST /feedback` with 채택).
- `/briefing` actions carry `action_id` + `evidence`; BIT stops and AED candidates carry `lat`/`lon`.

### 교통약자 이동 모듈 (`/mobility/stops`)

`GET /mobility/stops?gu=관악구|&sgg_code=11620|&limit=` serves the Seoul subset (823 of 2,000) of
`backend/experiments/mobility/out/recommended_top2000.csv` from the `mobility_stop` table (migration 0013;
`python -m app.modules.mobility`, also run by `make modules-load`; reloaded only when the file's sha256 changes).
Items: `stop_id, name, lat, lon, sgg, sgg_code, dong, score, p65_within_walk, greedy_rank, recommended, marginal_gain`,
best `greedy_rank` first; `definitions` and `sources` (sha256) in the response; 409 `no_module_data` before the load.
The `geo` dependency group (osmnx, networkx, shapely, scipy, pyarrow) serves experiments only and is excluded from the
API image.

### 응급·약국·병의원 (`/medical/*`, 국립중앙의료원 E-Gen)

`GET /medical/er?sgg_code=[&lat=&lon=]` (응급의료기관 + 실시간 가용 병상 `hvec`, 마지막 갱신 `beds_updated_at`, nearest
first), `GET /medical/open-now?sgg_code=&at=YYYY-MM-DDTHH:MM[&kind=pharmacy|hospital|all]` (지금 열린 약국·병의원,
요일별 `dutyTime1..8`, 8 = 공휴일, 자정 넘김 처리, `is_night`; hospital hours are 외래 진료시간, so `er_hospitals`
counts 응급실 운영 기관 separately), `GET /medical/status`. Fetches are cached per 시군구 (beds
5 min, directories 24 h) in `KCF_MEDICAL_CACHE_DIR` (backend/var/medical) with timestamp + sha256, counted against a
daily quota (`KCF_MEDICAL_DAILY_QUOTA` 900), and fall back to the newest snapshot (`fetched.*.stale`, `age_s`) or the
read-only seed in `data/cache/extra/nmc_snapshots` (`python -m app.medical.snapshot 11620 ...`). Key:
`KCF_DATA_GO_KR_KEY` (the key, or `keychain` to read the macOS keychain item `DATA_GO_KR_KEY`); unset = snapshots only.
No record -> `note: 자료 없음`; nothing at all -> 503 `medical_unavailable`. Holidays: fixed dates + an explicit
2024–2026 table in `backend/app/medical/hours.py` (verify yearly). `/briefing` and `/actions` carry `medical_night`
evidence (22:30 open pharmacies per 65세 이상 1만 명, 응급실 가용 병상) and a `medical.gap` action; the agent's
`get_medical` tool (fall runs) reports the nearest ER with beds and, at night, open pharmacies.

### Small-count honesty (EDGE_CASES.md follow-ups)

- AED 밤 공백 rank: `gap_rank` stays as the point rank, but `/briefing` (`aed.gap_rank_uncertainty`, `gap_rank_text`) and
  `/aed/gu` (`rank_interval`, `top5_probability`, `rank_text`) add a Poisson-bootstrap (events/yr resampled, seed 42,
  4,000 draws) 95% rank interval and the probability of being in the top 5; texts say "상위 5구 안 (확률 p%, 순위 구간
  lo~hi위)" instead of "N위".
- Heat region rank (`/briefing` `heat.rank_text`, CBS head, `evidence.rank_text`): exact "N위" only when the region has
  ≥ 50 historical cases, a quintile band ("상위 40% 안") at 20–49, "순위 자료 부족" below 20 (then omitted from the CBS).
- λ at 체감 최고 ≥ 36 ℃ is labelled a lower bound ("예상 환자 N명 이상(하한)") in plan reasons, briefing evidence
  (`expected_cases_is_lower_bound`), `/actions` evidence and 공문 초안.
- CBS `MessageTooLongError` → 422 `cbs_too_long` on `/actions` and `/documents` (was 500).
- Holiday table coverage: `medical/hours.py` lists 설·추석·대체공휴일 through `HOLIDAY_TABLE_END_YEAR` (2026); a date outside
  it logs a warning once per year, `/health/deep` carries `holiday_table` (status degraded when today is uncovered), and
  `/medical/open-now`, `/shelters` return `holiday_table_covered`.

### Feedback loop (`/feedback`)

`POST /feedback {run_id | sgg_code+date, action_id, decision: 채택|보류|부적합, note<=200, actual_outcome?}` stores a
reviewer's decision in an append-only table (same demo token, 64 KB cap, `/sim`-class rate limit);
`GET /feedback/summary?sgg_code=` counts by action type and decision. `action_id`s: `/briefing` actions carry
`heat.alert | heat.teams | heat.shelters | silver.check | aed.gap | aed.convert`; `/actions` exposes `cbs | bit | signals | route`.
Every heat recommendation in both responses carries the same `evidence` dict (expected cases λ, 65세 이상, 체감 최고 /
최고기온 + station, rank, dataset sha256). Feedback is logged for the next training cycle; it is not used for retraining yet.
Schema: `db/migrations/0003_heat.sql`; code: `app/heat/` (ingest + persist), `app/repo_heat.py`, `app/routers/heat.py`.

## Swap in the topic

1. Put CSVs in `../data/cache`, then `POST /datasets/ingest` (auth required) with source URL, license and column mapping. SHA-256 is recorded.
2. `experiments/run_experiment.load_from_db(...)` returns X, y, times; write two `Method`s; call `run_experiment(...)` (`temporal=True` for time-ordered data).
3. Specialise `records` key columns via a new `db/migrations/0002_*.sql`; add the domain flow in `frontend/src/pages/DomainPage.tsx`.

### Health and demo infrastructure

- `GET /health` liveness; `GET /health/deep` = DB (WAL, busy_timeout), migrations at head, datasets
  (regions, heat test years, silver/AED loaded), Ollama (reachable, planner installed and resident,
  VLM), last agent run. 200 `ok|degraded`, 503 `fail`. `KCF_REQUIRE_OLLAMA=true` makes a missing
  model a failure.
- Agent: single structured plan call, parallel tool waves, SSE streaming, run budget, demo token,
  loopback self-calls: `backend/app/agent/README.md`. Env knobs: `.env.example`.
- Docker: the backend reaches the host's Ollama via `host.docker.internal:11434`
  (`extra_hosts: host-gateway` for Linux; override with `KCF_OLLAMA_URL`). nginx proxies `/api/`
  without buffering so `/agent/run` streams. The DB volume is seeded from `backend/var/kcf.sqlite3`
  by `make docker-seed-db`; the data cache is mounted read-only.

Notes:
- `POST /auth/register` returns 202 with no token (same reply whether or not the e-mail exists); sign in with `/auth/login` afterwards.
- Ingest stores column roles (id/time/label/group/features). `load_from_db` returns `LoadedData` and never uses the label as a feature.
- Rate limiting behind docker: nginx sets `X-Forwarded-For` to `$remote_addr`; the backend keys on the rightmost entry. Under colima/Docker Desktop port forwarding every client may arrive from the VM gateway address and then share one bucket.
- `make figs` renders the newest `experiments/results/*.json`.

Security algorithms for the slide: `backend/app/security/ALGORITHMS.md`.

## Structure

```
app/
  Makefile  docker-compose.yml  .env.example
  backend/
    pyproject.toml  uv.lock  Dockerfile
    db/            schema.sql (v1), migrations/
    app/           main, config, db (migration runner), repo (SQL), schemas, deps
      routers/     meta, auth, datasets, runs, audit, heat
      heat/        idea A ingest_* + persist + heat_all CLI (python -m app.heat.heat_all)
      security/    passwords, tokens, ratelimit, headers, audit, ALGORITHMS.md
    experiments/   run_experiment.py, example_breast_cancer.py, results/
    figures/       icassp.mplstyle, plot_helpers.py, make_figures.py, out/
    tests/
  frontend/        Vite + React 18 + TS + Tailwind 3, Pretendard (local), recharts
    src/{api,components,pages}  nginx.conf  Dockerfile
```
