"""Pytest bootstrap for the backend test suite.

Runs before any test module is imported, so it must do two things up front:

1. Put the ``backend/`` directory on ``sys.path`` so ``app`` and ``evaluation``
   import regardless of where pytest is invoked from.

2. Provide dummy values for the settings that ``app.core.config.Settings``
   requires at import time. The unit tests exercise pure functions and never
   open a database connection or call an API, so these values only need to
   exist — they do not need to be real. This is what lets the full suite run in
   CI with no Postgres, no MinIO, and no API keys.
"""

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Non-"changethis" values so Settings validation neither warns nor raises.
_DUMMY_ENV = {
    "ENVIRONMENT": "local",
    "POSTGRES_SERVER": "localhost",
    "POSTGRES_PORT": "5432",
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test-password",
    "POSTGRES_DB": "test_db",
    "FIRST_SUPERUSER": "test@example.com",
    "FIRST_SUPERUSER_PASSWORD": "test-password",
    "MINIO_SECRET_KEY": "test-secret",
    "SECRET_KEY": "test-secret-key",
}
for key, value in _DUMMY_ENV.items():
    os.environ.setdefault(key, value)
