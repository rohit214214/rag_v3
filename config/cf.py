"""
Central place for which dotenv file to load.

python-dotenv's load_dotenv() with no path only reads ".env" in cwd,
not ".env.example". This module loads from an explicit path list so
local values can live in cf.env (or CONFIG_DOTENV_FILE).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_loaded_from: Path | None = None


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("CONFIG_DOTENV_FILE", "").strip()
    if override:
        paths.append(Path(override).expanduser())
    paths.extend(
        [
            PROJECT_ROOT / "cf.env",
            PROJECT_ROOT / ".env",
            PROJECT_ROOT / ".env.example",
        ]
    )
    return paths


def load_config() -> Path | None:
    """
    Load the first existing env file in order (only one file is read).
    Idempotent: subsequent calls return the same path without reloading.

    Search order:
    1) Path in CONFIG_DOTENV_FILE (if set)
    2) PROJECT_ROOT / cf.env
    3) PROJECT_ROOT / .env
    4) PROJECT_ROOT / .env.example

    Note: python-dotenv's plain load_dotenv() only reads ".env" by default,
    not ".env.example" — this module makes the filename explicit.
    """
    global _loaded_from
    if _loaded_from is not None:
        return _loaded_from
    for path in _candidate_paths():
        if path.is_file():
            load_dotenv(path, override=True)
            _loaded_from = path.resolve()
            return _loaded_from
    return None


def loaded_dotenv_path() -> Path | None:
    """Path of the env file that was loaded, or None if none existed."""
    return _loaded_from
