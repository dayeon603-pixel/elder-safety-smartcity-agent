"""Shared fixtures: isolated DB per test, app client, no real secrets."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings, load_settings
from app.main import create_app

TEST_SECRET = "9f2c71ab04de58c3a6b1f0e97d4c2385b6a1e0f7c9d3"  # random-looking, passes strength checks


@pytest.fixture(autouse=True)
def _isolated_actions_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parsed-table pickles go to the test's temp dir, never to backend/var/actions_cache."""
    monkeypatch.setenv("KCF_ACTIONS_CACHE_DIR", str(tmp_path / "actions_cache"))


@pytest.fixture
def audit_key(settings: Settings) -> bytes:
    return settings.audit_key


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "t.sqlite3")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "cache"
    data.mkdir()
    return load_settings({
        "KCF_SECRET_KEY": TEST_SECRET, "KCF_DB_PATH": str(tmp_path / "api.sqlite3"),
        "KCF_DATA_DIR": str(data), "KCF_RATE_CAPACITY": "1000", "KCF_RATE_REFILL_PER_S": "1000",
        "KCF_AUTH_RATE_CAPACITY": "1000", "KCF_AUTH_RATE_REFILL_PER_S": "1000",
        "KCF_VERIFY_RATE_CAPACITY": "1000", "KCF_VERIFY_RATE_REFILL_PER_S": "1000",
        "KCF_AUDIT_VERIFY_TTL_S": "0",
    })


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c
