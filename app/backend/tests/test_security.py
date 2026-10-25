"""Security primitives: hashing, tokens, rate limit, audit chain, SQL injection."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, repo
from app.config import ConfigError, load_settings
from app.main import create_app
from app.security import audit, passwords, tokens
from app.security.ratelimit import TokenBucketLimiter
from tests.conftest import TEST_SECRET

SECRET = b"s" * 48
GOOD_PW = "correct horse battery"
KEY = b"k" * 32


# --- Argon2id ---------------------------------------------------------------
def test_password_hash_is_argon2id_and_verifies() -> None:
    h = passwords.hash_password(GOOD_PW)
    assert h.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert passwords.verify_password(h, GOOD_PW)
    assert not passwords.verify_password(h, GOOD_PW + "x")
    assert not passwords.verify_password(h, "")


def test_password_salts_differ_and_missing_user_never_verifies() -> None:
    assert passwords.hash_password(GOOD_PW) != passwords.hash_password(GOOD_PW)
    assert not passwords.verify_password(None, GOOD_PW)
    assert not passwords.verify_password("not-a-hash", GOOD_PW)


# --- HMAC tokens ------------------------------------------------------------
def test_token_roundtrip() -> None:
    t = tokens.issue_token(SECRET, 7, "member", 60, now=1000)
    claims = tokens.verify_token(SECRET, t, now=1010)
    assert claims["sub"] == 7 and claims["role"] == "member"


def test_token_expiry_boundary() -> None:
    t = tokens.issue_token(SECRET, 1, "member", 60, now=1000)
    tokens.verify_token(SECRET, t, now=1059)
    with pytest.raises(tokens.ExpiredTokenError):
        tokens.verify_token(SECRET, t, now=1060)


def test_token_tamper_payload_and_signature_and_secret() -> None:
    t = tokens.issue_token(SECRET, 1, "member", 60, now=1000)
    body, sig = t.split(".")
    forged_body = tokens.issue_token(SECRET, 1, "admin", 60, now=1000).split(".")[0]
    for bad in (f"{forged_body}.{sig}", f"{body}.{sig[:-2]}AA", f"{body}x.{sig}", body, "a.b.c", "", "."):
        with pytest.raises(tokens.TokenError):
            tokens.verify_token(SECRET, bad, now=1001)
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(b"o" * 48, t, now=1001)


def test_token_rejects_alg_none_style_and_oversize() -> None:
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(SECRET, "x" * 5000)


def test_secret_must_come_from_env_and_be_long() -> None:
    with pytest.raises(ConfigError):
        load_settings({})
    with pytest.raises(ConfigError):
        load_settings({"KCF_SECRET_KEY": "short"})
    with pytest.raises(ConfigError):
        load_settings({"KCF_SECRET_KEY": TEST_SECRET, "KCF_CORS_ORIGINS": "*"})


# --- Rate limiter -----------------------------------------------------------
def test_token_bucket_burst_refill_and_isolation() -> None:
    now = [0.0]
    lim = TokenBucketLimiter(capacity=3, refill_per_s=1.0, clock=lambda: now[0])
    assert [lim.allow("a")[0] for _ in range(3)] == [True, True, True]
    ok, wait = lim.allow("a")
    assert not ok and wait == pytest.approx(1.0)
    assert lim.allow("b")[0]
    now[0] += 1.0
    assert lim.allow("a")[0] and not lim.allow("a")[0]
    now[0] += 100.0
    assert [lim.allow("a")[0] for _ in range(4)] == [True, True, True, False]


def test_token_bucket_bounded_memory() -> None:
    lim = TokenBucketLimiter(1, 1, max_keys=5)
    for i in range(50):
        lim.allow(str(i))
    assert len(lim._buckets) == 5


def test_rate_limit_middleware_returns_429(tmp_path) -> None:  # type: ignore[no-untyped-def]
    s = load_settings({
        "KCF_SECRET_KEY": TEST_SECRET, "KCF_DB_PATH": str(tmp_path / "r.sqlite3"),
        "KCF_RATE_CAPACITY": "3", "KCF_RATE_REFILL_PER_S": "0.001",
    })
    with TestClient(create_app(s)) as c:
        codes = [c.get("/health").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_auth_bucket_is_stricter(tmp_path) -> None:  # type: ignore[no-untyped-def]
    s = load_settings({
        "KCF_SECRET_KEY": TEST_SECRET, "KCF_DB_PATH": str(tmp_path / "r.sqlite3"),
        "KCF_AUTH_RATE_CAPACITY": "2", "KCF_AUTH_RATE_REFILL_PER_S": "0.001",
    })
    body = {"email": "a@example.com", "password": "whatever-password"}
    with TestClient(create_app(s)) as c:
        codes = [c.post("/auth/login", json=body).status_code for _ in range(3)]
        r = c.post("/auth/login", json=body)
        assert c.get("/health").status_code == 200
    assert codes == [401, 401, 429]
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


# --- Audit chain ------------------------------------------------------------
def test_audit_chain_valid_and_links(conn: sqlite3.Connection) -> None:
    hashes = [audit.append(conn, KEY, "u:1", f"act{i}", {"i": i}) for i in range(5)]
    r = audit.verify(conn, KEY)
    assert r.valid and r.entries == 5 and r.head == hashes[-1]
    prevs = [x["prev_hash"] for x in conn.execute("SELECT prev_hash FROM audit_log ORDER BY id")]
    assert prevs == [audit.GENESIS_HASH, *hashes[:-1]]


def test_audit_empty_chain_is_valid(conn: sqlite3.Connection) -> None:
    r = audit.verify(conn, KEY)
    assert r.valid and r.entries == 0 and r.head == audit.GENESIS_HASH


def test_audit_triggers_block_update_and_delete(conn: sqlite3.Connection) -> None:
    audit.append(conn, KEY, "u:1", "a", {})
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE audit_log SET actor = 'evil' WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit_log WHERE id = 1")


def _bypass_triggers(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> None:
    """Simulate an attacker with raw file access who drops the triggers first."""
    conn.execute("DROP TRIGGER audit_log_no_update")
    conn.execute("DROP TRIGGER audit_log_no_delete")
    conn.execute(sql, params)


def test_audit_detects_edit(conn: sqlite3.Connection) -> None:
    for i in range(4):
        audit.append(conn, KEY, "u:1", "a", {"i": i})
    _bypass_triggers(conn, "UPDATE audit_log SET detail = ? WHERE id = 2", ('{"i":999}',))
    r = audit.verify(conn, KEY)
    assert not r.valid and r.broken_at == 2 and r.reason == "hash mismatch"


def test_audit_detects_mid_chain_delete(conn: sqlite3.Connection) -> None:
    for i in range(4):
        audit.append(conn, KEY, "u:1", "a", {"i": i})
    _bypass_triggers(conn, "DELETE FROM audit_log WHERE id = 2")
    r = audit.verify(conn, KEY)
    assert not r.valid and r.broken_at == 3 and r.reason == "prev_hash mismatch"


def test_audit_detects_rehashed_edit_without_fixing_successors(conn: sqlite3.Connection) -> None:
    for i in range(3):
        audit.append(conn, KEY, "u:1", "a", {"i": i})
    row = conn.execute("SELECT * FROM audit_log WHERE id = 1").fetchone()
    new = audit.entry_hash(KEY, row["prev_hash"], row["ts"], "evil", row["action"], row["detail"])
    _bypass_triggers(conn, "UPDATE audit_log SET actor = 'evil', hash = ? WHERE id = 1", (new,))
    r = audit.verify(conn, KEY)
    assert not r.valid and r.broken_at == 2


def test_audit_verify_endpoint(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "a@example.com", "password": GOOD_PW})
    r = client.get("/audit/verify").json()
    assert r["valid"] is True and r["entries"] == 1 and len(r["head"]) == 64


# --- SQL injection / validation --------------------------------------------
def test_sql_injection_in_login_is_inert(client: TestClient, settings) -> None:  # type: ignore[no-untyped-def]
    client.post("/auth/register", json={"email": "victim@example.com", "password": GOOD_PW})
    for payload in ("' OR '1'='1", "x'; DROP TABLE users;--", "victim@example.com' --"):
        r = client.post("/auth/login", json={"email": payload, "password": payload})
        assert r.status_code == 422  # rejected by the strict email pattern before SQL
    # hostile-but-pattern-valid input is just data
    r = client.post("/auth/login", json={"email": "a%--@example.com", "password": "' OR 1=1 --"})
    assert r.status_code == 401
    conn = db.connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1
    conn.close()


def test_repo_binds_hostile_strings(conn: sqlite3.Connection) -> None:
    repo.create_user(conn, "a@example.com", "$argon2id$x")
    assert repo.get_user_by_email(conn, "' OR 1=1 --") is None
    assert repo.get_dataset_by_name(conn, "x'; DROP TABLE users;--") is None
    assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1


def test_strict_validation_rejects_unknown_fields_and_types(client: TestClient) -> None:
    base = {"email": "a@example.com", "password": GOOD_PW}
    assert client.post("/auth/register", json={**base, "role": "admin"}).status_code == 422
    assert client.post("/auth/register", json={**base, "password": "short"}).status_code == 422
    assert client.post("/auth/register", json={**base, "password": 123456789012}).status_code == 422
    assert client.post("/auth/register", json={"email": "nope", "password": GOOD_PW}).status_code == 422


def test_security_headers_and_cors(client: TestClient) -> None:
    r = client.get("/health", headers={"Origin": "http://localhost:5190"})
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["access-control-allow-origin"] == "http://localhost:5190"
    evil = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers


def test_foreign_keys_and_checks_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO records (dataset_id, ext_id, payload) VALUES (999, 'x', '{}')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO users (email, password_hash) VALUES ('a@b.co', 'plaintext')")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
