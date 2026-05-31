"""Run the retrieval eval and log the experiment to MLflow.

For each question in the eval set this embeds the question, runs the real hybrid
retriever (pgvector + Postgres FTS + RRF) scoped to the eval tenant across ALL
its documents, and scores whether the gold clause was surfaced and how highly.

Every run is recorded to MLflow as one experiment run:
  • params  = the RAG configuration that produced the result (chunk size, top-k,
    RRF k, embedding model, ...). Read live from the code, so changing a knob
    and re-running yields a directly comparable run.
  • metrics = Hit@k and MRR over the eval set.
  • artifacts = per-question and per-category breakdowns for inspection.

Run AFTER ``corpus.py``:
    python -m evaluation.run_eval --run-name baseline
    python -m evaluation.run_eval --run-name "chunk-256" --k 1,3,5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

import mlflow

from evaluation.env_bootstrap import REPO_ROOT, load_repo_env

# Populate DB credentials / API keys before any ``app.*`` import triggers Settings.
load_repo_env()

from sqlalchemy import text

from app.core.config import settings
from app.services import chunker, retrieval
from app.services.embeddings import generate_embeddings
from evaluation import db, metrics
from evaluation.dataset import DEFAULT_OUT, load_jsonl

DEFAULT_EXPERIMENT = "legal-rag-retrieval"
LAST_RUN_DIR = Path(__file__).resolve().parent / "data" / "last_run"

# Categories where the CUAD gold span is structurally unmatchable by the
# string-matching heuristic — not a retrieval failure, a measurement limit.
# Effective Date: the span encodes the heading label ("EFFECTIVE DATE") fused
# with its value ("JUNE 1, 1998") in the PDF highlight, but the text parser
# puts those on separate structural lines -> separate chunks -> needle never
# appears in a single chunk.  Excluded from the "matchable" headline metric.
MEASUREMENT_LIMITED: set[str] = {"Effective Date"}


def rag_config_params() -> dict[str, object]:
    """Snapshot the live RAG configuration to log as MLflow params."""
    return {
        "embedding_model": settings.EMBEDDING_MODEL,
        "embedding_dimensions": settings.EMBEDDING_DIMENSIONS,
        "max_chunk_tokens": chunker.MAX_CHUNK_TOKENS,
        "overlap_tokens": chunker.OVERLAP_TOKENS,
        "search_k_per_leg": retrieval.SEARCH_K_PER_LEG,
        "top_k_final": retrieval.TOP_K_FINAL,
        "rrf_k": retrieval.RRF_K,
        "match_needle_chars": metrics.NEEDLE_CHARS,
        "match_min_span_chars": metrics.MIN_SPAN_CHARS,
    }


async def _count_eval_chunks(session) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM document_chunk WHERE tenant_id = :tid"),
        {"tid": str(db.EVAL_TENANT_ID)},
    )
    return int(result.scalar_one())


async def run(eval_set_path: Path, k_values: list[int], limit: int | None) -> dict:
    records = load_jsonl(eval_set_path)
    if limit:
        records = records[:limit]
    if not records:
        raise SystemExit(f"Eval set {eval_set_path} is empty — run evaluation.dataset.")

    engine = db.make_engine()
    session_factory = db.make_session_factory(engine)
    scored: list[metrics.QueryScore] = []
    per_question_rows: list[dict] = []

    try:
        async with session_factory() as session:
            if await _count_eval_chunks(session) == 0:
                raise SystemExit(
                    "No chunks under the eval tenant. Run "
                    "`python -m evaluation.corpus` first to index the eval corpus."
                )

            # Embed all questions up front (the service batches internally).
            questions = [r["question"] for r in records]
            print(f"Embedding {len(questions)} questions...")
            question_embeddings = await generate_embeddings(questions)

            print("Running retrieval...")
            for record, embedding in zip(records, question_embeddings, strict=False):
                raw_chunks = await retrieval.hybrid_retrieve(
                    session,
                    query=record["question"],
                    query_embedding=embedding,
                    tenant_id=db.EVAL_TENANT_ID,
                    document_ids=None,  # all eval docs (true cross-doc discrimination)
                )
                retrieved = [
                    metrics.RetrievedChunk(
                        source_filename=c.original_filename, content=c.content
                    )
                    for c in raw_chunks
                ]
                score = metrics.score_query(
                    contract_id=record["contract_id"],
                    category=record["category"],
                    question=record["question"],
                    gold_spans=record["gold_spans"],
                    retrieved=retrieved,
                    k_values=k_values,
                )
                scored.append(score)
                per_question_rows.append(
                    {
                        "contract_id": score.contract_id,
                        "category": score.category,
                        "question": score.question,
                        "n_gold_spans": score.n_gold_spans,
                        "first_rank": score.first_rank,
                        "reciprocal_rank": round(score.reciprocal_rank, 4),
                        "hits": score.hits,
                        "top_retrieved": [
                            {
                                "rank": i + 1,
                                "filename": c.original_filename,
                                "page": c.page_number,
                                "section": c.section_title,
                                "snippet": c.content[:160],
                            }
                            for i, c in enumerate(raw_chunks)
                        ],
                    }
                )
    finally:
        await engine.dispose()

    summary = metrics.aggregate(scored, k_values)
    by_category = _aggregate_by_category(scored, k_values)
    matchable = [s for s in scored if s.category not in MEASUREMENT_LIMITED]
    summary_matchable = metrics.aggregate(matchable, k_values)
    return {
        "summary": summary,
        "summary_matchable": summary_matchable,
        "by_category": by_category,
        "per_question": per_question_rows,
        "n_contracts": len({s.contract_id for s in scored}),
    }


def _aggregate_by_category(
    scored: list[metrics.QueryScore], k_values: list[int]
) -> dict[str, dict[str, float]]:
    categories: dict[str, list[metrics.QueryScore]] = {}
    for score in scored:
        categories.setdefault(score.category, []).append(score)
    return {
        category: metrics.aggregate(items, k_values)
        for category, items in sorted(categories.items())
    }


def _write_artifacts(results: dict, params: dict, k_values: list[int]) -> Path:
    if LAST_RUN_DIR.exists():
        shutil.rmtree(LAST_RUN_DIR)
    LAST_RUN_DIR.mkdir(parents=True, exist_ok=True)

    (LAST_RUN_DIR / "summary.json").write_text(
        json.dumps(
            {
                "params": params,
                "k_values": k_values,
                "metrics": results["summary"],
                "metrics_matchable": results["summary_matchable"],
                "measurement_limited_categories": sorted(MEASUREMENT_LIMITED),
                "by_category": results["by_category"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    with (LAST_RUN_DIR / "per_question.jsonl").open("w", encoding="utf-8") as fh:
        for row in results["per_question"]:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return LAST_RUN_DIR


def _print_summary(results: dict, k_values: list[int]) -> None:
    summary = results["summary"]
    matchable = results["summary_matchable"]
    top_k = max(k_values)
    limited_note = ", ".join(sorted(MEASUREMENT_LIMITED))

    print("\n" + "=" * 60)
    print("RETRIEVAL EVAL RESULTS")
    print("=" * 60)
    print(f"Questions : {int(summary['n_questions'])}  "
          f"(matchable: {int(matchable['n_questions'])})")
    print(f"Contracts : {results['n_contracts']}")
    print(f"\n  {'Metric':<12} {'All':>6}  {'Matchable':>9}")
    print(f"  {'-'*12} {'------':>6}  {'---------':>9}")
    for k in k_values:
        key = f"hit@{k}"
        print(f"  {f'Hit@{k}':<12} {summary[key]:>6.3f}  {matchable[key]:>9.3f}")
    print(f"  {'MRR':<12} {summary['mrr']:>6.3f}  {matchable['mrr']:>9.3f}")
    print(f"\n  Matchable excludes: {limited_note}")
    print("  (structural limit: heading+value span straddles chunk boundary)")
    print("-" * 60)
    print(f"By category (Hit@{top_k}):")
    for category, cat_m in results["by_category"].items():
        flag = "  [measurement-limited]" if category in MEASUREMENT_LIMITED else ""
        print(
            f"  {cat_m[f'hit@{top_k}']:.2f}  "
            f"({int(cat_m['n_questions']):>2})  {category}{flag}"
        )
    print("=" * 60)


def main() -> None:
    # asyncpg requires the Selector event loop on Windows (the default Proactor
    # loop lacks the socket primitives it needs).
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = argparse.ArgumentParser(description="Run the retrieval eval -> MLflow.")
    parser.add_argument("--eval-set", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--run-name", default=None, help="MLflow run name")
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--k", default="1,3,6", help="comma-separated k values")
    parser.add_argument("--limit", type=int, default=None, help="cap #questions")
    args = parser.parse_args()

    # Retrieval returns TOP_K_FINAL chunks; k beyond that can never hit.
    k_values = sorted(
        {min(int(k), retrieval.TOP_K_FINAL) for k in args.k.split(",") if k.strip()}
    )

    results = asyncio.run(run(args.eval_set, k_values, args.limit))
    params = rag_config_params()
    artifact_dir = _write_artifacts(results, params, k_values)

    # A file:// URI is parsed reliably by MLflow on every OS (a bare Windows
    # path like "D:\..." can be misread as a URI scheme).
    tracking_uri = (REPO_ROOT / "mlruns").as_uri()
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(args.experiment)
    with mlflow.start_run(run_name=args.run_name):
        mlflow.log_params(params)
        mlflow.log_param("eval_set", args.eval_set.name)
        mlflow.log_param("n_contracts", results["n_contracts"])
        mlflow.log_param("k_values", ",".join(map(str, k_values)))
        # MLflow metric keys may not contain "@"; map hit@k -> hit_at_k.
        safe_metrics = {
            key.replace("@", "_at_"): value
            for key, value in results["summary"].items()
        }
        safe_matchable = {
            f"matchable_{key.replace('@', '_at_')}": value
            for key, value in results["summary_matchable"].items()
        }
        mlflow.log_metrics({**safe_metrics, **safe_matchable})
        mlflow.log_artifacts(str(artifact_dir), artifact_path="eval")

    _print_summary(results, k_values)
    print(f"\nArtifacts: {artifact_dir}")
    print(f"MLflow:    mlflow ui --backend-store-uri {tracking_uri}")


if __name__ == "__main__":
    main()
