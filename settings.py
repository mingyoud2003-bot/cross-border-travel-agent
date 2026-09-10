from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load_local_env() -> None:
    """Load local development variables without logging their values."""

    env_file = ROOT / ".env.local"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def api_key_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def session_db_path() -> str:
    configured = os.environ.get("SESSION_DB_PATH", "").strip()
    return configured or str(ROOT / ".data" / "travel-agent.db")


def rate_limit_per_minute() -> int:
    raw = os.environ.get("RATE_LIMIT_PER_MINUTE", "20")
    try:
        return max(1, int(raw))
    except ValueError:
        return 20
