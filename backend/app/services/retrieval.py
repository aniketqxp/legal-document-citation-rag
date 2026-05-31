"""Hybrid retrieval service — pgvector + Postgres FTS + in-memory RRF.

Architecture: Approach C (Approved in Phase 4 Review)
─────────────────────────────────────────────────────
Legal queries require BOTH:
  • Semantic (vector) matching: captures conceptual similarity.
    "What are the payment obligations?" will find "The licensee shall
     remit fees…" even without keyword overlap.
  • Keyword (FTS) matching: captures exact terms, proper nouns, clause
    identifiers. "Schedule A" or "Cybergy Holdings" would score 0 in a
    pure semantic search.

We execute both queries inside a single Postgres round-trip (using a
UNION-style approach via two async queries run concurrently), then fuse
the ranked lists in Python memory using Reciprocal Rank Fusion (RRF).

Reciprocal Rank Fusion
──────────────────────
RRF score = Σ  1 / (k + rank_i)   for each list i that contains the item.

A constant k=60 is the standard value from the original Cormack (2009)
paper. It prevents the top-ranked item from completely dominating when
one search mode returns a very high-confidence result.

Tenant Isolation
────────────────
EVERY query function in this module accepts ``tenant_id`` as a mandatory
keyword-only argument. tenant_id is injected into BOTH the semantic
WHERE clause AND the FTS WHERE clause. There is no code path through
which a chunk from another tenant can appear in results.

FTS Strategy
────────────
We search over the CONCATENATED string of   section_title || ' ' || content
(both columns included via to_tsvector). This is critical because the
section_title is stripped from the stored `content` column (Phase 3 design),
so a keyword search for "ARTICLE II DEFINITIONS" would miss all chunks if
we only searched `content`. The on-the-fly concatenation approach avoids
a schema migration for an MVP; it carries a small per-query overhead at
the document volumes we expect.
"""

# from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Number of candidates fetched from each individual search leg
# before RRF fusion down-selects to TOP_K_FINAL.
SEARCH_K_PER_LEG: int = 20

# Final number of chunks passed to the LLM after RRF fusion.
TOP_K_FINAL: int = 6

# RRF ranking constant (Cormack 2009).  Lower k → top ranks dominate more.
RRF_K: int = 60


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class RawChunkResult:
    """A chunk returned from one search leg before RRF fusion."""
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    original_filename: str
    page_number: int
    section_title: str | None
    content: str
    rank: int   # 1-based rank within its search leg


# ── Public API ────────────────────────────────────────────────────────────────

async def hybrid_retrieve(
    session: AsyncSession,
    *,
    query: str,
    query_embedding: list[float],
    tenant_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None = None,
) -> list[RawChunkResult]:
    """Run hybrid retrieval and return top-K chunks ranked by RRF.

    Args:
        session:        Async DB session.
        query:          Raw user query string (for FTS).
        query_embedding: Pre-computed embedding of the query (for pgvector).
        tenant_id:      MANDATORY — scopes BOTH search legs to one tenant.
        document_ids:   Optional list of document UUIDs to restrict search to.
                        If None, all tenant documents are searched.

    Returns:
        List of RawChunkResult objects ordered by descending RRF score,
        capped at TOP_K_FINAL.
    """
    # Run both legs sequentially to avoid 'another operation in progress' error
    # on the single async session object.
    semantic_results = await _semantic_search(
        session,
        query_embedding=query_embedding,
        tenant_id=tenant_id,
        document_ids=document_ids,
    )
    fts_results = await _fts_search(
        session,
        query=query,
        tenant_id=tenant_id,
        document_ids=document_ids,
    )

    # Fuse results in Python memory
    fused = _reciprocal_rank_fusion(semantic_results, fts_results)

    logger.info(
        "Hybrid retrieve: semantic=%d fts=%d fused→top%d (tenant=%s)",
        len(semantic_results),
        len(fts_results),
        TOP_K_FINAL,
        tenant_id,
    )

    return fused[:TOP_K_FINAL]


# ── Private: semantic search leg ──────────────────────────────────────────────

async def _semantic_search(
    session: AsyncSession,
    *,
    query_embedding: list[float],
    tenant_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None,
) -> list[RawChunkResult]:
    """Approximate KNN cosine similarity search via pgvector.

    Uses the ``<=>`` cosine distance operator with a brute-force sequential
    scan (no HNSW index at MVP scale — exact search, zero index overhead).

    The tenant_id filter is applied BEFORE the ORDER BY so Postgres can
    prune non-tenant rows without computing distances for them.
    """
    doc_filter = ""
    params: dict = {
        "embedding": str(query_embedding),
        "tenant_id": str(tenant_id),
        "limit": SEARCH_K_PER_LEG,
    }

    if document_ids:
        # Build a comma-delimited list of cast UUIDs for the IN clause.
        doc_filter = "AND dc.document_id = ANY(:doc_ids)"
        params["doc_ids"] = [str(d) for d in document_ids]

    sql = text(f"""
        SELECT
            dc.id            AS chunk_id,
            dc.document_id,
            d.original_filename,
            dc.page_number,
            dc.section_title,
            dc.content
        FROM document_chunk dc
        JOIN document d ON d.id = dc.document_id
        WHERE dc.tenant_id = :tenant_id\\:\\:uuid
          AND dc.embedding IS NOT NULL
          {doc_filter}
        ORDER BY dc.embedding <=> :embedding\\:\\:vector
        LIMIT :limit
    """)

    result = await session.execute(sql, params)
    rows = result.fetchall()

    return [
        RawChunkResult(
            chunk_id=uuid.UUID(str(row.chunk_id)),
            document_id=uuid.UUID(str(row.document_id)),
            original_filename=row.original_filename,
            page_number=row.page_number,
            section_title=row.section_title,
            content=row.content.replace('\xa0', ' '),
            rank=rank,
        )
        for rank, row in enumerate(rows, start=1)
    ]


# ── Private: FTS search leg ────────────────────────────────────────────────────

async def _fts_search(
    session: AsyncSession,
    *,
    query: str,
    tenant_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None,
) -> list[RawChunkResult]:
    """Postgres Full-Text Search over section_title + content.

    Uses ``plainto_tsquery`` (handles natural language; no Tsquery syntax
    needed from the user) against an on-the-fly tsvector built from the
    concatenation of section_title and content.

    Ranking uses ``ts_rank_cd`` (Cover Density ranking) which rewards
    documents where query terms appear near each other — well-suited to
    dense legal prose.
    """
    doc_filter = ""
    params: dict = {
        "query": query,
        "tenant_id": str(tenant_id),
        "limit": SEARCH_K_PER_LEG,
    }

    if document_ids:
        doc_filter = "AND dc.document_id = ANY(:doc_ids)"
        params["doc_ids"] = [str(d) for d in document_ids]

    sql = text(f"""
        SELECT
            dc.id            AS chunk_id,
            dc.document_id,
            d.original_filename,
            dc.page_number,
            dc.section_title,
            dc.content,
            ts_rank_cd(
                to_tsvector('english',
                    translate(coalesce(dc.section_title, '') || ' ' || dc.content, chr(160), ' ')
                ),
                plainto_tsquery('english', :query)
            ) AS fts_rank
        FROM document_chunk dc
        JOIN document d ON d.id = dc.document_id
        WHERE dc.tenant_id = :tenant_id\\:\\:uuid
          AND to_tsvector('english',
                  translate(coalesce(dc.section_title, '') || ' ' || dc.content, chr(160), ' ')
              ) @@ plainto_tsquery('english', :query)
          {doc_filter}
        ORDER BY fts_rank DESC
        LIMIT :limit
    """)

    result = await session.execute(sql, params)
    rows = result.fetchall()

    return [
        RawChunkResult(
            chunk_id=uuid.UUID(str(row.chunk_id)),
            document_id=uuid.UUID(str(row.document_id)),
            original_filename=row.original_filename,
            page_number=row.page_number,
            section_title=row.section_title,
            content=row.content.replace('\xa0', ' '),
            rank=rank,
        )
        for rank, row in enumerate(rows, start=1)
    ]


# ── Private: Reciprocal Rank Fusion ──────────────────────────────────────────

def _reciprocal_rank_fusion(
    *ranked_lists: list[RawChunkResult],
) -> list[RawChunkResult]:
    """Fuse multiple ranked lists using RRF.

    RRF score = Σ  1 / (RRF_K + rank_i)

    A chunk appearing in rank 1 of the semantic list and rank 3 of the FTS
    list gets a combined score larger than a chunk that only appears in one list,
    even at rank 1 — ensuring genuine dual-match documents surface first.

    Returns:
        Results sorted by descending RRF score. Deduplication is handled
        by merging on chunk_id: only the highest-ranked occurrence from each
        list contributes to the score.
    """
    scores: dict[uuid.UUID, float] = {}
    best_result: dict[uuid.UUID, RawChunkResult] = {}

    for ranked_list in ranked_lists:
        for item in ranked_list:
            rrf_contribution = 1.0 / (RRF_K + item.rank)
            scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + rrf_contribution
            # Keep the result object from the list where it ranked highest
            if item.chunk_id not in best_result or \
               item.rank < best_result[item.chunk_id].rank:
                best_result[item.chunk_id] = item

    sorted_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)
    return [best_result[cid] for cid in sorted_ids]
