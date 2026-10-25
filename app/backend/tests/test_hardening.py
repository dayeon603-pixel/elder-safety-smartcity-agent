"""Regression tests for the review findings (H1-H4, M1-M16, L1-L14)."""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import sqlite3
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from numpy.typing import NDArray
from starlette.requests import Request

from app import cli, db, repo
from app.config import ConfigError, load_settings
from app.main import create_app
from app.routers import datasets as ds_router
from app.security import audit, passwords, tokens
from app.security.kdf import derive_key
from app.security.ratelimit import TokenBucketLimiter, client_key, normalize_ip
from experiments.run_experiment import (
    ExperimentConfig, Method, bootstrap, git_hash, load_from_db, run_experiment, split_indices,
)
from tests.conftest import TEST_SECRET
from tests._migrations import ALL_VERSIONS, LATEST  # noqa: F401

PW = "correct horse battery"
KEY = b"k" * 32
F = NDArray[np.float64]
IA = NDArray[np.int64]
RNG = np.random.default_rng(0)


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    base = {
        "KCF_SECRET_KEY": TEST_SECRET, "KCF_DB_PATH": str(tmp_path / "h.sqlite3"),
        "KCF_DATA_DIR": str(tmp_path / "cache"), "KCF_RATE_CAPACITY": "1000",
        "KCF_RATE_REFILL_PER_S": "1000", "KCF_AUTH_RATE_CAPACITY": "1000",
        "KCF_AUTH_RATE_REFILL_PER_S": "1000", "KCF_VERIFY_RATE_CAPACITY": "1000",
        "KCF_VERIFY_RATE_REFILL_PER_S": "1000", "KCF_AUDIT_VERIFY_TTL_S": "0",
    }
    return {**base, **extra}


def _client(tmp_path: Path, **extra: str) -> Iterator[TestClient]:
    with TestClient(create_app(load_settings(_env(tmp_path, **extra))), raise_server_exceptions=False) as c:
        yield c


def _login(c: TestClient, email: str = "a@example.com", pw: str = PW) -> dict[str, str]:
    assert c.post("/auth/register", json={"email": email, "password": pw}).status_code == 202
    r = c.post("/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _req(headers: dict[str, str], peer: str = "10.0.0.9") -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/", "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 1234),
    })


@pytest.fixture
def c(tmp_path: Path) -> Iterator[TestClient]:
    yield from _client(tmp_path)


def _write(tmp_path: Path, name: str, text: str) -> None:
    (tmp_path / "cache" / name).write_text(text, encoding="utf-8")


def _body(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "file_name": "t.csv", "name": "toy", "source_url": "https://example.org/t", "license": "CC0",
        "id_column": "id", "time_column": "when", "label_column": "y", "group_column": "grp",
    }
    return {**base, **kw}


GOOD = "id,when,y,x1,grp\na,2020-01-01,0,1.5,g1\nb,2020-01-02,1,2.5,g2\nc,2020-01-03,1,3.5,g1\n"


# ===== H1 / M11: rate-limit keying ==========================================================
def test_h1_rightmost_forwarded_entry_is_the_key() -> None:
    assert client_key(_req({"X-Forwarded-For": "6.6.6.6, 1.2.3.4"}), True) == "1.2.3.4"
    assert client_key(_req({"X-Forwarded-For": "7.7.7.7, 1.2.3.4"}), True) == "1.2.3.4"
    assert client_key(_req({"X-Forwarded-For": "1.2.3.4"}), True) == "1.2.3.4"  # nginx $remote_addr
    assert client_key(_req({"X-Forwarded-For": "6.6.6.6, 1.2.3.4"}), False) == "10.0.0.9"
    assert client_key(_req({}), True) == "10.0.0.9"
    assert client_key(_req({"X-Forwarded-For": "1.2.3.4, not-an-ip"}), True) == "10.0.0.9"


def test_h1_ipv6_keyed_by_slash_64() -> None:
    a = normalize_ip("2001:db8:1:2:aaaa:bbbb:cccc:dddd")
    assert a == normalize_ip("2001:db8:1:2::1") == "2001:db8:1:2::/64"
    assert a != normalize_ip("2001:db8:1:3::1")
    assert normalize_ip("::ffff:1.2.3.4") == "1.2.3.4"
    assert normalize_ip("garbage") is None


def test_h1_spoofed_leftmost_xff_does_not_reset_bucket(tmp_path: Path) -> None:
    env = {"KCF_RATE_CAPACITY": "3", "KCF_RATE_REFILL_PER_S": "0.001", "KCF_TRUST_PROXY": "true"}
    for c in _client(tmp_path, **env):
        codes = [
            c.get("/health", headers={"X-Forwarded-For": f"9.9.9.{i}, 1.2.3.4"}).status_code
            for i in range(5)
        ]
        assert codes == [200, 200, 200, 429, 429]
        # a different real client (rightmost) has its own bucket
        assert c.get("/health", headers={"X-Forwarded-For": "5.5.5.5"}).status_code == 200


def test_h1_untrusted_proxy_ignores_header(tmp_path: Path) -> None:
    env = {"KCF_RATE_CAPACITY": "2", "KCF_RATE_REFILL_PER_S": "0.001"}
    for c in _client(tmp_path, **env):
        codes = [c.get("/health", headers={"X-Forwarded-For": f"9.9.9.{i}"}).status_code for i in range(3)]
        assert codes == [200, 200, 429]


def test_m11_docker_nginx_shape_single_entry_header(tmp_path: Path) -> None:
    """nginx sets X-Forwarded-For to $remote_addr (one entry). Behind colima every client may
    present the VM gateway address, so they share one bucket: documented, and asserted here."""
    env = {"KCF_RATE_CAPACITY": "2", "KCF_RATE_REFILL_PER_S": "0.001", "KCF_TRUST_PROXY": "true"}
    for c in _client(tmp_path, **env):
        gw = {"X-Forwarded-For": "192.168.5.2"}
        assert [c.get("/health", headers=gw).status_code for _ in range(3)] == [200, 200, 429]


def test_h1_eviction_cannot_reset_a_throttled_victim() -> None:
    now = [0.0]
    lim = TokenBucketLimiter(2, 0.001, clock=lambda: now[0], max_keys=3)
    for victim in ("v1", "v2", "v3"):
        assert lim.allow(victim)[0] and lim.allow(victim)[0] and not lim.allow(victim)[0]
    for i in range(200):  # attacker floods fresh keys
        lim.allow(f"fake{i}")
    assert len(lim._buckets) == 3
    assert all(not lim.allow(v)[0] for v in ("v1", "v2", "v3"))  # still throttled


def test_h1_idle_full_buckets_are_evictable() -> None:
    now = [0.0]
    lim = TokenBucketLimiter(2, 1.0, clock=lambda: now[0], max_keys=2)
    lim.allow("a"), lim.allow("b")
    now[0] += 10.0  # both refilled to capacity: evicting them loses nothing
    assert lim.allow("c")[0]
    assert len(lim._buckets) == 2 and "c" in lim._buckets


# ===== H2: keyed audit chain ================================================================
def test_h2_hkdf_rfc5869_vector() -> None:
    okm = derive_key(
        bytes([0x0B] * 22), bytes(range(0xF0, 0xFA)), 42, bytes(range(0x0D))
    )
    assert okm.hex() == (
        "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"
    )


def test_h2_audit_key_derived_and_separate_from_secret(tmp_path: Path) -> None:
    s = load_settings(_env(tmp_path))
    assert s.audit_key != s.secret_key and len(s.audit_key) == 32
    other = load_settings(_env(tmp_path, KCF_AUDIT_KEY="Zq8r1LmN4vXc7TgB2wYh5Kd9PjS3aUeF"))
    assert other.audit_key == b"Zq8r1LmN4vXc7TgB2wYh5Kd9PjS3aUeF"
    with pytest.raises(ConfigError, match="differ"):
        load_settings(_env(tmp_path, KCF_AUDIT_KEY=TEST_SECRET))


def _drop_triggers(conn: sqlite3.Connection) -> None:
    for t in ("audit_log_no_update", "audit_log_no_delete", "audit_log_no_replace"):
        conn.execute(f"DROP TRIGGER {t}")


def test_h2_attacker_with_db_write_cannot_recompute_chain(conn: sqlite3.Connection) -> None:
    for i in range(3):
        audit.append(conn, KEY, "u:1", "a", {"i": i})
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    _drop_triggers(conn)
    prev = audit.GENESIS_HASH
    for r in rows:  # forge entry 1 and re-chain everything with PLAIN SHA-256 (no key)
        detail = '{"i":999}' if r["id"] == 1 else r["detail"]
        body = audit.canonical_json(
            {"ts": r["ts"], "actor": r["actor"], "action": r["action"], "detail": json.loads(detail)}
        )
        h = hashlib.sha256(f"{prev}\n{body}".encode()).hexdigest()
        conn.execute("UPDATE audit_log SET detail=?, prev_hash=?, hash=? WHERE id=?", (detail, prev, h, r["id"]))
        prev = h
    res = audit.verify(conn, KEY)
    assert not res.valid and res.broken_at == 1 and res.reason == "hash mismatch"


def test_h2_wrong_key_does_not_verify(conn: sqlite3.Connection) -> None:
    audit.append(conn, KEY, "u:1", "a", {})
    assert audit.verify(conn, KEY).valid
    assert not audit.verify(conn, b"x" * 32).valid


def test_h2_prev_hash_is_unique(conn: sqlite3.Connection) -> None:
    audit.append(conn, KEY, "u:1", "a", {})
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO audit_log (ts, actor, action, detail, prev_hash, hash) VALUES "
            "('t','a','b','{}', ?, ?)", (audit.GENESIS_HASH, "f" * 64),
        )


@pytest.mark.parametrize("recursive", [True, False])
def test_h2_insert_or_replace_cannot_overwrite(conn: sqlite3.Connection, recursive: bool) -> None:
    h1 = audit.append(conn, KEY, "u:1", "a", {})
    audit.append(conn, KEY, "u:1", "b", {})
    conn.execute(f"PRAGMA recursive_triggers = {'ON' if recursive else 'OFF'}")  # trigger alone must hold
    attacks = [
        ("INSERT OR REPLACE INTO audit_log (id, ts, actor, action, detail, prev_hash, hash) "
         "VALUES (1, 't', 'evil', 'x', '{}', ?, ?)", ("a" * 64, "b" * 64)),
        ("INSERT OR REPLACE INTO audit_log (ts, actor, action, detail, prev_hash, hash) "
         "VALUES ('t', 'evil', 'x', '{}', ?, ?)", ("c" * 64, h1)),
        ("INSERT OR REPLACE INTO audit_log (ts, actor, action, detail, prev_hash, hash) "
         "VALUES ('t', 'evil', 'x', '{}', ?, ?)", (audit.GENESIS_HASH, "d" * 64)),
    ]
    for sql, params in attacks:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(sql, params)
    assert conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"] == 2
    assert audit.verify(conn, KEY).valid


def test_h2_verify_endpoint_returns_pinnable_head(c: TestClient, tmp_path: Path) -> None:
    _login(c)
    r = c.get("/audit/verify").json()
    conn = db.connect(tmp_path / "h.sqlite3")
    head = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()["hash"]
    conn.close()
    assert r["valid"] and r["head"] == head and r["entries"] == 2


def test_h2_migration_from_v1_keeps_legacy_chain(tmp_path: Path) -> None:
    v1 = tmp_path / "v1"
    v1.mkdir()
    (v1 / "schema.sql").write_text((db.DB_DIR / "schema.sql").read_text())
    conn = db.connect(tmp_path / "up.sqlite3")
    assert db.migrate(conn, v1) == 1
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, detail, prev_hash, hash) VALUES "
        "('t', 'old', 'x', '{}', ?, ?)", (audit.GENESIS_HASH, "e" * 64),
    )
    assert db.migrate(conn) == LATEST
    assert conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"] == 0
    assert conn.execute("SELECT actor FROM audit_log_legacy_v1").fetchone()["actor"] == "old"
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit_log_legacy_v1")
    audit.append(conn, KEY, "u:1", "a", {})
    assert audit.verify(conn, KEY).valid


# ===== H3: column roles / label leakage =====================================================
def _ingest(c: TestClient, tmp_path: Path, csv: str = GOOD, **kw: object) -> dict[str, object]:
    _write(tmp_path, "t.csv", csv)
    h = _login(c)
    r = c.post("/datasets/ingest", json=_body(**kw), headers=h)
    assert r.status_code == 200, r.text
    out: dict[str, object] = r.json()
    return out


def test_h3_label_id_time_group_never_become_features(c: TestClient, tmp_path: Path) -> None:
    csv = "id,when,y,x1,x2,grp\n" + "".join(
        f"r{i},2020-01-{i + 1:02d},{i % 2},{i * 1.5},{i * 0.5},g{i % 3}\n" for i in range(12)
    )
    out = _ingest(c, tmp_path, csv)
    roles = out["dataset"]["column_roles"]  # type: ignore[index]
    assert roles["features"] == ["x1", "x2"] and roles["label"] == "y"
    conn = db.connect(tmp_path / "h.sqlite3")
    data = load_from_db(conn, "toy")
    assert data.feature_names == ["x1", "x2"] and data.x.shape == (12, 2)
    assert data.groups is not None and data.times is not None
    with pytest.raises(ValueError, match="reserved"):
        load_from_db(conn, "toy", feature_columns=["x1", "y"])
    with pytest.raises(ValueError, match="reserved"):
        load_from_db(conn, "toy", feature_columns=["id"])
    conn.close()


def test_h3_explicit_feature_may_not_be_the_label(c: TestClient, tmp_path: Path) -> None:
    _write(tmp_path, "t.csv", GOOD)
    h = _login(c)
    r = c.post("/datasets/ingest", json=_body(feature_columns=["x1", "y"]), headers=h)
    assert r.status_code == 422 and "label" in r.text


def test_h3_legacy_dataset_without_roles_fails_loudly(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO datasets (name, source_url, license, sha256, fetched_at, row_count) "
        "VALUES ('old', 'https://x.org', 'l', ?, 't', 1)", ("a" * 64,),
    )
    with pytest.raises(ValueError, match="column roles"):
        load_from_db(conn, "old")


# ===== H4 / M12 / M13: splits and bootstrap ================================================
def test_h4_group_split_is_group_disjoint_and_deterministic() -> None:
    rng = np.random.default_rng(1)
    groups = np.repeat(np.arange(40), 5)
    y = np.repeat(rng.integers(0, 2, 40), 5).astype(np.int64)
    y[::7] = 1 - y[::7]
    cfg = ExperimentConfig("g", seed=3)
    tr, te = split_indices(y, cfg, groups=groups)
    assert not set(groups[tr]) & set(groups[te]) and len(tr) + len(te) == len(y)
    assert np.array_equal(te, split_indices(y, cfg, groups=groups)[1])
    with pytest.raises(ValueError, match="2 groups"):
        split_indices(y, cfg, groups=np.zeros(len(y), dtype=int))


def test_h4_cluster_bootstrap_is_wider_than_iid_for_clustered_data() -> None:
    rng = np.random.default_rng(5)
    n_groups, size = 30, 20
    y_g = rng.integers(0, 2, n_groups)
    y = np.repeat(y_g, size).astype(np.int64)
    p_g = np.clip(y_g * 0.6 + rng.normal(0.2, 0.3, n_groups), 0.01, 0.99)
    p = np.repeat(p_g, size)  # every row of a group is a copy: n_eff = n_groups
    groups = np.repeat(np.arange(n_groups), size)
    cfg = ExperimentConfig("b", n_boot=300)
    iid = bootstrap(y, p, p, cfg)["ours"]["brier"]
    clu = bootstrap(y, p, p, cfg, groups)["ours"]["brier"]
    assert clu["hi"] - clu["lo"] > 1.5 * (iid["hi"] - iid["lo"])


def test_h4_run_experiment_uses_groups(tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    groups = np.repeat(np.arange(60), 4)
    y = np.repeat(rng.integers(0, 2, 60), 4).astype(np.int64)
    x = np.column_stack([y + rng.normal(0, 1, len(y)), rng.normal(size=len(y))]).astype(np.float64)

    def fn(xt: F, yt: IA, xe: F, r: np.random.Generator) -> F:
        return np.asarray(np.clip(0.5 + 0.2 * (xe[:, 0] - xt[:, 0].mean()), 0.01, 0.99), dtype=np.float64)

    res = run_experiment(x, y, Method("m1", fn), Method("m2", fn), ExperimentConfig("grp", n_boot=100),
                         groups=groups, results_dir=tmp_path)
    assert res["group_aware"] is True and "cluster" in res["ci_method"]


def test_m12_temporal_cut_on_distinct_timestamps_with_ties() -> None:
    times = np.array(["2020-01-01"] * 40 + ["2020-01-02"] * 20 + ["2020-01-03"] * 40)
    y = np.array([0, 1] * 50)
    tr, te = split_indices(y, ExperimentConfig("t", temporal=True, test_size=0.4), times)
    assert np.sort(times[tr])[-1] < np.sort(times[te])[0]  # strict: ties never straddle the cut
    assert not set(tr) & set(te) and len(tr) + len(te) == 100
    with pytest.raises(ValueError, match="distinct"):
        split_indices(y, ExperimentConfig("t", temporal=True), np.array(["2020-01-01"] * 100))


def test_m13_nonfinite_features_rejected(tmp_path: Path) -> None:
    x = np.random.default_rng(0).normal(size=(60, 2))
    y = np.array([0, 1] * 30)
    x[3, 1] = np.nan

    def fn(xt: F, yt: IA, xe: F, r: np.random.Generator) -> F:
        return np.full(len(xe), 0.5)

    with pytest.raises(ValueError, match="NaN or infinite"):
        run_experiment(x, y, Method("a", fn), Method("b", fn), ExperimentConfig("nf"), results_dir=tmp_path)


def test_m13_loader_uses_stored_columns_not_first_payload(conn: sqlite3.Connection) -> None:
    roles = {"id": None, "time": None, "label": "y", "group": None, "label_values": [0, 1],
             "features": ["a", "b"], "features_inferred": False}
    recs = [("0", None, 0, None, json.dumps({"a": "1", "b": "2"})),
            ("1", None, 1, None, json.dumps({"a": "3", "b": "inf"}))]
    repo.insert_dataset(conn, name="d", source_url="https://x.org", license_="l", sha256="a" * 64,
                        fetched_at="t", file_name=None, description="", created_by=None,
                        records=recs, column_roles=roles)
    with pytest.raises(ValueError, match="non-finite"):
        load_from_db(conn, "d")
    conn.execute("UPDATE records SET payload = ? WHERE ext_id = '1'", (json.dumps({"a": "3"}),))
    with pytest.raises(ValueError, match="no column 'b'"):
        load_from_db(conn, "d")


# ===== M1: migration runner =================================================================
def test_m1_pragma_inside_string_is_not_stripped(tmp_path: Path) -> None:
    d = tmp_path / "db"
    d.mkdir()
    (d / "schema.sql").write_text(
        "PRAGMA foreign_keys = ON;\nCREATE TABLE t (v TEXT);\n"
        "INSERT INTO t VALUES ('line one\nPRAGMA user_version = 9;\nline three');\n"
        "CREATE TRIGGER tr BEFORE DELETE ON t BEGIN SELECT RAISE(ABORT, 'x;y'); END;\n"
    )
    conn = db.connect(tmp_path / "m.sqlite3")
    db.migrate(conn, d)
    assert conn.execute("SELECT v FROM t").fetchone()["v"] == "line one\nPRAGMA user_version = 9;\nline three"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0


def test_m1_concurrent_migrate_applies_once(tmp_path: Path) -> None:
    path = tmp_path / "race.sqlite3"
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        conn = db.connect(path)
        try:
            barrier.wait()
            assert db.migrate(conn) == LATEST
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    conn = db.connect(path)
    assert [r["version"] for r in conn.execute("SELECT version FROM schema_version ORDER BY 1")] == ALL_VERSIONS


# ===== M2 / M3 / M4: registration ===========================================================
def test_m2_register_and_audit_are_one_transaction(
    c: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> str:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr("app.routers.auth.audit.append", boom)
    assert c.post("/auth/register", json={"email": "a@example.com", "password": PW}).status_code == 500
    conn = db.connect(tmp_path / "h.sqlite3")
    assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
    conn.close()


def test_m3_register_reply_identical_for_new_and_existing(c: TestClient) -> None:
    new = c.post("/auth/register", json={"email": "a@example.com", "password": PW})
    dup = c.post("/auth/register", json={"email": "A@example.com", "password": "another password!"})
    assert new.status_code == dup.status_code == 202 and new.json() == dup.json()
    assert "access_token" not in new.json()
    # the second caller's password was NOT set on the existing account
    assert c.post("/auth/login", json={"email": "a@example.com", "password": "another password!"}).status_code == 401
    assert c.post("/auth/login", json={"email": "a@example.com", "password": PW}).status_code == 200


def test_m4_first_registrant_is_not_admin(c: TestClient, tmp_path: Path) -> None:
    _login(c)
    conn = db.connect(tmp_path / "h.sqlite3")
    assert conn.execute("SELECT role FROM users").fetchone()["role"] == "member"
    conn.close()


def test_m4_admin_only_via_env_email_or_cli(tmp_path: Path) -> None:
    for c in _client(tmp_path, KCF_ADMIN_EMAIL="Boss@Example.com"):
        _login(c, "other@example.com")
        _login(c, "boss@example.com")
        conn = db.connect(tmp_path / "h.sqlite3")
        roles = {r["email"]: r["role"] for r in conn.execute("SELECT email, role FROM users")}
        conn.close()
        assert roles == {"other@example.com": "member", "boss@example.com": "admin"}


def test_m4_cli_seed_admin_creates_and_promotes(tmp_path: Path) -> None:
    env = _env(tmp_path, KCF_ADMIN_PASSWORD=PW)
    assert cli.main(["seed-admin", "--email", "root@example.com"], env) == 0
    assert cli.main(["seed-admin", "--email", "root@example.com"], env) == 0  # idempotent promote
    assert cli.main(["seed-admin", "--email", "bad"], env) == 2
    assert cli.main(["seed-admin", "--email", "x@example.com"], {**env, "KCF_ADMIN_PASSWORD": "short"}) == 2
    conn = db.connect(tmp_path / "h.sqlite3")
    rows = conn.execute("SELECT email, role FROM users").fetchall()
    assert [(r["email"], r["role"]) for r in rows] == [("root@example.com", "admin")]
    s = load_settings(env)
    assert audit.verify(conn, s.audit_key).valid
    conn.close()


# ===== M5 / M6 / M9: ingest =================================================================
@pytest.mark.parametrize("bad", ["1.5", "nan", "inf", "1e999", "2", "", "-1", "abc", "9" * 400])
def test_m5_label_must_be_integer_in_classes(c: TestClient, tmp_path: Path, bad: str) -> None:
    _write(tmp_path, "t.csv", f"id,when,y,x1,grp\na,2020-01-01,0,1,g\nb,2020-01-02,{bad},2,g\n")
    r = c.post("/datasets/ingest", json=_body(), headers=_login(c))
    assert r.status_code == 422 and "row 2" in r.json()["detail"]
    assert c.get("/datasets").json() == []


def test_m5_label_values_configurable(c: TestClient, tmp_path: Path) -> None:
    _write(tmp_path, "t.csv", "id,when,y,x1,grp\na,2020-01-01,0,1,g\nb,2020-01-02,2,2,g\n")
    r = c.post("/datasets/ingest", json=_body(label_values=[0, 1, 2]), headers=_login(c))
    assert r.status_code == 200


def test_m6_short_and_long_rows_report_row_number(c: TestClient, tmp_path: Path) -> None:
    h = _login(c)
    _write(tmp_path, "t.csv", "id,when,y,x1,grp\na,2020-01-01,0,1,g\nb,2020-01-02,1\n")
    r = c.post("/datasets/ingest", json=_body(), headers=h)
    assert r.status_code == 422 and "row 2" in r.json()["detail"] and "expected 5" in r.json()["detail"]
    _write(tmp_path, "t.csv", "id,when,y,x1,grp\na,2020-01-01,0,1,g,EXTRA\n")
    r = c.post("/datasets/ingest", json=_body(), headers=h)
    assert r.status_code == 422 and "row 1" in r.json()["detail"]


@pytest.mark.parametrize("text", ["", "\n", "id,when,y,x1,grp\n"])
def test_m6_empty_csv_rejected(c: TestClient, tmp_path: Path, text: str) -> None:
    _write(tmp_path, "t.csv", text)
    assert c.post("/datasets/ingest", json=_body(), headers=_login(c)).status_code == 422
    assert c.get("/datasets").json() == []


def test_m6_event_time_normalised_and_validated(c: TestClient, tmp_path: Path) -> None:
    csv = "id,when,y,x1,grp\na,2020-01-01,0,1,g\nb,2020-01-02T03:04:05+09:00,1,2,g\nc,20200103T000000Z,1,3,g\n"
    _ingest(c, tmp_path, csv)
    conn = db.connect(tmp_path / "h.sqlite3")
    times = [r["event_time"] for r in conn.execute("SELECT event_time FROM records ORDER BY id")]
    conn.close()
    assert times == ["2020-01-01T00:00:00.000000Z", "2020-01-01T18:04:05.000000Z", "2020-01-03T00:00:00.000000Z"]
    for bad in ("01/02/2020", "yesterday", "", "2020-13-01"):
        _write(tmp_path, "u.csv", f"id,when,y,x1,grp\na,{bad},0,1,g\n")
        r = c.post("/datasets/ingest", json=_body(file_name="u.csv", name="u"), headers=_login_again(c))
        assert r.status_code == 422 and "row 1" in r.json()["detail"], bad


def _login_again(c: TestClient) -> dict[str, str]:
    r = c.post("/auth/login", json={"email": "a@example.com", "password": PW})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_m6_fetched_at_is_ingest_time_and_mtime_is_separate(c: TestClient, tmp_path: Path) -> None:
    _write(tmp_path, "t.csv", GOOD)
    old = float(calendar.timegm((2001, 1, 1, 12, 0, 0)))
    os.utime(tmp_path / "cache" / "t.csv", (old, old))
    ds = c.post("/datasets/ingest", json=_body(), headers=_login(c)).json()["dataset"]
    assert ds["file_mtime"].startswith("2001-01-01") and not ds["fetched_at"].startswith("2001")


def test_m6_reingest_with_different_mapping_is_409(c: TestClient, tmp_path: Path) -> None:
    h = _login(c)
    _write(tmp_path, "t.csv", GOOD)
    assert c.post("/datasets/ingest", json=_body(), headers=h).status_code == 200
    assert c.post("/datasets/ingest", json=_body(), headers=h).json()["created"] is False
    r = c.post("/datasets/ingest", json=_body(group_column=None), headers=h)
    assert r.status_code == 409 and "mapping" in r.json()["detail"]
    assert c.post("/datasets/ingest", json=_body(name="toy2", group_column=None), headers=h).status_code == 200


def test_m6_duplicate_and_empty_header_names_rejected(c: TestClient, tmp_path: Path) -> None:
    h = _login(c)
    _write(tmp_path, "t.csv", "id,when,y,x1,x1\na,2020-01-01,0,1,2\n")
    assert c.post("/datasets/ingest", json=_body(group_column=None), headers=h).status_code == 422
    _write(tmp_path, "t.csv", "id,when,y,,x1\na,2020-01-01,0,1,2\n")
    assert c.post("/datasets/ingest", json=_body(group_column=None), headers=h).status_code == 422


def test_m9_ingest_streams_in_batches_and_cleans_up(
    c: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    n = ds_router.BATCH_ROWS * 2 + 500
    body = "".join(f"r{i},2020-01-01,{i % 2},{i}.5,g{i % 7}\n" for i in range(n))
    _write(tmp_path, "t.csv", "id,when,y,x1,grp\n" + body)
    sizes: list[int] = []
    real = repo.insert_records

    def spy(conn: sqlite3.Connection, ds_id: int, batch: object) -> None:
        sizes.append(len(batch))  # type: ignore[arg-type]
        real(conn, ds_id, batch)  # type: ignore[arg-type]

    monkeypatch.setattr(repo, "insert_records", spy)
    h = _login(c)
    r = c.post("/datasets/ingest", json=_body(), headers=h)
    assert r.status_code == 200 and r.json()["dataset"]["row_count"] == n and r.json()["dataset"]["status"] == "complete"
    assert sizes == [ds_router.BATCH_ROWS, ds_router.BATCH_ROWS, 500]
    # failure after two committed batches leaves nothing behind
    _write(tmp_path, "t.csv", "id,when,y,x1,grp\n" + body + "bad,2020-01-01,7,1,g\n")
    r = c.post("/datasets/ingest", json=_body(name="again"), headers=h)
    assert r.status_code == 422 and f"row {n + 1}" in r.json()["detail"]
    assert [d["name"] for d in c.get("/datasets").json()] == ["toy"]


def test_m9_loading_datasets_hidden_and_swept(conn: sqlite3.Connection) -> None:
    ds_id = repo.begin_dataset(
        conn, name="half", source_url="https://x.org", license_="l", sha256="a" * 64, fetched_at="t",
        file_name=None, description="", created_by=None, column_roles={}, file_mtime=None,
    )
    assert repo.list_datasets(conn) == []
    with pytest.raises(sqlite3.IntegrityError):
        repo.begin_dataset(
            conn, name="half", source_url="https://x.org", license_="l", sha256="a" * 64, fetched_at="t",
            file_name=None, description="", created_by=None, column_roles={}, file_mtime=None,
        )
    assert repo.delete_incomplete_datasets(conn) == 1 and ds_id > 0


def test_m9_file_changed_during_ingest_is_rejected(
    c: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "t.csv", GOOD)
    monkeypatch.setattr(ds_router, "sha256_of", lambda p, m: "0" * 64)  # pass-1 digest != bytes parsed
    r = c.post("/datasets/ingest", json=_body(), headers=_login(c))
    assert r.status_code == 409 and c.get("/datasets").json() == []


def test_m9_byte_cap_enforced_while_streaming(tmp_path: Path) -> None:
    for c in _client(tmp_path, KCF_MAX_CSV_BYTES="40"):
        _write(tmp_path, "t.csv", GOOD)
        assert c.post("/datasets/ingest", json=_body(), headers=_login(c)).status_code == 413


# ===== M7 / M8 / M10 ========================================================================
def test_m7_validation_error_does_not_echo_input(c: TestClient) -> None:
    r = c.post("/auth/register", json={"email": "a@example.com", "password": "tooshort", "x": 1})
    assert r.status_code == 422
    text = r.text
    assert "tooshort" not in text and '"input"' not in text and '"ctx"' not in text
    assert {"loc", "msg", "type"} <= set(r.json()["detail"][0])


def test_m7_unhandled_error_is_generic_with_security_headers_and_cors(tmp_path: Path) -> None:
    app = create_app(load_settings(_env(tmp_path)))
    boom = APIRouter()

    @boom.get("/boom")
    def _boom() -> None:
        raise RuntimeError("secret internal detail /etc/passwd")

    app.include_router(boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom", headers={"Origin": "http://localhost:5190"})
    assert r.status_code == 500 and r.json() == {"detail": "internal server error"}
    assert "secret" not in r.text and "passwd" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors" in r.headers["content-security-policy"]
    assert r.headers["access-control-allow-origin"] == "http://localhost:5190"


def test_m8_audit_verify_is_rate_limited_and_cached(tmp_path: Path) -> None:
    env = {"KCF_VERIFY_RATE_CAPACITY": "2", "KCF_VERIFY_RATE_REFILL_PER_S": "0.001"}
    for c in _client(tmp_path, **env):
        assert [c.get("/audit/verify").status_code for _ in range(3)] == [200, 200, 429]
        assert c.get("/health").status_code == 200  # other routes use a different bucket
    for c in _client(tmp_path / "ttl", KCF_AUDIT_VERIFY_TTL_S="60"):
        first = c.get("/audit/verify").json()
        _login(c)
        assert c.get("/audit/verify").json() == first  # served from cache inside the TTL


def test_m8_failed_login_spam_is_aggregated(c: TestClient, tmp_path: Path) -> None:
    for _ in range(25):
        assert c.post("/auth/login", json={"email": "no@example.com", "password": "x"}).status_code == 401
    conn = db.connect(tmp_path / "h.sqlite3")
    n = conn.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'auth.login_failed'").fetchone()["n"]
    conn.close()
    assert n == 1


def test_m10_openapi_only_in_dev(tmp_path: Path) -> None:
    for c in _client(tmp_path):
        assert c.get("/openapi.json").status_code == 404 and c.get("/docs").status_code == 404
    for c in _client(tmp_path / "dev", KCF_ENV="dev"):
        assert c.get("/openapi.json").status_code == 200


# ===== M14 / M15 / M16 / L11-L13 ============================================================
def _data() -> tuple[F, IA]:
    r = np.random.default_rng(0)
    y = np.array([0, 1] * 60)
    return np.column_stack([y + r.normal(0, 1, 120), r.normal(size=120)]), y


def _const(xt: F, yt: IA, xe: F, r: np.random.Generator) -> F:
    return np.full(len(xe), float(yt.mean()))


def _score(xt: F, yt: IA, xe: F, r: np.random.Generator) -> F:
    return np.asarray(1 / (1 + np.exp(-xe[:, 0])), dtype=np.float64)


def test_m14_experiment_ids_and_files_never_collide(tmp_path: Path, conn: sqlite3.Connection) -> None:
    x, y = _data()
    cfg = ExperimentConfig("same", n_boot=100)
    a = run_experiment(x, y, Method("a", _const), Method("b", _score), cfg, conn=conn, results_dir=tmp_path)
    b = run_experiment(x, y, Method("a", _const), Method("b", _score), cfg, conn=conn, results_dir=tmp_path)
    assert a["experiment"] != b["experiment"] and a["result_path"] != b["result_path"]
    assert Path(a["result_path"]).exists() and Path(b["result_path"]).exists()
    assert a["experiment"].split("@")[1] in Path(a["result_path"]).name
    assert conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 4


@pytest.mark.parametrize("name", ["../evil", "a/b", "", ".hidden", "x" * 80, "a b"])
def test_m14_config_name_is_sanitised(name: str) -> None:
    with pytest.raises(ValueError, match="name must match"):
        ExperimentConfig(name)


def test_m15_git_hash_marks_dirty(tmp_path: Path) -> None:
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("1")
    subprocess.run([*git, "add", "."], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-qm", "c"], cwd=tmp_path, check=True)
    clean = git_hash(tmp_path)
    assert not clean.endswith("-dirty") and clean != "unversioned"
    (tmp_path / "f.txt").write_text("2")
    assert git_hash(tmp_path) == f"{clean}-dirty"


def test_m16_runs_store_dataset_sha256(tmp_path: Path, conn: sqlite3.Connection) -> None:
    from experiments.example_breast_cancer import DATASET_NAME, make_baseline, make_ours, register_wdbc

    ds_id = register_wdbc(conn)
    data = load_from_db(conn, DATASET_NAME)
    res = run_experiment(data.x, data.y, make_baseline(data.feature_names), make_ours(),
                         ExperimentConfig("p", n_boot=100), conn=conn, dataset_id=ds_id, results_dir=tmp_path)
    sha = conn.execute("SELECT sha256 FROM datasets WHERE id = ?", (ds_id,)).fetchone()["sha256"]
    params = [json.loads(r["params"]) for r in conn.execute("SELECT params FROM runs")]
    assert all(p["dataset_sha256"] == sha for p in params) and res["dataset_sha256"] == sha
    from sklearn.datasets import load_breast_cancer

    b = load_breast_cancer()
    expect = hashlib.sha256(
        b.data.astype("<f8").tobytes() + (1 - b.target).astype("<i8").tobytes()
    ).hexdigest()
    assert sha == expect  # the hash is over the arrays, as the provenance text states
    desc = conn.execute("SELECT description FROM datasets WHERE id = ?", (ds_id,)).fetchone()["description"]
    assert "not of a CSV" in desc and "scikit-learn" in desc


def test_l11_l12_l13_ci_label_durations_and_rng(tmp_path: Path) -> None:
    x, y = _data()
    seen: list[object] = []

    def slow(xt: F, yt: IA, xe: F, r: np.random.Generator) -> F:
        seen.append(r)
        time.sleep(0.15)
        return _const(xt, yt, xe, r)

    def stochastic(xt: F, yt: IA, xe: F, r: np.random.Generator) -> F:
        return np.clip(_score(xt, yt, xe, r) * 0.9 + r.uniform(0, 0.05, len(xe)), 0, 1)

    cfg = ExperimentConfig("l", n_boot=100)
    a = run_experiment(x, y, Method("slow", slow), Method("rnd", stochastic), cfg, results_dir=tmp_path)
    b = run_experiment(x, y, Method("slow", slow), Method("rnd", stochastic), cfg, results_dir=tmp_path)
    assert "percentile" in a["ci_method"]
    assert a["duration_s"]["baseline"] >= 0.15 > a["duration_s"]["ours"]  # per-role, not shared
    assert all(isinstance(r, np.random.Generator) for r in seen)
    assert a["methods"]["ours"]["y_prob"] == b["methods"]["ours"]["y_prob"]  # rng is seeded


# ===== L1 .. L10 ============================================================================
def test_l1_base64_is_strict_and_canonical() -> None:
    assert tokens._b64d("QQ") == b"A"
    for bad in ("QR", "Q Q", "QQ==", "QQ\n", "Q+", "QQ$"):
        with pytest.raises(tokens.TokenError):
            tokens._b64d(bad)
    t = tokens.issue_token(b"s" * 48, 1, "member", 60, now=1000)
    body, sig = t.split(".")
    assert tokens.verify_token(b"s" * 48, t, now=1001)["sub"] == 1
    alt = sig[:-1] + ("B" if sig[-1] != "B" else "C")  # same MAC bytes would need canonical form
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(b"s" * 48, f"{body}.{alt}", now=1001)


@pytest.mark.parametrize(
    "secret",
    ["a" * 40, "ab" * 20, "changeme-changeme-changeme-change", "my-secret-key-for-the-app-1234567", "0123" * 4],
)
def test_l3_weak_or_placeholder_secrets_rejected(secret: str) -> None:
    with pytest.raises(ConfigError):
        load_settings({"KCF_SECRET_KEY": secret})


@pytest.mark.parametrize("ttl", ["0", "0.5", "-5", "abc", "nan", "inf", "900.5"])
def test_l4_ttl_must_be_integer_at_least_one(tmp_path: Path, ttl: str) -> None:
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, KCF_TOKEN_TTL_S=ttl))
    assert load_settings(_env(tmp_path, KCF_TOKEN_TTL_S="1")).token_ttl_s == 1


def test_l5_password_whitespace_is_significant(c: TestClient) -> None:
    padded = "  spaced out pass  "
    assert c.post("/auth/register", json={"email": "a@example.com", "password": padded}).status_code == 202
    ok = c.post("/auth/login", json={"email": "a@example.com", "password": padded})
    assert ok.status_code == 200
    assert c.post("/auth/login", json={"email": "a@example.com", "password": padded.strip()}).status_code == 401
    # 12 spaces is not a valid password because length is counted unstripped but must still pass min
    assert c.post("/auth/register", json={"email": " b@example.com ", "password": PW}).status_code == 202


def test_l6_login_rehashes_outdated_hash(c: TestClient, tmp_path: Path) -> None:
    from argon2 import PasswordHasher, Type

    weak = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1, type=Type.ID).hash(PW)
    assert passwords.needs_rehash(weak)
    conn = db.connect(tmp_path / "h.sqlite3")
    db.migrate(conn)
    conn.execute("INSERT INTO users (email, password_hash) VALUES ('w@example.com', ?)", (weak,))
    conn.close()
    assert c.post("/auth/login", json={"email": "w@example.com", "password": PW}).status_code == 200
    conn = db.connect(tmp_path / "h.sqlite3")
    new = conn.execute("SELECT password_hash FROM users WHERE email = 'w@example.com'").fetchone()[0]
    conn.close()
    assert new != weak and not passwords.needs_rehash(new) and passwords.verify_password(new, PW)


def test_l7_audit_timestamps_follow_chain_order(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "ts.sqlite3"
    db.migrate(db.connect(path))

    def worker(i: int) -> None:
        cn = db.connect(path)
        for j in range(10):
            audit.append(cn, KEY, f"u:{i}", "a", {"j": j})
        cn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    cn = db.connect(path)
    ts = [r["ts"] for r in cn.execute("SELECT ts FROM audit_log ORDER BY id")]
    assert len(ts) == 40 and ts == sorted(ts) and audit.verify(cn, KEY).valid


def test_l8_synchronous_full(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 1


def test_l10_hsts_only_when_enabled(tmp_path: Path) -> None:
    for c in _client(tmp_path):
        assert "strict-transport-security" not in c.get("/health").headers
    for c in _client(tmp_path / "h", KCF_HSTS="true"):
        assert c.get("/health").headers["strict-transport-security"].startswith("max-age=")


def test_l14_available_requires_auth(c: TestClient) -> None:
    assert c.get("/datasets/available").status_code == 401
    assert c.get("/datasets/available", headers=_login(c)).status_code == 200
