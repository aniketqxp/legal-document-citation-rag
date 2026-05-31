"""CUAD-grounded evaluation harness for the legal-document-citation-rag pipeline.

This package measures *retrieval quality* — does the hybrid retriever surface
the contract clause that actually answers a question? — using the labelled
clause spans shipped with the CUAD v1 dataset as ground truth.

Layout
──────
    categories.py  CUAD clause category -> natural-language question map.
    dataset.py     Build an eval set (questions + gold spans) from CUAD.   [stdlib]
    metrics.py     Pure, deterministic retrieval metrics (Hit@k, MRR).     [stdlib]
    db.py          Eval-only DB engine + a dedicated, isolated eval tenant.
    corpus.py      Index a CUAD subset under the eval tenant (real pipeline).
    run_eval.py    Run retrieval over the eval set, score it, log to MLflow.

The two modules tagged ``[stdlib]`` carry no application or third-party imports,
so they can be unit-tested in CI without a database, API keys, or model files.
"""
