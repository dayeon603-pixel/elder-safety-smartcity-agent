-- 0006: 영점 행동 에이전트. PRAGMA 없음: 러너가 한 트랜잭션으로 실행.
-- sim_inbox: 로컬 시뮬레이터(RSU, 119)가 실제 HTTP로 받은 메시지. 수정/삭제 불가.
-- agent_run: 에이전트 실행 기록(계획, 단계별 측정 지연 ms, 모델 이름+digest). 수정/삭제 불가.
-- 이미지는 저장하지 않는다. image_sha256 (감사용 해시)만 남긴다.

CREATE TABLE sim_inbox (
  id INTEGER PRIMARY KEY,
  receipt_id TEXT NOT NULL UNIQUE CHECK (length(receipt_id) BETWEEN 8 AND 64),
  channel TEXT NOT NULL CHECK (channel IN ('rsu','119')),
  payload TEXT NOT NULL CHECK (json_valid(payload)),
  payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256)=64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
  received_at TEXT NOT NULL
) STRICT;
CREATE INDEX idx_sim_inbox_channel ON sim_inbox(channel, id);
CREATE TRIGGER sim_inbox_no_update BEFORE UPDATE ON sim_inbox
BEGIN SELECT RAISE(ABORT,'sim_inbox rows are immutable'); END;
CREATE TRIGGER sim_inbox_no_delete BEFORE DELETE ON sim_inbox
BEGIN SELECT RAISE(ABORT,'sim_inbox rows are immutable'); END;

CREATE TABLE agent_run (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE CHECK (length(run_id) BETWEEN 8 AND 64),
  trigger TEXT NOT NULL CHECK (trigger IN ('fall','heat_daily')),
  context TEXT NOT NULL CHECK (json_valid(context)),        -- 이미지 제외
  planner TEXT NOT NULL CHECK (planner IN ('llm','llm+fallback','deterministic')),
  plan TEXT NOT NULL CHECK (json_valid(plan)),
  steps TEXT NOT NULL CHECK (json_valid(steps)),            -- 단계별 측정 ms 포함
  briefing TEXT NOT NULL,
  briefing_source TEXT NOT NULL CHECK (briefing_source IN ('llm','template')),
  grounding TEXT NOT NULL CHECK (json_valid(grounding)),
  model_name TEXT,
  model_digest TEXT,
  vlm_name TEXT,
  vlm_digest TEXT,
  image_sha256 TEXT CHECK (image_sha256 IS NULL OR length(image_sha256)=64),
  total_ms REAL NOT NULL CHECK (total_ms >= 0),
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX idx_agent_run_created ON agent_run(created_at);
CREATE TRIGGER agent_run_no_update BEFORE UPDATE ON agent_run
BEGIN SELECT RAISE(ABORT,'agent_run rows are immutable; create a new run'); END;
CREATE TRIGGER agent_run_no_delete BEFORE DELETE ON agent_run
BEGIN SELECT RAISE(ABORT,'agent_run rows are immutable'); END;
