"""Unit tests for Reciprocal Rank Fusion in the retrieval service.

RRF is the core ranking logic that merges the semantic and keyword search legs.
It is a pure function, so it can be tested directly with hand-built ranked lists
— no database required.
"""

import uuid

from app.services.retrieval import RawChunkResult, _reciprocal_rank_fusion


def _chunk(rank: int, content: str = "x") -> RawChunkResult:
    return RawChunkResult(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        original_filename="doc.pdf",
        page_number=1,
        section_title=None,
        content=content,
        rank=rank,
    )


def test_fusion_rewards_chunks_appearing_in_both_legs():
    shared_id = uuid.uuid4()
    shared_in_semantic = _chunk(rank=2)
    shared_in_fts = _chunk(rank=1)
    shared_in_semantic.chunk_id = shared_id
    shared_in_fts.chunk_id = shared_id

    semantic_only = _chunk(rank=1)  # rank 1 in only one list
    fts_only = _chunk(rank=2)

    fused = _reciprocal_rank_fusion(
        [semantic_only, shared_in_semantic],
        [shared_in_fts, fts_only],
    )

    # The dual-list chunk outranks the single-list rank-1 chunk.
    assert fused[0].chunk_id == shared_id
    # Deduplicated: the shared chunk appears exactly once.
    assert [c.chunk_id for c in fused].count(shared_id) == 1
    # Every input chunk id is represented.
    assert len(fused) == 3


def test_fusion_keeps_the_highest_ranked_occurrence():
    shared_id = uuid.uuid4()
    low = _chunk(rank=5, content="low")
    high = _chunk(rank=1, content="high")
    low.chunk_id = shared_id
    high.chunk_id = shared_id

    fused = _reciprocal_rank_fusion([low], [high])

    assert len(fused) == 1
    # Keeps the better (lower number = higher) rank and its result object.
    assert fused[0].rank == 1
    assert fused[0].content == "high"


def test_fusion_orders_by_descending_score():
    a = _chunk(rank=1)
    b = _chunk(rank=2)
    c = _chunk(rank=3)
    fused = _reciprocal_rank_fusion([a, b, c])
    assert [chunk.rank for chunk in fused] == [1, 2, 3]
