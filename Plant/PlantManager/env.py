"""Load project .env into process environment (does not override existing vars)."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_plant_env() -> Path | None:
    """Load Plant/.env if present. Returns the path loaded, or None."""
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)
        return env_path
    load_dotenv(override=False)
    return None
