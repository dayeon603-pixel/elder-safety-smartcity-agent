"""Environment-driven settings. Secrets come from the environment only."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .security.kdf import derive_key

APP_ROOT = Path(__file__).resolve().parents[2]
MIN_SECRET_LEN = 32
AUDIT_KEY_INFO = b"kcf/audit-chain/v1"

DEFAULT_DB_PATH = APP_ROOT / "backend" / "var" / "kcf.sqlite3"
DEFAULT_DATA_DIR = APP_ROOT.parent / "data" / "cache"
DEFAULT_CORS = "http://localhost:5190,http://127.0.0.1:5190"
DEFAULT_FRONTEND_PORT = 5190
DEFAULT_TOKEN_TTL_S = 900
DEFAULT_RATE_CAPACITY = 60.0
DEFAULT_RATE_REFILL_PER_S = 20.0
DEFAULT_AUTH_RATE_CAPACITY = 10.0
DEFAULT_AUTH_RATE_REFILL_PER_S = 0.2
DEFAULT_MAX_CSV_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_CSV_ROWS = 500_000
DEFAULT_VERIFY_RATE_CAPACITY = 5.0
DEFAULT_VERIFY_RATE_REFILL_PER_S = 0.1
DEFAULT_AUDIT_VERIFY_TTL_S = 5.0
DEFAULT_AGENT_RATE_CAPACITY = 6.0
DEFAULT_AGENT_RATE_REFILL_PER_S = 0.5
DEFAULT_SIM_RATE_CAPACITY = 30.0
DEFAULT_SIM_RATE_REFILL_PER_S = 2.0
DEFAULT_MAX_SIM_BODY_BYTES = 64 * 1024
DEFAULT_MAX_AGENT_BODY_BYTES = 6 * 1024 * 1024
MIN_DEMO_TOKEN_LEN = 16
MIN_WEBHOOK_SECRET_LEN = 16
DEFAULT_WEBHOOK_TIMEOUT_S = 5.0
DEFAULT_WEBHOOK_ATTEMPTS = 3
# Writable side-caches default to the DB's directory (backend/var in production, the temp dir in tests), so a
# test database never shares snapshots or audio with the real one.
AUDIO_SUBDIR, MEDICAL_CACHE_SUBDIR = "audio", "medical"
DEFAULT_MEDICAL_DAILY_QUOTA = 900  # data.go.kr development keys allow 1,000 calls/day per service
MEDICAL_SNAPSHOT_SUBDIR = "extra/nmc_snapshots"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")
MIN_DISTINCT_SECRET_CHARS = 10
PLACEHOLDER_WORDS = (
    "changeme", "change-me", "change_me", "placeholder", "example", "secret", "password",
    "your-", "your_", "xxxx", "insert", "replace", "default", "test",
)


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable runtime configuration."""

    secret_key: bytes
    db_path: Path
    data_dir: Path
    token_ttl_s: int
    cors_origins: tuple[str, ...]
    rate_capacity: float
    rate_refill_per_s: float
    auth_rate_capacity: float
    auth_rate_refill_per_s: float
    max_csv_bytes: int
    max_csv_rows: int
    trust_proxy: bool
    audit_key: bytes
    admin_email: str | None
    verify_rate_capacity: float
    verify_rate_refill_per_s: float
    audit_verify_ttl_s: float
    dev: bool
    hsts: bool
    demo_token: str | None
    agent_self_url: str | None
    agent_rate_capacity: float
    agent_rate_refill_per_s: float
    sim_rate_capacity: float
    sim_rate_refill_per_s: float
    max_sim_body_bytes: int
    max_agent_body_bytes: int
    warmup: bool
    require_ollama: bool
    webhook_url: str | None
    webhook_secret: bytes | None
    webhook_timeout_s: float
    webhook_attempts: int
    audio_dir: Path
    data_go_kr_key: str | None
    medical_cache_dir: Path
    medical_snapshot_dir: Path
    medical_daily_quota: int
    rate_exempt_loopback: bool
    vworld_key: str | None  # V-World WMTS key (never logged, never sent to the browser)
    frontend_port: int  # where the Vite build is served (LAN URL for the phone terminal)


def _num(env: Mapping[str, str], key: str, default: float, *, allow_zero: bool = False) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc
    if not math.isfinite(value):
        raise ConfigError(f"{key} must be finite")
    if value < 0 or (value == 0 and not allow_zero):
        raise ConfigError(f"{key} must be > 0" if not allow_zero else f"{key} must be >= 0")
    return value


def _int(env: Mapping[str, str], key: str, default: int, minimum: int = 1) -> int:
    value = _num(env, key, float(default))
    if value != int(value) or int(value) < minimum:
        raise ConfigError(f"{key} must be an integer >= {minimum}, got {value!r}")
    return int(value)


def _bool(env: Mapping[str, str], key: str) -> bool:
    return env.get(key, "false").strip().lower() == "true"


def check_secret_strength(name: str, value: str) -> None:
    """Reject short, low-variety or placeholder-looking secrets.

    This is a sanity filter, not an entropy proof: it catches `aaaa...`, `changeme...` and
    copied example values, not every guessable string.

    Raises:
        ConfigError: if the secret is too short, has too few distinct characters, or
            contains a placeholder word.
    """
    if len(value) < MIN_SECRET_LEN:
        raise ConfigError(f"{name} must be set and >= {MIN_SECRET_LEN} chars (run `make env`)")
    if len(set(value)) < MIN_DISTINCT_SECRET_CHARS:
        raise ConfigError(f"{name} has fewer than {MIN_DISTINCT_SECRET_CHARS} distinct characters")
    lowered = value.lower()
    for word in PLACEHOLDER_WORDS:
        if word in lowered:
            raise ConfigError(f"{name} looks like a placeholder (contains {word!r})")


def check_loopback_url(name: str, value: str) -> str:
    """Accept only an http URL whose host is loopback (the agent may call nothing but itself).

    Raises:
        ConfigError: on any other scheme or host.
    """
    parts = urlsplit(value)
    if parts.scheme != "http" or parts.hostname is None or parts.hostname not in LOOPBACK_HOSTS:
        raise ConfigError(f"{name} must be http://127.0.0.1:<port> or http://localhost:<port>, got {value!r}")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ConfigError(f"{name} must not carry a path, query or fragment")
    return f"{parts.scheme}://{parts.netloc}"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from an environment mapping (defaults to os.environ).

    Raises:
        ConfigError: if KCF_SECRET_KEY is missing/short or a numeric value is invalid.
    """
    e = os.environ if env is None else env
    secret = e.get("KCF_SECRET_KEY", "")
    check_secret_strength("KCF_SECRET_KEY", secret)
    audit_key_raw = e.get("KCF_AUDIT_KEY", "")
    if audit_key_raw:
        check_secret_strength("KCF_AUDIT_KEY", audit_key_raw)
        if audit_key_raw == secret:
            raise ConfigError("KCF_AUDIT_KEY must differ from KCF_SECRET_KEY")
        audit_key = audit_key_raw.encode("utf-8")
    else:
        audit_key = derive_key(secret.encode("utf-8"), AUDIT_KEY_INFO)
    db_path = Path(e.get("KCF_DB_PATH", str(DEFAULT_DB_PATH)))
    admin_email = e.get("KCF_ADMIN_EMAIL", "").strip().lower() or None
    if admin_email is not None and "@" not in admin_email:
        raise ConfigError("KCF_ADMIN_EMAIL must be an email address")
    origins = tuple(o.strip() for o in e.get("KCF_CORS_ORIGINS", DEFAULT_CORS).split(",") if o.strip())
    if "*" in origins:
        raise ConfigError("KCF_CORS_ORIGINS must be an explicit allowlist, not '*'")
    demo_token = e.get("KCF_DEMO_TOKEN", "").strip() or None
    if demo_token is not None and len(demo_token) < MIN_DEMO_TOKEN_LEN:
        raise ConfigError(f"KCF_DEMO_TOKEN must be >= {MIN_DEMO_TOKEN_LEN} chars (or unset)")
    self_url_raw = e.get("KCF_AGENT_SELF_URL", "").strip()
    agent_self_url = check_loopback_url("KCF_AGENT_SELF_URL", self_url_raw) if self_url_raw else None
    webhook_url = e.get("KCF_WEBHOOK_URL", "").strip() or None
    webhook_secret_raw = e.get("KCF_WEBHOOK_SECRET", "").strip()
    if webhook_url is not None:
        parts = urlsplit(webhook_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ConfigError("KCF_WEBHOOK_URL must be an http(s) URL")
        if len(webhook_secret_raw) < MIN_WEBHOOK_SECRET_LEN:
            raise ConfigError(f"KCF_WEBHOOK_SECRET must be >= {MIN_WEBHOOK_SECRET_LEN} chars when KCF_WEBHOOK_URL is set")
    return Settings(
        secret_key=secret.encode("utf-8"),
        db_path=db_path,
        data_dir=Path(e.get("KCF_DATA_DIR", str(DEFAULT_DATA_DIR))),
        token_ttl_s=_int(e, "KCF_TOKEN_TTL_S", DEFAULT_TOKEN_TTL_S),
        cors_origins=origins,
        rate_capacity=_num(e, "KCF_RATE_CAPACITY", DEFAULT_RATE_CAPACITY),
        rate_refill_per_s=_num(e, "KCF_RATE_REFILL_PER_S", DEFAULT_RATE_REFILL_PER_S),
        auth_rate_capacity=_num(e, "KCF_AUTH_RATE_CAPACITY", DEFAULT_AUTH_RATE_CAPACITY),
        auth_rate_refill_per_s=_num(e, "KCF_AUTH_RATE_REFILL_PER_S", DEFAULT_AUTH_RATE_REFILL_PER_S),
        max_csv_bytes=_int(e, "KCF_MAX_CSV_BYTES", DEFAULT_MAX_CSV_BYTES),
        max_csv_rows=_int(e, "KCF_MAX_CSV_ROWS", DEFAULT_MAX_CSV_ROWS),
        trust_proxy=_bool(e, "KCF_TRUST_PROXY"),
        audit_key=audit_key,
        admin_email=admin_email,
        verify_rate_capacity=_num(e, "KCF_VERIFY_RATE_CAPACITY", DEFAULT_VERIFY_RATE_CAPACITY),
        verify_rate_refill_per_s=_num(e, "KCF_VERIFY_RATE_REFILL_PER_S", DEFAULT_VERIFY_RATE_REFILL_PER_S),
        audit_verify_ttl_s=_num(e, "KCF_AUDIT_VERIFY_TTL_S", DEFAULT_AUDIT_VERIFY_TTL_S, allow_zero=True),
        dev=e.get("KCF_ENV", "production").strip().lower() == "dev",
        hsts=_bool(e, "KCF_HSTS"),
        demo_token=demo_token,
        agent_self_url=agent_self_url,
        agent_rate_capacity=_num(e, "KCF_AGENT_RATE_CAPACITY", DEFAULT_AGENT_RATE_CAPACITY),
        agent_rate_refill_per_s=_num(e, "KCF_AGENT_RATE_REFILL_PER_S", DEFAULT_AGENT_RATE_REFILL_PER_S),
        sim_rate_capacity=_num(e, "KCF_SIM_RATE_CAPACITY", DEFAULT_SIM_RATE_CAPACITY),
        sim_rate_refill_per_s=_num(e, "KCF_SIM_RATE_REFILL_PER_S", DEFAULT_SIM_RATE_REFILL_PER_S),
        max_sim_body_bytes=_int(e, "KCF_MAX_SIM_BODY_BYTES", DEFAULT_MAX_SIM_BODY_BYTES),
        max_agent_body_bytes=_int(e, "KCF_MAX_AGENT_BODY_BYTES", DEFAULT_MAX_AGENT_BODY_BYTES),
        warmup=_bool(e, "KCF_WARMUP"),
        require_ollama=_bool(e, "KCF_REQUIRE_OLLAMA"),
        webhook_url=webhook_url,
        webhook_secret=webhook_secret_raw.encode("utf-8") if webhook_url is not None else None,
        webhook_timeout_s=_num(e, "KCF_WEBHOOK_TIMEOUT_S", DEFAULT_WEBHOOK_TIMEOUT_S),
        webhook_attempts=_int(e, "KCF_WEBHOOK_ATTEMPTS", DEFAULT_WEBHOOK_ATTEMPTS),
        audio_dir=Path(e.get("KCF_AUDIO_DIR", str(db_path.parent / AUDIO_SUBDIR))),
        data_go_kr_key=e.get("KCF_DATA_GO_KR_KEY", "").strip() or None,
        medical_cache_dir=Path(e.get("KCF_MEDICAL_CACHE_DIR", str(db_path.parent / MEDICAL_CACHE_SUBDIR))),
        medical_snapshot_dir=Path(e.get("KCF_MEDICAL_SNAPSHOT_DIR",
                                        str(Path(e.get("KCF_DATA_DIR", str(DEFAULT_DATA_DIR))) / MEDICAL_SNAPSHOT_SUBDIR))),
        medical_daily_quota=_int(e, "KCF_MEDICAL_DAILY_QUOTA", DEFAULT_MEDICAL_DAILY_QUOTA),
        # Demo browsers on this laptop must never see 429s on read routes: in demo/dev mode (KCF_ENV=dev, or `make demo`
        # which sets KCF_RATE_EXEMPT_LOOPBACK=true) loopback GET/HEAD skip the limiter. Never behind a trusted proxy
        # (every request would arrive from loopback). Production default stays strict.
        vworld_key=e.get("VWORLD_KEY", "").strip() or None,
        frontend_port=_int(e, "KCF_FRONTEND_PORT", DEFAULT_FRONTEND_PORT),
        rate_exempt_loopback=(_bool(e, "KCF_RATE_EXEMPT_LOOPBACK") if e.get("KCF_RATE_EXEMPT_LOOPBACK", "").strip()
                              else e.get("KCF_ENV", "production").strip().lower() == "dev") and not _bool(e, "KCF_TRUST_PROXY"),
    )
