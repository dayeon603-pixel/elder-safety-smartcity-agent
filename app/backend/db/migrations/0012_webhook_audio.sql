-- 0012: 행정 시스템 연동(웹훅 수신 시뮬레이터)과 CAP 경보의 정류장 안내문. PRAGMA 없음: 러너가 한 트랜잭션으로 실행.
-- sim_inbox.channel 에 'webhook' 추가 (CHECK 변경 = 표 재생성, 내용은 복사). 행은 여전히 수정·삭제 불가.
-- cap_alert.bit_lines: 그 경보의 버스정류장 안내단말 2줄 문구(JSON 배열), audio 는 /cap/alerts/{id}/audio.wav 로 생성.
CREATE TABLE sim_inbox_v2 (
  id INTEGER PRIMARY KEY,
  receipt_id TEXT NOT NULL UNIQUE CHECK (length(receipt_id) BETWEEN 8 AND 64),
  channel TEXT NOT NULL CHECK (channel IN ('rsu','119','webhook')),
  payload TEXT NOT NULL CHECK (json_valid(payload)),
  payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256)=64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
  received_at TEXT NOT NULL
) STRICT;
INSERT INTO sim_inbox_v2 (id, receipt_id, channel, payload, payload_sha256, received_at)
  SELECT id, receipt_id, channel, payload, payload_sha256, received_at FROM sim_inbox;
DROP TABLE sim_inbox;
ALTER TABLE sim_inbox_v2 RENAME TO sim_inbox;
CREATE INDEX idx_sim_inbox_channel ON sim_inbox(channel, id);
CREATE TRIGGER sim_inbox_no_update BEFORE UPDATE ON sim_inbox
BEGIN SELECT RAISE(ABORT,'sim_inbox rows are immutable'); END;
CREATE TRIGGER sim_inbox_no_delete BEFORE DELETE ON sim_inbox
BEGIN SELECT RAISE(ABORT,'sim_inbox rows are immutable'); END;
ALTER TABLE cap_alert ADD COLUMN bit_lines TEXT CHECK (bit_lines IS NULL OR (json_valid(bit_lines) AND length(bit_lines) <= 256));
