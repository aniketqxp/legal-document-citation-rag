"""Semantic chunking service.

Takes a list of ``ParsedBlock`` objects from the parser and produces
``ChunkData`` objects that are ready for embedding and DB insertion.

Chunking strategy
─────────────────
1. **Section integrity first**: if a section's full text fits within
   ``MAX_CHUNK_TOKENS``, it becomes a single chunk. No splitting required.

2. **Sliding-window fallback**: if a section exceeds ``MAX_CHUNK_TOKENS``,
   it is split into overlapping sub-chunks. ``OVERLAP_TOKENS`` of context is
   preserved across boundaries so that a clause straddling a split point is
   still fully retrievable from either chunk.

3. **Section-title prepending** (user requirement, non-negotiable):
   Before the text is sent to OpenRouter for embedding, the section title is
   prepended in the format::

       [Section Title]
       <chunk body text>

   This is stored as ``embed_content`` — the string passed to the embedding
   API. The ``raw_text`` field holds the clean, unprefixed body text that the
   Phase 4 LLM will read as retrieved context (the prefix is redundant in the
   LLM prompt where the section is already labelled, and wastes context tokens).

   Design rationale: storing a separate ``embed_content`` vs ``raw_text`` is a
   standard "contextual retrieval" technique. The embedding captures rich
   semantic meaning (because of the header), while the LLM receives clean prose.

Token counting
──────────────
tiktoken ``cl100k_base`` is used throughout. This encoder matches
``text-embedding-3-small`` (the active embedding model) and the Gemini family,
ensuring token counts stored in the DB are reliable for Phase 4 context budgeting.

Non-Goals (MVP scope):
  - Sentence-boundary detection (requires spaCy or NLTK — adds latency).
  - Paragraph-aware merging of tiny adjacent blocks (marginal quality gain).
  - Cross-page section merging (would complicate page_number accuracy).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import tiktoken

from app.services.pdf_parser import ParsedBlock

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Maximum tokens per chunk.
# 512 is the sweet spot: large enough to contain a full contract clause,
# small enough that the embedding model accurately represents the content, and
# leaves room in a 4096-token LLM context window for 5+ retrieved chunks.
MAX_CHUNK_TOKENS: int = 512

# Overlap between consecutive sub-chunks of the same over-length section.
# 64 tokens ≈ 2-3 sentences of context preserved across boundaries.
OVERLAP_TOKENS: int = 64

# Must match the embedding model used in embeddings.py and the pgvector
# column dimension in the Alembic migration.
TIKTOKEN_ENCODING: str = "cl100k_base"


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ChunkData:
    """A single chunk ready for embedding and database insertion.

    Attributes:
        embed_content:  The string submitted to OpenRouter for embedding.
                        Always prefixed with ``[section_title]\\n`` when a
                        section title is available. This gives the embedding
                        model maximum semantic signal.

        raw_text:       The clean chunk body text stored in
                        ``DocumentChunk.content``. No section-title prefix.
                        This is what the Phase 4 LLM receives as retrieved
                        context; having the prefix stripped keeps the LLM
                        prompt lean and avoids token waste.

        page_number:    1-indexed source page (from ``ParsedBlock.page_number``).
                        Stored verbatim on ``DocumentChunk`` for citation rendering.

        section_title:  Nearest section heading (or ``None`` for preamble text).
                        Stored on ``DocumentChunk`` for frontend citation display
                        ("Answer found in §3.2 Representations and Warranties").

        chunk_index:    Zero-based position within the parent document.
                        Allows the frontend to order chunks for display.

        token_count:    tiktoken count of ``raw_text`` (NOT of ``embed_content``).
                        Stored on ``DocumentChunk`` for Phase 4 context budgeting.
    """
    embed_content: str
    raw_text: str
    page_number: int
    section_title: str | None
    chunk_index: int
    token_count: int


# ── Public API ────────────────────────────────────────────────────────────────

def chunk_blocks(blocks: list[ParsedBlock]) -> list[ChunkData]:
    """Convert a list of ``ParsedBlock`` objects into ``ChunkData`` objects.

    This is the single entry point called by the Celery ingestion task.

    Args:
        blocks: Output of ``DocumentIngestorProtocol.parse()``.

    Returns:
        An ordered list of ``ChunkData`` objects numbered from 0.
        Empty blocks (whitespace-only text) are silently skipped.
    """
    encoder = tiktoken.get_encoding(TIKTOKEN_ENCODING)
    chunks: list[ChunkData] = []
    chunk_index = 0

    for block in blocks:
        text = block.text.strip()
        if not text:
            continue

        token_ids = encoder.encode(text)
        token_count = len(token_ids)

        if token_count <= MAX_CHUNK_TOKENS:
            # ── Happy path: section fits in a single chunk ─────────────────
            chunk_index = _emit_chunk(
                chunks=chunks,
                raw_text=text,
                page_number=block.page_number,
                section_title=block.section_title,
                chunk_index=chunk_index,
                token_count=token_count,
            )
        else:
            # ── Fallback: section is too large — split with overlap ─────────
            logger.debug(
                "Section '%s' (page %d) has %d tokens > limit %d — splitting",
                (block.section_title or "<no title>")[:60],
                block.page_number,
                token_count,
                MAX_CHUNK_TOKENS,
            )
            sub_chunks = _split_with_overlap(
                token_ids=token_ids,
                encoder=encoder,
                page_number=block.page_number,
                section_title=block.section_title,
            )
            for sub in sub_chunks:
                chunk_index = _emit_chunk(
                    chunks=chunks,
                    raw_text=sub["text"],
                    page_number=sub["page_number"],
                    section_title=sub["section_title"],
                    chunk_index=chunk_index,
                    token_count=sub["token_count"],
                )

    logger.info(
        "Chunker produced %d chunks from %d blocks",
        len(chunks),
        len(blocks),
    )
    return chunks


# ── Private helpers ───────────────────────────────────────────────────────────

def _build_embed_content(section_title: str | None, raw_text: str) -> str:
    """Build the string submitted to the embedding API.

    User requirement (non-negotiable): section_title MUST be prepended so the
    embedding captures the full semantic context of the passage, not just its
    local words out of context.

    Format:
        With title:    "[Representations and Warranties]\\nThe Seller represents..."
        Without title: "This Agreement is entered into as of..."
    """
    if section_title:
        return f"[{section_title}]\n{raw_text}"
    return raw_text


def _emit_chunk(
    *,
    chunks: list[ChunkData],
    raw_text: str,
    page_number: int,
    section_title: str | None,
    chunk_index: int,
    token_count: int,
) -> int:
    """Append a ``ChunkData`` to the accumulator and return the next index."""
    chunks.append(ChunkData(
        embed_content=_build_embed_content(section_title, raw_text),
        raw_text=raw_text,
        page_number=page_number,
        section_title=section_title,
        chunk_index=chunk_index,
        token_count=token_count,
    ))
    return chunk_index + 1


def _split_with_overlap(
    *,
    token_ids: list[int],
    encoder: tiktoken.Encoding,
    page_number: int,
    section_title: str | None,
) -> list[dict]:
    """Sliding-window token split for over-length sections.

    Produces overlapping sub-chunks so clauses near boundaries are retrievable
    from both surrounding chunks.

    Args:
        token_ids:     Pre-encoded token IDs of the full section text.
        encoder:       tiktoken encoder instance (for decoding back to str).
        page_number:   Inherited from the parent ``ParsedBlock``.
        section_title: Inherited from the parent ``ParsedBlock``.

    Returns:
        A list of dicts with keys: ``text``, ``page_number``,
        ``section_title``, ``token_count``.
    """
    stride = MAX_CHUNK_TOKENS - OVERLAP_TOKENS  # how far to advance each step
    sub_chunks: list[dict] = []
    start = 0

    while start < len(token_ids):
        end = min(start + MAX_CHUNK_TOKENS, len(token_ids))
        chunk_token_ids = token_ids[start:end]
        chunk_text = encoder.decode(chunk_token_ids).strip()

        if chunk_text:
            sub_chunks.append({
                "text": chunk_text,
                "page_number": page_number,
                "section_title": section_title,
                "token_count": len(chunk_token_ids),
            })

        if end >= len(token_ids):
            break

        start += stride

    return sub_chunks
