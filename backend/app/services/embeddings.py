"""Embedding generation service via OpenRouter.

All API calls are routed through OpenRouter using the OpenAI-compatible SDK,
satisfying the PROJECT_CONTEXT.md hard rule: "All LLM API calls must be
routed through OpenRouter."

Batching strategy
─────────────────
The OpenRouter ``text-embedding-3-small`` endpoint accepts up to 2048 inputs
per request. We batch conservatively at ``EMBEDDING_BATCH_SIZE = 100`` to
stay well within rate limits even under concurrent Celery workers. Each batch
is executed in a thread pool (``asyncio.to_thread``) so the event loop is not
blocked by the synchronous OpenAI SDK call.

Response ordering
─────────────────
The OpenAI spec guarantees that ``response.data`` items carry an ``.index``
field matching the position of the corresponding input string. We sort by this
index before appending to the result list, making the output order
deterministic regardless of the API's response ordering.

Error handling
──────────────
Any API-level error raises ``EmbeddingError`` (a non-retriable signal for hard
failures like bad API keys) or propagates the raw exception (for transient
network errors that Celery should retry).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from openai import OpenAI, AuthenticationError, BadRequestError

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Number of texts to send per API call.
# 100 is conservative — safely within rate limits at peak load.
EMBEDDING_BATCH_SIZE: int = 100


# ── Client factory ────────────────────────────────────────────────────────────

def _get_client() -> OpenAI:
    """Return an OpenAI-compatible client pointing at OpenRouter.

    A new client is created per call (lightweight; just stores config).
    Connection pooling is handled by httpx internally.
    """
    return OpenAI(
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
    )


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_embeddings(texts: Sequence[str]) -> list[list[float]]:
    """Generate vector embeddings for a sequence of text strings.

    Sends texts in batches of ``EMBEDDING_BATCH_SIZE`` to the OpenRouter
    embedding endpoint. The output list is guaranteed to be in the same order
    as the input sequence.

    Args:
        texts: The strings to embed. For Phase 3 these are the
               ``ChunkData.embed_content`` strings (with section-title prefix).

    Returns:
        A list of float vectors, one per input text, in input order.
        Each vector has ``settings.EMBEDDING_DIMENSIONS`` dimensions (1536).

    Raises:
        EmbeddingError: On authentication failures or malformed requests
                        (hard failures — Celery should NOT retry these).
        Exception:      Propagated as-is for transient network errors
                        (Celery will retry with exponential back-off).
    """
    if not texts:
        return []

    texts_list = list(texts)
    all_embeddings: list[list[float]] = []
    total = len(texts_list)
    total_batches = (total + EMBEDDING_BATCH_SIZE - 1) // EMBEDDING_BATCH_SIZE

    logger.info(
        "Embedding %d texts in %d batches — model=%s dims=%d",
        total,
        total_batches,
        settings.EMBEDDING_MODEL,
        settings.EMBEDDING_DIMENSIONS,
    )

    client = _get_client()

    for batch_num, batch_start in enumerate(
        range(0, total, EMBEDDING_BATCH_SIZE), start=1
    ):
        batch = texts_list[batch_start : batch_start + EMBEDDING_BATCH_SIZE]

        try:
            response = await asyncio.to_thread(
                client.embeddings.create,
                model=settings.EMBEDDING_MODEL,
                input=batch,
                dimensions=settings.EMBEDDING_DIMENSIONS,
            )
        except AuthenticationError as exc:
            raise EmbeddingError(
                "OpenRouter authentication failed — check OPENROUTER_API_KEY. "
                f"Detail: {exc}"
            ) from exc
        except BadRequestError as exc:
            raise EmbeddingError(
                f"OpenRouter rejected the embedding request (batch {batch_num}/"
                f"{total_batches}): {exc}"
            ) from exc
        except Exception:
            # Transient error (timeout, connection reset, etc.) — let Celery retry.
            logger.exception(
                "Transient error on embedding batch %d/%d", batch_num, total_batches
            )
            raise

        # Sort by index to guarantee ordering matches input.
        sorted_items = sorted(response.data, key=lambda item: item.index)
        batch_vectors = [item.embedding for item in sorted_items]
        all_embeddings.extend(batch_vectors)

        logger.debug(
            "Batch %d/%d ✓ — received %d vectors",
            batch_num,
            total_batches,
            len(batch_vectors),
        )

    # Sanity check: the API may silently drop items on edge-case inputs.
    if len(all_embeddings) != total:
        raise EmbeddingError(
            f"Embedding count mismatch: submitted {total} texts, "
            f"received {len(all_embeddings)} vectors. Pipeline aborted."
        )

    logger.info("Embedding complete — %d vectors generated", total)
    return all_embeddings


# ── Custom exceptions ─────────────────────────────────────────────────────────

class EmbeddingError(Exception):
    """Hard failure from the embedding API (auth error, bad request, etc.).

    The Celery task treats ``EmbeddingError`` as a non-retriable failure
    that should mark the document as ``failed`` without wasting retry budget.
    """
