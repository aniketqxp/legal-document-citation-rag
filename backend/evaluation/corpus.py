"""Index the eval set's contracts under the eval tenant.

This is the eval's one-time setup step. For every contract referenced by the
eval set it:

    full_contract_txt  ->  blocks  ->  chunks  ->  embeddings  ->  document_chunk

It deliberately reuses the application's real ``chunker``, ``embeddings``
service, and ``bulk_insert_chunks`` so the eval measures the *actual* retrieval
pipeline — not a reimplementation. The only substitution is the text source: we
index CUAD's clean reference text instead of running the PDF parser, so that
retrieval quality is measured independently of parser quality (the parser is a
separate, swappable stage). Heading detection still reuses the parser's own
legal-heading heuristic, so chunk section titles match production behaviour.

Run AFTER building the eval set:
    python -m evaluation.corpus
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys

from evaluation.env_bootstrap import load_repo_env

# Populate DB credentials / API keys before any ``app.*`` import triggers Settings.
load_repo_env()

from app.crud.chunk import bulk_insert_chunks
from app.models.document import Document, DocumentStatus
from app.services.chunker import chunk_blocks
from app.services.embeddings import generate_embeddings
from app.services.pdf_parser import (
    ParsedBlock,
    PdfPlumberHeuristicIngestor,
)
from evaluation import db
from evaluation.dataset import DEFAULT_OUT, TXT_DIR, load_jsonl

# CUAD txt files carry no page markup; approximate one page per this many chars
# purely so the citation metadata column is populated. Page accuracy is NOT what
# this harness measures (that is the parser's job, tested separately).
CHARS_PER_PAGE = 3000

_is_heading = PdfPlumberHeuristicIngestor._is_legal_heading_by_pattern


def text_to_blocks(full_text: str) -> list[ParsedBlock]:
    """Split clean contract text into section-aware blocks for the chunker.

    Mirrors the PDF parser's block model: lines that match the legal-heading
    pattern start a new section; intervening lines accumulate as the section
    body. Page numbers are approximate (see ``CHARS_PER_PAGE``).
    """
    blocks: list[ParsedBlock] = []
    section: str | None = None
    body: list[str] = []
    consumed = 0
    block_start_page = 1

    def flush() -> None:
        nonlocal body
        if body:
            joined = "\n".join(body).strip()
            if joined:
                blocks.append(
                    ParsedBlock(
                        page_number=block_start_page,
                        section_title=section,
                        text=joined,
                    )
                )
        body = []

    for raw_line in full_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        consumed += len(line) + 1
        page = consumed // CHARS_PER_PAGE + 1
        if _is_heading(line):
            flush()
            section = line
            block_start_page = page
        else:
            if not body:
                block_start_page = page
            body.append(line)
    flush()
    return blocks


async def ingest_contract(
    session, *, contract_id: str, txt_filename: str
) -> int:
    """Index one contract under the eval tenant; return the chunk count."""
    txt_path = TXT_DIR / txt_filename
    full_text = txt_path.read_text(encoding="utf-8", errors="ignore")

    blocks = text_to_blocks(full_text)
    chunks = chunk_blocks(blocks)
    if not chunks:
        print(f"  ! {contract_id}: produced no chunks, skipping")
        return 0

    embeddings = await generate_embeddings([c.embed_content for c in chunks])

    raw_bytes = full_text.encode("utf-8")
    document = Document(
        tenant_id=db.EVAL_TENANT_ID,
        uploaded_by_id=db.EVAL_USER_ID,
        original_filename=contract_id,  # retrieval surfaces this as the citation
        filename=txt_filename,
        file_hash=hashlib.sha256(raw_bytes).hexdigest(),
        minio_object_key=f"eval/{contract_id}",
        status=DocumentStatus.ready,
        page_count=max((b.page_number for b in blocks), default=1),
        file_size_bytes=len(raw_bytes),
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)

    return await bulk_insert_chunks(
        session,
        document_id=document.id,
        tenant_id=db.EVAL_TENANT_ID,
        chunks=chunks,
        embeddings=embeddings,
    )


async def build_corpus(eval_set_path) -> None:
    records = load_jsonl(eval_set_path)
    # One entry per distinct contract (a contract appears once per question).
    contracts = {r["contract_id"]: r["txt_filename"] for r in records}
    print(f"Indexing {len(contracts)} contracts under eval tenant {db.EVAL_TENANT_ID}")

    engine = db.make_engine()
    session_factory = db.make_session_factory(engine)
    try:
        async with session_factory() as session:
            await db.ensure_eval_principals(session)
            await db.reset_eval_corpus(session)

            total_chunks = 0
            for i, (contract_id, txt_filename) in enumerate(contracts.items(), start=1):
                print(f"[{i}/{len(contracts)}] {contract_id}")
                total_chunks += await ingest_contract(
                    session, contract_id=contract_id, txt_filename=txt_filename
                )
    finally:
        await engine.dispose()

    print(f"\nDone. Indexed {len(contracts)} contracts, {total_chunks} chunks total.")


def main() -> None:
    # asyncpg requires the Selector event loop on Windows (the default Proactor
    # loop lacks the socket primitives it needs).
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = argparse.ArgumentParser(description="Index the eval corpus.")
    parser.add_argument("--eval-set", default=DEFAULT_OUT, help="eval set JSONL path")
    args = parser.parse_args()
    asyncio.run(build_corpus(args.eval_set))


if __name__ == "__main__":
    main()
