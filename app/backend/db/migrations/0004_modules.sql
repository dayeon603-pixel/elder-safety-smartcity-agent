-- 0004: 실버존(보행) 및 AED 밤 공백 모듈 산출물. PRAGMA 없음: 러너가 한 트랜잭션으로 실행.
-- 값은 experiments/{silver,aed}/out 의 CSV/JSON을 그대로 옮긴 것이고 계산은 하지 않는다.
-- 출처 파일의 sha256은 datasets 와 module_run 에 함께 남는다.

CREATE TABLE module_run (
  id INTEGER PRIMARY KEY,
  module TEXT NOT NULL CHECK (module IN ('silver','aed')),
  source_file TEXT NOT NULL CHECK (length(source_file) BETWEEN 1 AND 128),
  sha256 TEXT NOT NULL CHECK (length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
  protocol_sha TEXT CHECK (protocol_sha IS NULL OR (length(protocol_sha)=64 AND protocol_sha NOT GLOB '*[^0-9a-f]*')),
  dataset_id INTEGER REFERENCES datasets(id) ON DELETE SET NULL,
  n_rows INTEGER NOT NULL CHECK (n_rows >= 0),
  loaded INTEGER NOT NULL CHECK (loaded IN (0,1)),   -- 0 = 출처 등록만, 표로 옮기지 않음
  note TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(note)),
  created_at TEXT NOT NULL,
  UNIQUE (module, source_file)
) STRICT;
CREATE INDEX idx_module_run_ds ON module_run(dataset_id);

-- 포착률: 사고 다발지 중 300m 안에 구역이 있는 비율. variant/pool/method 값은 실험 코드 그대로.
CREATE TABLE silver_result (
  variant TEXT NOT NULL CHECK (variant IN ('harm','ge2021')),
  stratum TEXT NOT NULL CHECK (stratum IN ('all','new')),
  pool TEXT NOT NULL CHECK (pool IN ('C','C0')),
  method TEXT NOT NULL CHECK (length(method) BETWEEN 1 AND 32),
  k INTEGER NOT NULL CHECK (k > 0),
  coverage REAL NOT NULL CHECK (coverage BETWEEN 0 AND 1),
  ci_lo REAL NOT NULL CHECK (ci_lo BETWEEN 0 AND 1),
  ci_hi REAL NOT NULL CHECK (ci_hi BETWEEN 0 AND 1),
  n_hot INTEGER NOT NULL CHECK (n_hot >= 0),
  casualties REAL NOT NULL CHECK (casualties >= 0),
  CHECK (ci_lo <= ci_hi),
  PRIMARY KEY (variant, stratum, pool, method, k)
) STRICT, WITHOUT ROWID;

-- 방법 간 포착률 차이(%p), 같은 부트스트랩 표본 위의 쌍체 구간.
CREATE TABLE silver_delta (
  variant TEXT NOT NULL CHECK (variant IN ('harm','ge2021')),
  stratum TEXT NOT NULL CHECK (stratum IN ('all','new')),
  pool TEXT NOT NULL CHECK (pool IN ('C','C0')),
  method TEXT NOT NULL,
  k INTEGER NOT NULL CHECK (k > 0),
  vs TEXT NOT NULL,                       -- 'C:random_k' 처럼 pool:method
  delta_pp REAL NOT NULL, lo_pp REAL NOT NULL, hi_pp REAL NOT NULL,
  CHECK (lo_pp <= hi_pp),
  PRIMARY KEY (variant, stratum, pool, method, k, vs)
) STRICT, WITHOUT ROWID;

-- 지도 점. cur_within_300m / ours_within_300m = 이 점이 현행 구역 / 영점 후보 300m 안에 있는지(원본 열 그대로).
-- sgg_code 는 적재 시점의 행정경계로 구한다: 'polygon' = 경계 안, 'centroid' = 가장 가까운 시군구 중심, NULL = 못 구함.
CREATE TABLE silver_site (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('current','ours')),
  lat REAL NOT NULL CHECK (lat BETWEEN 33 AND 39),
  lon REAL NOT NULL CHECK (lon BETWEEN 124 AND 132),
  sido TEXT,
  name TEXT,
  sgg_code TEXT REFERENCES region(code),
  sgg_method TEXT CHECK (sgg_method IN ('polygon','centroid')),
  cur_within_300m INTEGER NOT NULL CHECK (cur_within_300m IN (0,1)),
  ours_within_300m INTEGER NOT NULL CHECK (ours_within_300m IN (0,1)),
  CHECK ((sgg_code IS NULL) = (sgg_method IS NULL))
) STRICT;
CREATE INDEX idx_silver_site_sgg ON silver_site(sgg_code, kind);

CREATE TABLE silver_hotspot_2024 (
  id INTEGER PRIMARY KEY,
  lat REAL NOT NULL CHECK (lat BETWEEN 33 AND 39),
  lon REAL NOT NULL CHECK (lon BETWEEN 124 AND 132),
  casualties REAL NOT NULL CHECK (casualties >= 0),
  sgg_code TEXT REFERENCES region(code),
  sgg_method TEXT CHECK (sgg_method IN ('polygon','centroid')),
  covered_current INTEGER NOT NULL CHECK (covered_current IN (0,1)),
  covered_ours INTEGER NOT NULL CHECK (covered_ours IN (0,1)),
  CHECK ((sgg_code IS NULL) = (sgg_method IS NULL))
) STRICT;
CREATE INDEX idx_silver_hot_sgg ON silver_hotspot_2024(sgg_code);

-- 공개 장소 심정지의 시간대별 건수와 AED 열림 비율(0~1). 주말은 원본 보고대로(W1)와 자리표시 패턴을 평일로 본 경우(W2).
CREATE TABLE aed_hourly_gap (
  hour INTEGER PRIMARY KEY CHECK (hour BETWEEN 0 AND 23),
  public_arrests INTEGER NOT NULL CHECK (public_arrests >= 0),
  open_share_weekday REAL NOT NULL CHECK (open_share_weekday BETWEEN 0 AND 1),
  open_share_weekend REAL NOT NULL CHECK (open_share_weekend BETWEEN 0 AND 1),
  open_share_weekend_w2 REAL NOT NULL CHECK (open_share_weekend_w2 BETWEEN 0 AND 1)
) STRICT;

-- 서울 25개 구. name = 원본의 구 이름, sgg_code = region 표에서 이름으로 맞춘 5자리 코드(못 맞추면 NULL).
-- source_code 는 원본 파일의 코드(통계청 체계)라 region.code 와 다르다.
CREATE TABLE aed_gu_gap (
  gu TEXT PRIMARY KEY CHECK (length(gu) BETWEEN 1 AND 16),
  sgg_code TEXT UNIQUE REFERENCES region(code),
  source_code TEXT NOT NULL,
  n_aed INTEGER NOT NULL CHECK (n_aed >= 0),
  n_aed_24h_weekday INTEGER NOT NULL CHECK (n_aed_24h_weekday BETWEEN 0 AND n_aed),
  night_open_share REAL NOT NULL CHECK (night_open_share BETWEEN 0 AND 1),
  day_open_share REAL NOT NULL CHECK (day_open_share BETWEEN 0 AND 1),
  gap_score REAL NOT NULL,
  public_arrests_night REAL NOT NULL CHECK (public_arrests_night >= 0)  -- 연간 추정치
) STRICT;

-- 24시간 전환 후보. 시설명(org)은 기관명이고 관리자 이름과 전화번호는 적재하지 않는다.
CREATE TABLE aed_candidate (
  list_kind TEXT NOT NULL CHECK (list_kind IN ('top5_per_gu','budget_5pct')),
  serial_seq TEXT NOT NULL CHECK (length(serial_seq) BETWEEN 1 AND 32),
  gu TEXT NOT NULL REFERENCES aed_gu_gap(gu),
  rank_in_gu INTEGER NOT NULL CHECK (rank_in_gu >= 1),
  org TEXT NOT NULL,
  address TEXT NOT NULL,
  place TEXT NOT NULL,
  weekday_hours TEXT,                      -- 'HHMM-HHMM', 원본이 비어 있으면 NULL
  night_open_weekday_share REAL NOT NULL CHECK (night_open_weekday_share BETWEEN 0 AND 1),
  gain_score REAL NOT NULL,
  lat REAL NOT NULL CHECK (lat BETWEEN 33 AND 39),
  lon REAL NOT NULL CHECK (lon BETWEEN 124 AND 132),
  PRIMARY KEY (list_kind, serial_seq)
) STRICT, WITHOUT ROWID;
CREATE INDEX idx_aed_cand_gu ON aed_candidate(list_kind, gu, rank_in_gu);
CREATE INDEX idx_aed_cand_gu_fk ON aed_candidate(gu);

-- summary.json 의 요약 통계 한 줄씩. name 은 JSON 경로.
CREATE TABLE aed_stat (
  name TEXT PRIMARY KEY CHECK (length(name) BETWEEN 1 AND 96),
  value REAL NOT NULL,
  ci_lo REAL, ci_hi REAL, n INTEGER,
  CHECK ((ci_lo IS NULL) = (ci_hi IS NULL)),
  CHECK (ci_lo IS NULL OR ci_lo <= ci_hi)
) STRICT, WITHOUT ROWID;
