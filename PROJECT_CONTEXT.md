# MISSION: legal-document-citation-rag

## 1. Project Overview

This project is a tenant-isolated legal contract review application.

Core user journey:

1. A user uploads a PDF contract.
2. The backend stores the PDF in MinIO and indexes extracted text into PostgreSQL.
3. The user asks a question about one or more documents.
4. The API returns an answer with bracketed citations that map back to document,
   page, section, and source snippet metadata.

## 2. Active Stack

- Frontend: Vite, React, TypeScript, TanStack Router, TanStack Query, Tailwind CSS.
- Backend: Python 3.11, FastAPI, SQLModel, SQLAlchemy asyncio.
- Database: PostgreSQL with pgvector.
- Task queue: Celery with Redis.
- Object storage: MinIO.
- AI routing: OpenRouter for chat completions and embeddings.
- Local dataset/reference inputs: CUAD PDFs and upstream reference repositories are
  kept out of git and used only as local implementation references.

## 3. RAG Flow

1. Upload endpoint validates PDF inputs and writes the file to MinIO.
2. A tenant-scoped Celery task parses the PDF with pdfplumber.
3. The chunker groups text by detected section and page metadata.
4. Embeddings are generated through OpenRouter and stored in pgvector.
5. Query handling embeds the question, runs vector search plus PostgreSQL full-text
   search, merges results with reciprocal rank fusion, and sends the retrieved
   context to the chat model through OpenRouter.
6. The answer parser resolves aliases like `[Source 1]` into structured citation
   metadata for the frontend PDF viewer.

## 4. Hard Rules

1. Tenant isolation: every tenant-owned table has `tenant_id`, and every read path
   must filter by `tenant_id`.
2. Citation metadata: chunks store document ID, filename, page number, section
   title, and snippet seed data for citation rendering.
3. No unsupported answers: prompts require the model to answer
   `I cannot determine this from the provided documents.` when retrieved context
   does not contain the answer.
4. Local vector storage: embeddings are stored in PostgreSQL with pgvector.
5. OpenRouter routing: chat completions and embeddings use OpenRouter endpoints.
