"""Load the repo-root ``.env`` into the process environment.

The application's ``Settings`` reads ``.env`` relative to the current working
directory. The project's ``.env`` lives at the repo root, but the eval scripts
are run as ``python -m evaluation.<x>`` from the ``backend/`` directory, so that
lookup would miss it. This module finds the repo-root ``.env`` explicitly and
populates ``os.environ`` (without overwriting anything already set), so the eval
can be run from anywhere and still pick up DB credentials and API keys.

Import and call ``load_repo_env()`` BEFORE importing ``app.*`` modules, because
``app.core.config`` instantiates ``Settings`` at import time.
"""

from __future__ import annotations

import os
from pathlib import Path

# evaluation/env_bootstrap.py -> evaluation -> backend -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_repo_env(env_path: Path | None = None) -> Path | None:
    """Set environment variables from the repo-root ``.env`` file.

    Existing environment variables win (so an explicit override on the command
    line is honoured). Returns the path that was loaded, or ``None`` if no
    ``.env`` file was found (the app may still get its config from real env vars).
    """
    path = env_path or (REPO_ROOT / ".env")
    if not path.is_file():
        return None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)
    return path
