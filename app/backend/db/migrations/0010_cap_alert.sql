-- 0010: 발행된 CAP 1.2 경보 (Atom 피드 /cap/feed.atom 의 원본). PRAGMA 없음: 러너가 한 트랜잭션으로 실행.
-- 한 행 = 에이전트가 만든 경보 하나. xml 은 OASIS CAP 1.2 XSD 검증을 통과한 문서만 저장된다 (xsd_valid = 1).
-- status 는 항상 Exercise (훈련): 외부로 전송되는 것은 없고, 구독 가능한 피드로만 공개한다. 수정·삭제 불가.
CREATE TABLE cap_alert (
  identifier TEXT PRIMARY KEY CHECK (length(identifier) BETWEEN 8 AND 128 AND identifier NOT GLOB '*[ ,<&]*'),
  run_id TEXT REFERENCES agent_run(run_id),
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
CREATE TRIGGER cap_alert_no_update BEFORE UPDATE ON cap_alert
BEGIN SELECT RAISE(ABORT,'cap_alert rows are immutable; issue a new identifier'); END;
CREATE TRIGGER cap_alert_no_delete BEFORE DELETE ON cap_alert
BEGIN SELECT RAISE(ABORT,'cap_alert rows are immutable'); END;
