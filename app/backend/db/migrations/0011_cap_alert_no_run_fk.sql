-- 0011: cap_alert.run_id 의 외래키 제거. 경보는 실행(agent_run 행)이 저장되기 *전에* 발행되므로 FK 가 항상 깨졌다.
-- run_id 는 참조용 텍스트로 남긴다 (실행 기록은 /agent/runs/{run_id}). 표는 어디에도 데이터가 없어 다시 만든다.
DROP TABLE cap_alert;
CREATE TABLE cap_alert (
  identifier TEXT PRIMARY KEY CHECK (length(identifier) BETWEEN 8 AND 128 AND identifier NOT GLOB '*[ ,<&]*'),
  run_id TEXT CHECK (run_id IS NULL OR length(run_id) BETWEEN 8 AND 64),
  sent TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('Actual','Exercise','System','Test','Draft')),
  msg_type TEXT NOT NULL CHECK (msg_type IN ('Alert','Update','Cancel','Ack','Error')),
  event TEXT NOT NULL,
  headline TEXT,
  xml TEXT NOT NULL CHECK (length(xml) <= 65536),
  json TEXT NOT NULL CHECK (json_valid(json)),
  xsd_valid INTEGER NOT NULL CHECK (xsd_valid IN (0,1)),
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX idx_cap_alert_created ON cap_alert(created_at);
CREATE INDEX idx_cap_alert_run ON cap_alert(run_id);
CREATE TRIGGER cap_alert_no_update BEFORE UPDATE ON cap_alert
BEGIN SELECT RAISE(ABORT,'cap_alert rows are immutable; issue a new identifier'); END;
CREATE TRIGGER cap_alert_no_delete BEFORE DELETE ON cap_alert
BEGIN SELECT RAISE(ABORT,'cap_alert rows are immutable'); END;
