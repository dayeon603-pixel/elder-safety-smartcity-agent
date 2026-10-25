-- 0003: 온열질환 경보 평가 (특보 사각지대). PRAGMA 없음: 러너가 한 트랜잭션으로 실행.
CREATE TABLE region (
  code TEXT PRIMARY KEY CHECK (length(code)=5 AND code NOT GLOB '*[^0-9]*'),
  sido TEXT NOT NULL, name TEXT NOT NULL,
  lat REAL NOT NULL CHECK (lat BETWEEN 33 AND 39),
  lon REAL NOT NULL CHECK (lon BETWEEN 124 AND 132),
  pop INTEGER NOT NULL CHECK (pop>0),
  pop65 INTEGER NOT NULL CHECK (pop65 BETWEEN 0 AND pop),
  pop_asof TEXT NOT NULL,
  UNIQUE (sido, name)
) STRICT;
CREATE TABLE region_alias (
  sido_raw TEXT NOT NULL, name_raw TEXT NOT NULL,
  region_code TEXT NOT NULL REFERENCES region(code),
  kind TEXT NOT NULL CHECK (kind IN ('exact','sido_rename','merge','split','manual')),
  PRIMARY KEY (sido_raw, name_raw)
) STRICT, WITHOUT ROWID;
CREATE INDEX idx_alias_region ON region_alias(region_code);
CREATE TABLE weather_station (
  id TEXT PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL,
  dataset_id INTEGER NOT NULL REFERENCES datasets(id)
) STRICT;
CREATE INDEX idx_station_ds ON weather_station(dataset_id);
CREATE TABLE weather_daily (
  station_id TEXT NOT NULL REFERENCES weather_station(id),
  date TEXT NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  tmax REAL CHECK (tmax BETWEEN -40 AND 50), tmin REAL, tavg REAL,
  PRIMARY KEY (station_id, date)
) STRICT, WITHOUT ROWID;
CREATE TABLE region_station (
  region_code TEXT PRIMARY KEY REFERENCES region(code),
  station_id TEXT NOT NULL REFERENCES weather_station(id),
  dist_km REAL NOT NULL CHECK (dist_km >= 0)
) STRICT;
CREATE INDEX idx_rs_station ON region_station(station_id);
CREATE TABLE heat_case_daily (
  region_code TEXT NOT NULL REFERENCES region(code),
  date TEXT NOT NULL CHECK (substr(date,6,2) BETWEEN '05' AND '09'),
  n_cases INTEGER NOT NULL CHECK (n_cases >= 0),
  n_indoor INTEGER NOT NULL CHECK (n_indoor >= 0),
  n_outdoor INTEGER NOT NULL CHECK (n_outdoor >= 0),
  CHECK (n_indoor + n_outdoor <= n_cases),
  PRIMARY KEY (region_code, date)
) STRICT, WITHOUT ROWID;
CREATE INDEX idx_case_date ON heat_case_daily(date, region_code);
CREATE TABLE heat_case_unmapped (
  sido_raw TEXT NOT NULL, name_raw TEXT NOT NULL, n_cases INTEGER NOT NULL,
  PRIMARY KEY (sido_raw, name_raw)
) STRICT, WITHOUT ROWID;
CREATE TABLE shelter (
  id INTEGER PRIMARY KEY,
  region_code TEXT REFERENCES region(code),
  name TEXT NOT NULL,
  lat REAL NOT NULL CHECK (lat BETWEEN 33 AND 39),
  lon REAL NOT NULL CHECK (lon BETWEEN 124 AND 132),
  capacity INTEGER CHECK (capacity >= 0),
  night_open INTEGER CHECK (night_open IN (0,1)),
  weekend_open INTEGER CHECK (weekend_open IN (0,1)),
  dataset_id INTEGER NOT NULL REFERENCES datasets(id),
  UNIQUE (dataset_id, name, lat, lon)
) STRICT;
CREATE INDEX idx_shelter_region ON shelter(region_code);
CREATE INDEX idx_shelter_ds ON shelter(dataset_id);
CREATE TABLE alert_run (
  id INTEGER PRIMARY KEY,
  method TEXT NOT NULL CHECK (method IN ('B1','B1S','B2','HEUR','OURS')),
  variant TEXT NOT NULL CHECK (variant IN ('F','O')),
  train_year_min INTEGER NOT NULL, train_year_max INTEGER NOT NULL,
  test_year INTEGER NOT NULL,
  budget_kind TEXT NOT NULL CHECK (budget_kind IN ('per_day_b1','per_day_b1s','season_total')),
  budget_ref_run_id INTEGER REFERENCES alert_run(id),
  params TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(params)),
  seed INTEGER NOT NULL, dataset_sha TEXT NOT NULL CHECK (length(dataset_sha)=64),
  git_hash TEXT NOT NULL, created_at TEXT NOT NULL,
  CHECK (train_year_max < test_year AND train_year_min <= train_year_max),
  UNIQUE (method, variant, test_year, budget_kind, dataset_sha, git_hash)
) STRICT;
CREATE INDEX idx_run_ref ON alert_run(budget_ref_run_id);
CREATE INDEX idx_run_year ON alert_run(test_year, variant);
CREATE TABLE alert (
  run_id INTEGER NOT NULL REFERENCES alert_run(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  region_code TEXT NOT NULL REFERENCES region(code),
  score REAL NOT NULL, alerted INTEGER NOT NULL CHECK (alerted IN (0,1)),
  PRIMARY KEY (run_id, date, region_code)
) STRICT, WITHOUT ROWID;
CREATE INDEX idx_alert_region ON alert(run_id, region_code, date) WHERE alerted = 1;
CREATE INDEX idx_alert_region_fk ON alert(region_code);
CREATE TRIGGER alert_immutable BEFORE UPDATE ON alert
BEGIN SELECT RAISE(ABORT,'alert rows are immutable; create a new run'); END;
CREATE TABLE eval_metric (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES alert_run(id) ON DELETE CASCADE,
  metric TEXT NOT NULL CHECK (metric IN ('capture','precision','auc','prauc','n_alerts','capture_curve')),
  x REAL,
  baseline_run_id INTEGER REFERENCES alert_run(id),
  value REAL NOT NULL,
  ci_lo REAL, ci_hi REAL, n_boot INTEGER, block_days INTEGER,
  CHECK (ci_lo IS NULL OR (ci_lo <= value AND value <= ci_hi))
) STRICT;
CREATE UNIQUE INDEX uq_metric ON eval_metric(run_id, metric, COALESCE(x,-1), COALESCE(baseline_run_id,0));
CREATE INDEX idx_metric_base ON eval_metric(baseline_run_id);
