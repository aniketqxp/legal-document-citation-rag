# Retrieval Evaluation Harness

Measures how well the hybrid retriever (pgvector + Postgres FTS + RRF) surfaces
the **right contract clause** for a question, using the labelled clause spans in
the **CUAD v1** dataset as ground truth. Every run is tracked as an **MLflow**
experiment so you can compare retrieval configurations over time.

It reuses the application's real `chunker`, `embeddings`, and `retrieval`
services — it does not reimplement the pipeline. All eval data is written under
a dedicated, isolated **eval tenant**, so it never touches your real documents.

```
CUAD master_clauses.csv ──► dataset.py ──► eval_set.jsonl   (questions + gold spans)
CUAD full_contract_txt  ──► corpus.py  ──► document_chunk    (indexed under eval tenant)
eval_set + corpus       ──► run_eval.py ──► MLflow run       (Hit@k, MRR, artifacts)
```

## What it measures

- **Hit@k** — share of questions for which a chunk from the correct contract
  containing the gold clause appears in the top-k retrieved results (recall).
- **MRR** — mean reciprocal rank of that first correct chunk (ranking quality).

Matching is deterministic string matching (no judge-LLM), so the numbers are
free, fast, and reproducible. See the module docstring in `metrics.py`.

> Note: the corpus is built from CUAD's clean reference text, not the PDF parser,
> so this isolates **retrieval** quality from **parser** quality (a separate
> stage). Chunking, embeddings, and retrieval are the real production code.

## Prerequisites

1. The stack is already running (`docker compose up -d`) — the harness only
   needs **Postgres**, which Docker publishes on `localhost:5432`.
2. Your repo-root `.env` has a working `OPENROUTER_API_KEY` (used to embed the
   corpus and the questions). The scripts load `.env` automatically.
3. The `CUAD_v1/` dataset is present at the repo root (it already is).

## One-time setup (eval virtual environment)

The eval reuses the app's libraries plus MLflow, in a standalone venv so it does
not disturb the container. From the `backend/` directory (PowerShell):

```powershell
python -m venv .venv-eval
.\.venv-eval\Scripts\Activate.ps1
pip install -r evaluation/requirements-eval.txt
```

## Run it (3 steps)

All commands run from `backend/` with the eval venv activated.

```powershell
# 1. Build the eval set from CUAD ground truth (writes data/eval_set.jsonl)
python -m evaluation.dataset --contracts 12 --per-contract 6

# 2. Index those contracts under the eval tenant (embeds via OpenRouter).
#    Idempotent: re-running resets the eval corpus first.
python -m evaluation.corpus

# 3. Run retrieval over every question, score it, and log to MLflow
python -m evaluation.run_eval --run-name baseline
```

Step 3 prints a summary table and writes a run to `../mlruns`. View it with:

```powershell
mlflow ui --backend-store-uri file:///$($PWD.Path -replace '\\','/')/../mlruns
# then open http://localhost:5000
```

(`run_eval.py` prints the exact `mlflow ui` command for your machine on exit.)

## Run an experiment (the point of MLflow)

The retrieval knobs are constants in the application code; `run_eval.py` logs
their live values as MLflow params, so changing one and re-running produces a
directly comparable run:

| Knob | Where |
| --- | --- |
| `MAX_CHUNK_TOKENS`, `OVERLAP_TOKENS` | `app/services/chunker.py` |
| `SEARCH_K_PER_LEG`, `TOP_K_FINAL`, `RRF_K` | `app/services/retrieval.py` |
| `EMBEDDING_MODEL` | `.env` |

Example — does a smaller chunk help?

```powershell
# edit MAX_CHUNK_TOKENS = 256 in app/services/chunker.py, then:
python -m evaluation.corpus                       # re-index with the new chunking
python -m evaluation.run_eval --run-name chunk-256
```

Open MLflow and compare `chunk-256` against `baseline` on `hit_at_3` / `mrr`.

## Tests & CI

The deterministic metric logic, RRF fusion, and citation parsing are covered by
unit tests in `backend/tests/`, which run on every push via
`.github/workflows/ci.yml` (lint + tests, no DB or API keys required):

```powershell
# from backend/, in an environment with dev deps installed:
pip install -r requirements-dev.txt
pytest -q
ruff check evaluation tests
```
