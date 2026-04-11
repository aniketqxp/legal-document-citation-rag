# ── Stage 1: dependency installer ─────────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /build

# System libs needed to build asyncpg, pdfplumber native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ── Stage 2: runtime image ─────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# curl: used by Docker healthcheck  |  libpq-dev: asyncpg runtime dependency
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy installed python packages from the deps stage
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application source (bind-mounted at runtime in dev via docker-compose.override.yml)
COPY . .

RUN chmod +x scripts/prestart.sh

EXPOSE 8000

# Production default: run the app (override.yml adds prestart + --reload in dev)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
