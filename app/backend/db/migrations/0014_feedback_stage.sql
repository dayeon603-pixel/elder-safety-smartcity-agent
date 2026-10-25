-- 0014: 결재 단계 (gov-operations 시연). PRAGMA 없음: 러너가 한 트랜잭션으로 실행.
-- 한 행 = 한 권고(sgg_code, date, action_id)에 대한 단계 기록: adopt(채택) → approve_demo(과장 결재 시연) / hold(보류, 사유 필수).
-- append-only; 모든 단계는 audit_log 해시 체인에도 기록된다. 어떤 단계도 "발송"을 뜻하지 않는다 (전달 대기).
CREATE TABLE feedback_stage (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  feedback_id INTEGER REFERENCES feedback(id),
  run_id TEXT REFERENCES agent_run(run_id),
  sgg_code TEXT REFERENCES region(code),
  date TEXT CHECK (date IS NULL OR date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  action_id TEXT NOT NULL CHECK (length(action_id) BETWEEN 1 AND 64),
  stage TEXT NOT NULL CHECK (stage IN ('adopt','approve_demo','hold')),
  reason TEXT NOT NULL DEFAULT '' CHECK (length(reason) <= 200),
  audit_hash TEXT CHECK (audit_hash IS NULL OR length(audit_hash) = 64),
  CHECK (stage <> 'hold' OR length(reason) > 0),
  CHECK (run_id IS NOT NULL OR (sgg_code IS NOT NULL AND date IS NOT NULL))
) STRICT;
CREATE INDEX idx_feedback_stage_ref ON feedback_stage(sgg_code, date, action_id, id);
CREATE TRIGGER feedback_stage_no_update BEFORE UPDATE ON feedback_stage
BEGIN SELECT RAISE(ABORT,'feedback_stage rows are immutable; add a new row'); END;
CREATE TRIGGER feedback_stage_no_delete BEFORE DELETE ON feedback_stage
BEGIN SELECT RAISE(ABORT,'feedback_stage rows are immutable'); END;
