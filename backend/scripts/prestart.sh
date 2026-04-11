#!/usr/bin/env bash
# ============================================================
# Pre-start script — runs before uvicorn starts.
# Called by docker-compose command for both backend and
# celery-worker services.
# ============================================================
set -e

echo "⏳ Running Alembic migrations..."
alembic upgrade head
echo "✅ Migrations complete."

echo "⏳ Creating initial data (superuser)..."
python -m app.initial_data
echo "✅ Initial data ready."
