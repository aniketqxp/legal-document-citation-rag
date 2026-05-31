"""Unit tests for citation parsing in the LLM service.

Resolving ``[Source N]`` aliases into structured citations is the product's
headline feature, and ``_parse_citations`` is a pure function over the model's
text output — so it is worth pinning down precisely.
"""

import uuid

from app.services.llm import ChunkContext, _parse_citations


def _chunk(alias_index: int, content: str = "Some clause text.") -> ChunkContext:
    return ChunkContext(
        alias_index=alias_index,
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        original_filename=f"doc{alias_index}.pdf",
        page_number=alias_index,
        section_title=f"Section {alias_index}",
        content=content,
        rrf_score=0.5,
    )


def test_parses_aliases_in_order_of_appearance():
    chunks = [_chunk(1), _chunk(2), _chunk(3)]
    answer = "The term is 30 days [Source 2] but renews automatically [Source 1]."
    citations = _parse_citations(answer, chunks)

    assert [c.alias for c in citations] == ["Source 2", "Source 1"]
    assert citations[0].page_number == 2
    assert citations[0].source_filename == "doc2.pdf"


def test_deduplicates_repeated_aliases():
    chunks = [_chunk(1)]
    answer = "Obligation A [Source 1]. Obligation B [Source 1]."
    citations = _parse_citations(answer, chunks)
    assert len(citations) == 1
    assert citations[0].alias == "Source 1"


def test_ignores_aliases_with_no_matching_chunk():
    chunks = [_chunk(1), _chunk(2)]
    answer = "This cites a chunk that was never provided [Source 9]."
    assert _parse_citations(answer, chunks) == []


def test_returns_empty_list_when_no_citations_present():
    chunks = [_chunk(1)]
    answer = "I cannot determine this from the provided documents."
    assert _parse_citations(answer, chunks) == []


def test_citation_snippet_is_populated_from_chunk_content():
    chunks = [_chunk(1, content="The governing law is the State of Nevada.")]
    citations = _parse_citations("Governed by Nevada law [Source 1].", chunks)
    assert citations[0].snippet == "The governing law is the State of Nevada."
