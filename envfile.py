"""Read .env from the repository root into the environment.

Not python-dotenv, because this is twenty lines and one fewer dependency in a
CI job. Real environment variables always win, so a GitHub Actions secret is
never shadowed by a stale value in a file that should not be there anyway.
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _find() -> Path | None:
    """Look for .env beside this file and in the directories above it.

    The same tree is used as a subdirectory of a larger repository and as the
    root of its own, so the file is one level up in one layout and alongside
    in the other. Searching upward covers both without a flag to get wrong.
    """
    for directory in (HERE, *HERE.parents):
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
        if (directory / ".git").exists():
            break
    return None


def load(path: Path | None = None) -> int:
    env = path or _find()
    if env is None or not env.exists():
        return 0
    loaded = 0
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded
