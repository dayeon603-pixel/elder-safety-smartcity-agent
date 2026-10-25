-- 0009: 실버존 결과 표에 최적화 선택 풀(OPT)과 D 풀을 허용. PRAGMA 없음: 러너가 한 트랜잭션으로 실행.
-- SQLite 는 CHECK 를 바꿀 수 없어 표를 다시 만든다. 내용은 `python -m app.modules.load` 가 다시 채운다
-- (module_run 의 silver 행을 지워 다음 적재가 '변경 없음'으로 건너뛰지 않게 한다).
DROP TABLE silver_result;
CREATE TABLE silver_result (
  variant TEXT NOT NULL CHECK (variant IN ('harm','ge2021')),
  stratum TEXT NOT NULL CHECK (stratum IN ('all','new')),
  pool TEXT NOT NULL CHECK (pool IN ('C','C0','D','OPT')),
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
DROP TABLE silver_delta;
CREATE TABLE silver_delta (
  variant TEXT NOT NULL CHECK (variant IN ('harm','ge2021')),
  stratum TEXT NOT NULL CHECK (stratum IN ('all','new')),
  pool TEXT NOT NULL CHECK (pool IN ('C','C0','D','OPT')),
  method TEXT NOT NULL,
  k INTEGER NOT NULL CHECK (k > 0),
  vs TEXT NOT NULL,                       -- 'C:random_k' 처럼 pool:method
  delta_pp REAL NOT NULL, lo_pp REAL NOT NULL, hi_pp REAL NOT NULL,
  CHECK (lo_pp <= hi_pp),
  PRIMARY KEY (variant, stratum, pool, method, k, vs)
) STRICT, WITHOUT ROWID;
DELETE FROM module_run WHERE module = 'silver';
