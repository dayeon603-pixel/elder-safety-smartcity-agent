"""Operator commands.

`python -m app.cli seed-admin --email you@example.com` (password from KCF_ADMIN_PASSWORD or a prompt)
`python -m app.cli preflight [--warm-up]` startup self-test for `make demo`: migrations at head,
datasets present, Ollama reachable with the planner model (optionally preloaded). Exit 1 on failure.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import re
import sqlite3
import sys
from collections.abc import Mapping, Sequence

from . import db, ops, repo
from .actions import warm as actions_warm
from .agent import constants as agent_constants
from .config import ConfigError, Settings, load_settings
from .schemas import EMAIL_RE, MIN_PASSWORD_LEN
from .security import audit, passwords

logger = logging.getLogger(__name__)
ADMIN_PASSWORD_ENV = "KCF_ADMIN_PASSWORD"


def seed_admin(conn: sqlite3.Connection, settings: Settings, email: str, password: str) -> int:
    """Create the admin account, or promote an existing user; return the user id.

    Raises:
        ValueError: if the email or password fails the same rules as registration.
    """
    if not re.fullmatch(EMAIL_RE, email):
        raise ValueError("invalid email address")
    if not MIN_PASSWORD_LEN <= len(password):
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    pw_hash = passwords.hash_password(password)
    with db.transaction(conn):
        existing = repo.get_user_by_email(conn, email)
        if existing is None:
            user = repo.create_user(conn, email, pw_hash, "admin")
            action = "admin.seed_created"
        else:
            user = existing
            repo.set_role(conn, int(existing["id"]), "admin")
            repo.update_password_hash(conn, int(existing["id"]), pw_hash)
            action = "admin.seed_promoted"
        audit.append(conn, settings.audit_key, "cli", action, {"user_id": int(user["id"])})
    return int(user["id"])


def preflight(settings: Settings, warm: bool, with_vlm: bool) -> int:
    """Run migrations, then the deep health checks; optionally preload the planner (and VLM).

    Returns 0 when the demo can run (status ok or degraded without KCF_REQUIRE_OLLAMA), else 1.
    """
    conn = db.connect(settings.db_path)
    try:
        logger.info("schema version %d at %s", db.migrate(conn), settings.db_path)
    finally:
        conn.close()
    for name, res in actions_warm.warm(settings.data_dir).items():  # parsed source tables -> disk cache
        logger.log(logging.INFO if "ms" in res else logging.ERROR, "actions table %-10s %s", name, res)
    if warm:
        models = (agent_constants.PLANNER_MODEL,) + ((agent_constants.VLM_MODEL,) if with_vlm else ())
        for model, ms in ops.warm_up(models).items():
            logger.info("warm-up %s: %s", model, "FAILED" if ms is None else f"{ms:.0f} ms")
    report = ops.deep_health(settings)
    for name, check in report["checks"].items():
        level = logging.INFO if check["ok"] else logging.ERROR
        logger.log(level, "%-16s %s %s", name, "ok " if check["ok"] else "BAD",
                   json.dumps({k: v for k, v in check.items() if k != "ok"}, ensure_ascii=False))
    logger.log(logging.INFO if report["status"] != ops.STATUS_FAIL else logging.ERROR,
               "preflight status: %s", report["status"])
    return 0 if report["status"] != ops.STATUS_FAIL else 1


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("seed-admin", help="create or promote the admin account")
    p.add_argument("--email", required=True)
    pf = sub.add_parser("preflight", help="startup self-test (migrations, datasets, Ollama)")
    pf.add_argument("--warm-up", action="store_true", help="preload the planner model with keep_alive")
    pf.add_argument("--vlm", action="store_true", help="also preload the VLM")
    args = parser.parse_args(argv)
    environ = os.environ if env is None else env
    try:
        settings = load_settings(environ)
        if args.cmd == "preflight":
            return preflight(settings, args.warm_up, args.vlm)
        password = environ.get(ADMIN_PASSWORD_ENV) or getpass.getpass("Admin password: ")
        conn = db.connect(settings.db_path)
        try:
            db.migrate(conn)
            uid = seed_admin(conn, settings, args.email.strip(), password)
        finally:
            conn.close()
    except (ConfigError, ValueError) as exc:
        logger.error("%s", exc)
        return 2
    logger.info("admin account ready: user id %d", uid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
