"""Endpoint and migration tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from tests.conftest import TEST_SECRET
from tests._migrations import ALL_VERSIONS, LATEST  # noqa: F401

PW = "correct horse battery"
CSV = "id,when,y,x1,grp\na,2020-01-01,0,1.5,g1\nb,2020-01-02,1,2.5,g2\nc,2020-01-03,1,3.5,g1\n"


def _auth(client: TestClient, email: str = "a@example.com") -> dict[str, str]:
    assert client.post("/auth/register", json={"email": email, "password": PW}).status_code == 202
    r = client.post("/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _body(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "file_name": "t.csv", "name": "toy", "source_url": "https://example.org/t", "license": "CC0",
        "id_column": "id", "time_column": "when", "label_column": "y", "group_column": "grp",
    }
    return {**base, **kw}


def test_health_info(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    info = client.get("/info").json()
    assert info["schema_version"] == LATEST and info["datasets"] == [] and info["last_run"] is None


def test_register_login_duplicate(client: TestClient) -> None:
    _auth(client)
    dup = client.post("/auth/register", json={"email": "A@example.com", "password": PW})
    assert dup.status_code == 202
    assert client.post("/auth/login", json={"email": "a@example.com", "password": PW}).status_code == 200
    assert client.post("/auth/login", json={"email": "a@example.com", "password": "wrong-password"}).status_code == 401


def test_ingest_requires_auth_and_records_provenance(client: TestClient, settings: Settings) -> None:
    (settings.data_dir / "t.csv").write_text(CSV, encoding="utf-8")
    assert client.post("/datasets/ingest", json=_body()).status_code == 401
    h = _auth(client)
    r = client.post("/datasets/ingest", json=_body(), headers=h)
    assert r.status_code == 200 and r.json()["created"] is True
    ds = r.json()["dataset"]
    import hashlib
    assert ds["sha256"] == hashlib.sha256(CSV.encode()).hexdigest() and ds["row_count"] == 3
    again = client.post("/datasets/ingest", json=_body(), headers=h).json()
    assert again["created"] is False
    assert client.get("/datasets").json()[0]["name"] == "toy"
    assert client.get("/info").json()["datasets"][0]["sha256"] == ds["sha256"]
    assert client.get("/datasets/available").status_code == 401
    assert client.get("/datasets/available", headers=h).json() == [{"file_name": "t.csv", "size_bytes": len(CSV)}]
    assert client.get("/audit/verify").json()["entries"] == 3


def test_ingest_changed_content_conflicts(client: TestClient, settings: Settings) -> None:
    h = _auth(client)
    f = settings.data_dir / "t.csv"
    f.write_text(CSV, encoding="utf-8")
    client.post("/datasets/ingest", json=_body(), headers=h)
    f.write_text(CSV + "d,2020-01-04,0,9,g1\n", encoding="utf-8")
    assert client.post("/datasets/ingest", json=_body(), headers=h).status_code == 409


@pytest.mark.parametrize("name", ["../t.csv", "..%2Ft.csv", "/etc/passwd", "sub/t.csv", "t.txt"])
def test_ingest_rejects_path_traversal(client: TestClient, settings: Settings, name: str) -> None:
    (settings.data_dir.parent / "t.csv").write_text(CSV, encoding="utf-8")
    h = _auth(client)
    assert client.post("/datasets/ingest", json=_body(file_name=name), headers=h).status_code == 422


def test_ingest_symlink_escape_blocked(client: TestClient, settings: Settings, tmp_path: Path) -> None:
    secret = tmp_path / "outside.csv"
    secret.write_text(CSV, encoding="utf-8")
    (settings.data_dir / "link.csv").symlink_to(secret)
    h = _auth(client)
    assert client.post("/datasets/ingest", json=_body(file_name="link.csv"), headers=h).status_code == 404


def test_ingest_bad_inputs(client: TestClient, settings: Settings) -> None:
    h = _auth(client)
    (settings.data_dir / "t.csv").write_text(CSV, encoding="utf-8")
    assert client.post("/datasets/ingest", json=_body(label_column="nope"), headers=h).status_code == 422
    assert client.post("/datasets/ingest", json=_body(label_column="grp"), headers=h).status_code == 422
    assert client.post("/datasets/ingest", json=_body(source_url="javascript:alert(1)"), headers=h).status_code == 422
    assert client.post("/datasets/ingest", json=_body(file_name="missing.csv"), headers=h).status_code == 404
    (settings.data_dir / "dup.csv").write_text("id,y\na,1\na,0\n", encoding="utf-8")
    assert client.post("/datasets/ingest", json=_body(file_name="dup.csv", time_column=None, group_column=None), headers=h).status_code == 422
    assert client.get("/datasets").json() == []  # failed ingests leave nothing behind


def test_runs_endpoints_empty(client: TestClient) -> None:
    assert client.get("/runs").json() == []
    assert client.get("/runs/comparison").json() == []
    assert client.get("/runs?limit=0").status_code == 422


def test_migration_runner(tmp_path: Path) -> None:
    d = tmp_path / "db"
    (d / "migrations").mkdir(parents=True)
    (d / "schema.sql").write_text("PRAGMA foreign_keys = ON;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\n")
    (d / "migrations" / "0002_add_b.sql").write_text("CREATE TABLE b (id INTEGER PRIMARY KEY);\n")
    c = db.connect(tmp_path / "m.sqlite3")
    assert db.migrate(c, d) == 2
    assert db.migrate(c, d) == 2  # idempotent
    assert [r["version"] for r in c.execute("SELECT version FROM schema_version ORDER BY version")] == [1, 2]
    (d / "migrations" / "0002_add_b.sql").write_text("CREATE TABLE b (id INTEGER, extra TEXT);\n")
    with pytest.raises(db.MigrationError, match="modified"):
        db.migrate(c, d)


def test_failed_migration_rolls_back(tmp_path: Path) -> None:
    d = tmp_path / "db"
    (d / "migrations").mkdir(parents=True)
    (d / "schema.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);\n")
    (d / "migrations" / "0002_bad.sql").write_text("CREATE TABLE b (id INTEGER);\nCREATE TABLE a (id INTEGER);\n")
    c = db.connect(tmp_path / "m.sqlite3")
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(c, d)
    tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "b" not in tables
    assert c.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"] == 1


def test_health_deep_reports_each_check(client: TestClient, settings: Settings) -> None:
    """Empty DB: schema at head but no datasets -> 503 fail; every check carries its evidence."""
    r = client.get("/health/deep")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "fail" and body["version"]
    checks = body["checks"]
    assert checks["db"]["ok"] and checks["db"]["journal_mode"].lower() == "wal" and checks["db"]["busy_timeout_ms"] >= 5000
    assert checks["migrations"]["ok"] and checks["migrations"]["applied"] == checks["migrations"]["head"] == LATEST
    assert checks["datasets"]["ok"] is False and checks["datasets"]["regions"] == 0
    assert set(checks["ollama"]) >= {"ok", "url", "reachable"}
    assert checks["last_agent_run"] == {"ok": True, "run": None}
    from app import cli
    env = {"KCF_SECRET_KEY": TEST_SECRET, "KCF_DB_PATH": str(settings.db_path), "KCF_DATA_DIR": str(settings.data_dir)}
    assert cli.main(["preflight"], env) == 1  # the self-test fails loudly on a demo box without data
