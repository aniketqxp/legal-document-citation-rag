"""LLM service routed through OpenRouter.

Responsibilities:
  1. Build the retrieval-augmented prompt from retrieved context chunks.
  2. Call the configured chat model through OpenRouter with retry logic.
  3. Parse bracketed citation aliases ([Source 1], [Source 2]) into structured data.
"""

import asyncio
import logging
import random
import re
import uuid
from dataclasses import dataclass

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError

from app.core.config import settings

logger = logging.getLogger(__name__)

QUERY_MODEL: str = settings.QUERY_LLM_MODEL
MAX_CONTEXT_CHUNKS: int = 6
MAX_RETRIES: int = 5
RETRY_BASE_SECONDS: float = 2.0
MAX_HISTORY_MESSAGES: int = 6

SYSTEM_PROMPT: str = """You are an expert legal AI assistant.
You review contract documents and answer questions with precision.

RULES:
1. Base your answer ONLY on the context blocks provided in the current message.
2. Every factual claim in your answer MUST end with the stable bracketed alias
   (e.g., [Source 1], [Source 2]) provided in the context header.
3. Use multiple citations if information comes from multiple blocks.
   Example: The contract terminates in 30 days [Source 1] but allows extensions [Source 3].
4. If the answer cannot be found in the provided context, respond with
   exactly: "I cannot determine this from the provided documents."
5. Do NOT add information from your training data. Legal accuracy is critical.
6. Write in clear, concise prose with paragraph breaks for readability.
7. You may use conversation history to understand follow-up questions, but all
   factual claims MUST come from the current context blocks.
"""


@dataclass
class HistoryItem:
    """A single message from the conversation history."""

    role: str
    content: str


@dataclass
class ChunkContext:
    alias_index: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    original_filename: str
    page_number: int
    section_title: str | None
    content: str
    rrf_score: float


@dataclass
class CitationResult:
    alias: str
    chunk_id: str
    document_id: str
    source_filename: str
    page_number: int
    section_title: str | None
    snippet: str


@dataclass
class LLMResponse:
    answer: str
    citations: list[CitationResult]
    model_used: str
    chunks_used: int


class LLMError(Exception):
    """Hard failure from the LLM API."""


async def answer_query(
    *,
    query: str,
    chunks: list[ChunkContext],
    history: list[HistoryItem] | None = None,
) -> LLMResponse:
    """Generate a cited answer from retrieved context chunks."""
    context_chunks = chunks[:MAX_CONTEXT_CHUNKS]
    context_str = _build_context_string(context_chunks)

    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in (history or [])[-MAX_HISTORY_MESSAGES:]:
        role = "assistant" if item.role == "assistant" else "user"
        messages.append({"role": role, "content": item.content})

    current_user_message = f"Context documents:\n\n{context_str}\n\nQuestion: {query}"
    messages.append({"role": "user", "content": current_user_message})

    raw_answer = await _call_openrouter_with_retry(messages=messages)
    citations = _parse_citations(raw_answer, context_chunks)

    return LLMResponse(
        answer=raw_answer,
        citations=citations,
        model_used=QUERY_MODEL,
        chunks_used=len(context_chunks),
    )


def _build_context_string(chunks: list[ChunkContext]) -> str:
    parts: list[str] = []
    for chunk in chunks:
        alias = f"[Source {chunk.alias_index}]"
        section_label = (
            f"Section {chunk.section_title}" if chunk.section_title else "General Provisions"
        )
        doc_context = f"File: {chunk.original_filename}, Section: {section_label}"
        meta = f"Page: {chunk.page_number}, Score: {chunk.rrf_score:.4f}"

        parts.append(
            "CITATION ALIAS: "
            f"{alias}\nDOCUMENT: {doc_context}\nMETADATA: {meta}\n{chunk.content}"
        )
    return "\n\n".join(parts)


async def _call_openrouter_with_retry(*, messages: list[dict[str, str]]) -> str:
    """Call the configured chat model through OpenRouter."""
    if not settings.OPENROUTER_API_KEY:
        raise LLMError(
            "OPENROUTER_API_KEY is not set. Please provide an OpenRouter API key."
        )

    client = AsyncOpenAI(
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.chat.completions.create(
                model=QUERY_MODEL,
                messages=messages,
                temperature=0.1,
                max_tokens=2048,
            )
            content = response.choices[0].message.content

            if not content:
                raise LLMError("OpenRouter returned an empty response.")

            logger.info(
                "OpenRouter chat call succeeded on attempt %d/%d",
                attempt,
                MAX_RETRIES,
            )
            return content

        except LLMError:
            raise

        except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
            if attempt == MAX_RETRIES:
                raise LLMError(
                    f"OpenRouter request failed after {MAX_RETRIES} attempts: {exc}"
                ) from exc
            wait = _backoff_seconds(attempt)
            logger.warning(
                "OpenRouter transient failure (%d/%d); retrying in %.1fs: %s",
                attempt,
                MAX_RETRIES,
                wait,
                exc,
            )
            await asyncio.sleep(wait)

        except Exception as exc:
            raise LLMError(f"OpenRouter API error: {exc}") from exc

    raise LLMError("OpenRouter call failed after exhausting all retries.")


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter."""
    base = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
    jitter = base * 0.25 * (2 * random.random() - 1)
    return max(1.0, base + jitter)


def _parse_citations(answer: str, chunks: list[ChunkContext]) -> list[CitationResult]:
    """Extract citation aliases from the answer text and resolve them to chunks."""
    alias_map: dict[str, ChunkContext] = {
        f"source {chunk.alias_index}": chunk for chunk in chunks
    }
    seen: set[str] = set()
    citations: list[CitationResult] = []

    for match in re.finditer(r"\[(.*?)\]", answer):
        raw_alias = match.group(1).strip()
        lower_alias = raw_alias.lower()

        if lower_alias in seen or lower_alias not in alias_map:
            continue

        seen.add(lower_alias)
        chunk = alias_map[lower_alias]
        citations.append(
            CitationResult(
                alias=raw_alias,
                chunk_id=str(chunk.chunk_id),
                document_id=str(chunk.document_id),
                source_filename=chunk.original_filename,
                page_number=chunk.page_number,
                section_title=chunk.section_title,
                snippet=_citation_snippet(chunk.content),
            )
        )

    return citations


def _citation_snippet(content: str, max_chars: int = 220) -> str:
    """Return a stable snippet seed for frontend text-layer highlighting."""
    normalized = re.sub(r"\s+", " ", content).strip()
    if len(normalized) <= max_chars:
        return normalized

    boundary = normalized.rfind(".", 0, max_chars)
    if boundary >= 80:
        return normalized[: boundary + 1]

    return normalized[:max_chars].rstrip()
