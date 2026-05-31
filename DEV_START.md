# Developer Quick Start

Use these steps for a clean local run.

## 1. Stop Old Containers

```bash
docker compose down
```

## 2. Start Backend Services

```bash
docker compose up -d
```

## 3. Re-run Initialization

```bash
docker compose exec backend python -m app.initial_data
```

This re-checks the initial user setup and MinIO bucket/CORS setup.

## 4. Start Frontend

```bash
cd frontend
npm install
npm run dev
```

Workbench: `http://localhost:5173`

## Troubleshooting

- If ports `5173`, `8000`, `9000`, or `5432` are occupied, stop old local
  processes and run `docker compose down`.
- If PDF loading fails after services restart, run
  `docker compose exec backend python -m app.initial_data` again.
