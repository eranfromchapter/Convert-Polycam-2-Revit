"""Settings + env loading shared by every module.

Secrets come from .env / the environment only. config/settings.json holds
non-secret knobs (folder names, endpoint paths, export formats).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO_ROOT / "config" / "settings.json"

load_dotenv(REPO_ROOT / ".env")


@lru_cache(maxsize=1)
def settings() -> dict:
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


def state_dir() -> Path:
    d = REPO_ROOT / settings()["state"]["dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path(key: str) -> Path:
    """Resolve a state file path from settings (e.g. 'manifest')."""
    p = REPO_ROOT / settings()["state"][key]
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in "
            f"(see docs/DEV_QUESTIONS.md for values the dev must provide)."
        )
    return val
