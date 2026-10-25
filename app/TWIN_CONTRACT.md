# 영점 디지털 트윈 데모 · 공동 계약서 (2026-10-24 17:45, Dayeon 승인)

백엔드 상태: (19:40; + `GET /twin/11620/population?date=&hour=` (서울 생활인구 실측, 21동, walk_nodes per 동; `GET /twin/11620/walk-nodes`), `GET /twin/11620/actions/{id}/document` (문안 preview), `GET /twin/11620/fall-summary?lat=&lon=&date=&hour=` (119 정보 preview), actions now carry `destination`/`status`/`drafts`(90·157자)/`display_source`; /feedback `stage` approve_demo|hold, summary `by_date`; SSE keep-alive bug fixed) ALL 6 live on :8010 · 1 scene `GET /twin/11620/scene` = 16,968 footprints (gwanak_buildings.parquet: 층수 실측 7.7%, 나머지 면적 10분위 추정; `buildings.height_source_counts`/`measured_pct`/`imputation.bands` in payload & `?meta=1`), gzip 1.08 MB, 2nd call <10 ms; positions = metres from `center` (deck.gl METER_OFFSETS, coordinateOrigin=center); OSM fallback + 국토부 SHP(zip) auto-preferred when dropped under extra/buildings/ (sha256 key → rebuild ~5 s) · 2 state `GET /twin/11620/state?date=&hour=0..24&daytype=` (+ `silver_eval_seoul`, `aed_before_after`) · 3 timeline · 4 route · 5 terminal `GET /terminal/stream?stop_id=` + `POST /terminal/publish` (X-Demo-Token; NOTE the :8010 process was started before KCF_DEMO_TOKEN existed in .env so it currently accepts no-token POSTs) + `GET /terminal/last` + `POST /twin/11620/publish-heat?date=&hour=` + auto-publish after /agent/run · 6 rain-days (2025 hourly: 7 verified nights; 2023–24 daily-only candidates) · extras: `GET /twin/11620/actions?date=&hour=`, `POST /twin/11620/actions/{id}/cap` (xsd_valid, generation_ms), `GET /twin/11620/cameras`, `GET /heat/verify` (= `make verify-heat`, 0.7 s, 일치), /feedback 채택 → `audit {id, hash}` · 관악구 무더위쉼터 공개자료 없음 (shelters=[]).

목표: 심사위원이 "진짜 돌아가는 하나의 시스템"으로 보게 하는 메인 데모.
**관제 화면(노트북, 3D 관악구) + 현장 단말(휴대폰/태블릿) + 라이브 낙상(웹캠)** 이 하나의 흐름으로 이어진다.

## 절대 규칙
- **3D 단순화 금지 (Dayeon 지시).** 관악구 건물 전부, 실제 footprint 그대로, 실제 층수 높이. 성능 문제는 엔지니어링(바이너리 속성, 사전 계산, 워커, 인스턴싱)으로 해결한다. 건물 생략·박스화·LOD로 덜 그리기 금지.
- 한 화면, **스크롤 없음** (1920×1080 기준, 1440×900에서도 스크롤 없음). 글자는 크게, 읽을 문장 최소.
- 실제 데이터만. 날짜·장소는 실제(2025-07-22 관악구 등). 상태는 전부 API에서 온다; 프론트에 숫자 상수 금지.
- 인터넷 없이 동작 (배경 지도 타일 사용 금지; 우리 데이터로만 그린다). 라이브러리는 npm 설치 후 번들.
- 발송 없음: 재난문자·119·RSU는 "훈련 · 발송 안 함" 표시, 시뮬레이터 202만.
- 비 시나리오는 실제로 비가 온 날(ASOS 관측)로만. 가짜 날씨 금지.

## 백엔드 (인프라 담당 Fable) — 모두 GET은 캐시 가능, 응답에 sources/fetched_at 포함
1. `GET /twin/{sgg_code}/scene` → 정적 장면 묶음 (관악구 11620 우선, 다른 서울 구도 동작)
   - `buildings`: 전부. deck.gl용 **바이너리 친화** 형식 (flat 좌표 Float32 + startIndices + height). 높이 = levels×3.2m (없으면 OSM 같은 태그 중앙값, 출처 표시). 구 경계 밖 건물 제외 기준 = 대표점 포함.
   - `roads`: 보행망 간선 (경로 그리기용) 
   - `boundary`, `center`, `bbox`
   - 사전 계산 후 파일 캐시(sha256), 두 번째 호출 < 300 ms.
2. `GET /twin/{sgg_code}/state?date=YYYY-MM-DD&hour=H&daytype=weekday|sat|sun|holiday` → 그 시각의 동적 상태
   - `aed[]` {id,lat,lon,name,open}, `shelters[]` {open}, `crosswalks[]` {green_s, need_s@0.7, short}, `silver_current[]`/`silver_ours[]`, `stops_recommended[]`, `heat` {체감, b1_alert, ours_alert, rank_text, expected_cases_text}, `weather` {temp, feels, rain_mm} (실측), `summary` {aed_open_pct, …}
3. `GET /twin/{sgg_code}/timeline?date=` → 06~24시 시간별 요약 (재생용, 한 번에)
4. `GET /twin/route?lat=&lon=&date=&hour=&daytype=` → 보행망 최단경로로 가장 가까운 **열린** AED: {aed, path:[[lon,lat],…], meters, seconds@0.8m/s}
5. 단말 채널: `GET /terminal/stream?stop_id=` (SSE) + `POST /terminal/publish` (X-Demo-Token) {kind: heat|fall|rain|clear, title, body, audio_url?, stop_id?}. 에이전트 실행(/agent/run)과 폭염 판단 시 자동 publish도 지원. 마지막 메시지 재전송(재접속 대비).
6. 실제 비 온 날 후보: 관악 인근 ASOS에서 2023–2025 여름 밤 강수 ≥ 10mm/h 날짜 목록 `GET /twin/rain-days`.

## 프론트 A — 관제 화면 `/twin` (3D)
- deck.gl (+ @deck.gl/react) 기울인 시점, 조명/그림자, 시간에 따라 하늘·조명 변화(낮→밤). 
- 레이어: 건물(전부), 폭염 위험(구역 색), 실버존(현행 회색 / 영점 파랑), 횡단보도(신호 부족 빨강), AED·쉼터 기둥(열림 초록 / 닫힘 회색, 시간에 따라 꺼짐 애니메이션), 추천 정류장, 낙상 펄스, 열린 AED까지 경로 애니메이션.
- 하단 시나리오 바: [▶ 2025-07-22 하루 재생] [밤 23시] [비 오는 밤(실측일)] [낙상 발생] + 시간 슬라이더 06~24.
- 우측 레일: 에이전트 기록(SSE 단계별 실측 ms), 결과 카드 3장(특보 vs 영점, 열린 AED %, 조치), 재난문자 초안(N/157자).
- 좌하단 인셋: 웹캠 + 스켈레톤 (기존 /field 파이프라인 재사용) — 낙상 감지 시 시나리오 3 자동 시작.
- 키보드 단축키로 발표 진행(1~4, 스페이스).

## 프론트 B — 현장 단말 `/terminal?stop=` 
- 정류장 안내단말처럼 보이는 전체화면(가로/세로 모두), 아주 큰 글씨, 어르신용. 
- SSE 수신 → 화면 전환 + 마을방송 음성 재생(백엔드 WAV) + 진동/플래시. 대기 화면: 현재 체감온도·시각·가까운 열린 AED·쉼터.
- 휴대폰에서 LAN으로 접속 (`tools/demo_tools.sh lan`), 오디오 자동재생 제약 → 첫 화면 "시작" 탭 1회.
- 노트북 두 번째 창 대체 모드.

## 일정
- 21:00 1차 통합 (Dayeon 확인): /twin 3D 장면 + 시간 재생 + 단말 수신.
- 23:00 낙상 라이브 연동 + 경로 + 비 시나리오.
- 01:00 기능 동결, 백업 녹화.
