-- 0013: 교통약자 이동(mobility) 모듈: 추천 정류장 상위 목록 (experiments/mobility/out/recommended_top2000.csv).
-- PRAGMA 없음: 러너가 한 트랜잭션으로 실행. 적재기는 파일 sha256 이 바뀔 때만 표를 통째로 다시 채운다 (한 트랜잭션).
-- 값은 실험 산출물을 그대로 복사한다 (계산 없음). sgg_code 는 적재 시점의 region (sido, name) 으로 맞춘다; 못 맞추면 NULL.
CREATE TABLE mobility_stop (
  stop_id TEXT PRIMARY KEY CHECK (length(stop_id) BETWEEN 1 AND 32),
  name TEXT NOT NULL,
  lat REAL NOT NULL CHECK (lat BETWEEN 33 AND 39),
  lon REAL NOT NULL CHECK (lon BETWEEN 124 AND 132),
  sido TEXT NOT NULL,
  sgg TEXT NOT NULL,
  dong TEXT,
  sgg_code TEXT REFERENCES region(code),
  score REAL NOT NULL CHECK (score BETWEEN 0 AND 100),
  p65_within_walk REAL NOT NULL CHECK (p65_within_walk >= 0),
  greedy_rank INTEGER NOT NULL CHECK (greedy_rank >= 1),
  recommended INTEGER NOT NULL CHECK (recommended IN (0,1)),
  marginal_gain REAL,
  dataset_id INTEGER NOT NULL REFERENCES datasets(id)
) STRICT;
CREATE INDEX idx_mobility_sido_sgg ON mobility_stop(sido, sgg, greedy_rank);
CREATE INDEX idx_mobility_sgg_code ON mobility_stop(sgg_code);
CREATE INDEX idx_mobility_ds ON mobility_stop(dataset_id);
