"""Deterministic retrieval metrics — no LLM, no network, no database.

Why deterministic metrics?
──────────────────────────
The product's core promise is that an answer is grounded in a real clause from
the right document. The cleanest, cheapest, and most *reproducible* way to test
that is to ask: for a question whose gold answer span is known, did the hybrid
retriever return a chunk that (a) comes from the correct contract and (b)
contains that span — and how highly was it ranked?

That question is answered with plain string matching, so these metrics are:
  • Free and fast (no judge-LLM calls), so they can run on every commit.
  • Deterministic, so a metric change always reflects a code/config change.
  • Unit-testable in CI with hand-written fixtures.

Metrics
───────
  Hit@k : 1 if a relevant chunk appears in the top-k results, else 0.
          Averaged over the eval set, this is "recall" — the share of questions
          for which we surfaced the answer passage at all within k results.
  MRR   : Mean Reciprocal Rank. 1/rank of the first relevant chunk (0 if none).
          Rewards ranking the right clause *near the top*, not just somewhere.

Matching heuristic
───────────────────
A CUAD gold span is the lawyer-highlighted answer text. A retrieved chunk counts
as relevant to a query when it is from the right contract AND the normalised
head of any gold span (first ``NEEDLE_CHARS`` characters) appears verbatim
inside the normalised chunk. Matching on the head — rather than requiring the
whole span — tolerates the chunker splitting a long clause across boundaries,
while the contract-id check prevents a generic phrase in the wrong document from
counting as a hit (a real cross-document retrieval test).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A span shorter than this after normalisation is too generic to match on
# (e.g. a one-word "Yes"); such gold spans are skipped.
MIN_SPAN_CHARS: int = 8

# We match on the first N normalised characters of a gold span. Long clauses get
# split by the chunker; the head is the most identifying, least boilerplate part.
NEEDLE_CHARS: int = 80

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lower-case, strip punctuation to spaces, and collapse whitespace.

    Makes matching robust to the cosmetic differences (case, line breaks,
    stray punctuation) between a CUAD highlight and our extracted chunk text.
    """
    lowered = text.lower().replace("\xa0", " ")
    return _WHITESPACE.sub(" ", _NON_ALNUM.sub(" ", lowered)).strip()


def span_matches(gold_span: str, chunk_text: str) -> bool:
    """Return True if ``chunk_text`` appears to contain ``gold_span``.

    Uses the normalised head of the span (see module docstring). Spans that are
    too short to be discriminating are never considered a match.
    """
    needle = normalize(gold_span)
    if len(needle) < MIN_SPAN_CHARS:
        return False
    return needle[:NEEDLE_CHARS] in normalize(chunk_text)


@dataclass
class RetrievedChunk:
    """The minimal, pipeline-agnostic view of a retrieved chunk a metric needs.

    Decoupled from the app's ``RawChunkResult`` so ``metrics`` stays free of
    application imports and can be unit-tested in isolation.
    """

    source_filename: str
    content: str


def first_relevant_rank(
    *,
    gold_spans: list[str],
    contract_id: str,
    retrieved: list[RetrievedChunk],
) -> int | None:
    """1-based rank of the first retrieved chunk relevant to the query.

    Relevant := from the expected contract AND containing a gold span. Returns
    ``None`` when no retrieved chunk is relevant.
    """
    for rank, chunk in enumerate(retrieved, start=1):
        if chunk.source_filename != contract_id:
            continue
        if any(span_matches(span, chunk.content) for span in gold_spans):
            return rank
    return None


def hit_at_k(rank: int | None, k: int) -> int:
    """1 if a relevant chunk was found at or above rank ``k``, else 0."""
    return 1 if rank is not None and rank <= k else 0


def reciprocal_rank(rank: int | None) -> float:
    """1/rank of the first relevant chunk, or 0.0 if none was found."""
    return 1.0 / rank if rank is not None else 0.0


@dataclass
class QueryScore:
    """The scored outcome of evaluating a single question."""

    contract_id: str
    category: str
    question: str
    n_gold_spans: int
    first_rank: int | None
    hits: dict[int, int] = field(default_factory=dict)
    reciprocal_rank: float = 0.0


def score_query(
    *,
    contract_id: str,
    category: str,
    question: str,
    gold_spans: list[str],
    retrieved: list[RetrievedChunk],
    k_values: list[int],
) -> QueryScore:
    """Score one question against its retrieved chunks for every k in ``k_values``."""
    rank = first_relevant_rank(
        gold_spans=gold_spans, contract_id=contract_id, retrieved=retrieved
    )
    return QueryScore(
        contract_id=contract_id,
        category=category,
        question=question,
        n_gold_spans=len(gold_spans),
        first_rank=rank,
        hits={k: hit_at_k(rank, k) for k in k_values},
        reciprocal_rank=reciprocal_rank(rank),
    )


def aggregate(scores: list[QueryScore], k_values: list[int]) -> dict[str, float]:
    """Average per-query scores into the headline metrics logged to MLflow.

    Returns a flat dict, e.g. ``{"hit@1": 0.62, "hit@3": 0.81, "mrr": 0.55,
    "n_questions": 72.0}``. Returns zeros (not a crash) for an empty eval set.
    """
    n = len(scores)
    if n == 0:
        metrics = {f"hit@{k}": 0.0 for k in k_values}
        metrics["mrr"] = 0.0
        metrics["n_questions"] = 0.0
        return metrics

    metrics = {
        f"hit@{k}": sum(s.hits.get(k, 0) for s in scores) / n for k in k_values
    }
    metrics["mrr"] = sum(s.reciprocal_rank for s in scores) / n
    metrics["n_questions"] = float(n)
    return metrics
