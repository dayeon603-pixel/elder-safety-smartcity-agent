-- 0008: 담당자 피드백 (선순환 루프의 기록). PRAGMA 없음: 러너가 한 트랜잭션으로 실행.
-- 한 행 = 한 권고(action_id)에 대한 사람의 결정. 수정·삭제 불가 (append-only). 재학습에 아직 쓰이지 않는다:
-- 다음 학습 주기의 입력으로 적재만 한다. 개인정보 컬럼 없음 (note는 200자 자유 서술, 담당자 식별 없음).
CREATE TABLE feedback (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  run_id TEXT REFERENCES agent_run(run_id),
  sgg_code TEXT REFERENCES region(code),
  date TEXT CHECK (date IS NULL OR date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  action_id TEXT NOT NULL CHECK (length(action_id) BETWEEN 1 AND 64),
  decision TEXT NOT NULL CHECK (decision IN ('채택','보류','부적합')),
  note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 200),
  actual_outcome TEXT CHECK (actual_outcome IS NULL OR (json_valid(actual_outcome) AND length(actual_outcome) <= 2048)),
  CHECK (run_id IS NOT NULL OR (sgg_code IS NOT NULL AND date IS NOT NULL))
) STRICT;
CREATE INDEX idx_feedback_region ON feedback(sgg_code, date);
CREATE INDEX idx_feedback_action ON feedback(action_id, decision);
CREATE TRIGGER feedback_no_update BEFORE UPDATE ON feedback
BEGIN SELECT RAISE(ABORT,'feedback rows are immutable; add a new row'); END;
CREATE TRIGGER feedback_no_delete BEFORE DELETE ON feedback
BEGIN SELECT RAISE(ABORT,'feedback rows are immutable'); END;
