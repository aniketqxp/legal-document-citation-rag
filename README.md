# legal-document-citation-rag

Tenant-isolated legal document question answering with PDF citation metadata.

The application lets a user upload contract PDFs, indexes extracted sections into
PostgreSQL with pgvector, and answers questions with citations that point back to
the source document, page, section, and snippet seed.

## Stack

- Backend: FastAPI, SQLModel, SQLAlchemy asyncio, Alembic
- Frontend: Vite, React, TypeScript, TanStack Router, TanStack Query
- Storage: PostgreSQL with pgvector, MinIO for PDF objects
- Background jobs: Celery with Redis
- AI provider path: OpenRouter for embeddings and chat completions

## Core Flow

1. `POST /api/v1/documents/upload` validates a PDF, writes it to MinIO, creates a
   tenant-scoped document row, and queues ingestion.
2. The Celery worker loads the document by `document_id` and `tenant_id`, parses
   text with pdfplumber, creates section-aware chunks, embeds them, and stores
   vectors in `document_chunk`.
3. `POST /api/v1/chat/conversations/{id}/query` embeds the question, runs hybrid
   retrieval with pgvector and PostgreSQL full-text search, fuses ranks with RRF,
   and sends the retrieved context to the configured OpenRouter chat model.
4. The response resolves aliases such as `[Source 1]` into structured citation
   objects for the PDF viewer.

## Tenant Isolation

Tenant-owned tables include `tenant_id`. API routes derive the tenant from the
JWT current-user dependency. CRUD and retrieval calls accept `tenant_id` as a
required input, and the worker receives `tenant_id` with each ingestion task so
background processing uses the same boundary as request handling.

## Local Development

Copy the environment template:

```bash
cp .env.example .env
```

Start backend infrastructure:

```bash
docker compose up -d
docker compose exec backend python -m app.initial_data
```

Start the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open the workbench at `http://localhost:5173`.

## Important Environment Variables

- `OPENROUTER_API_KEY`: required for embeddings and chat answers
- `OPENROUTER_BASE_URL`: defaults to `https://openrouter.ai/api/v1`
- `EMBEDDING_MODEL`: defaults to `openai/text-embedding-3-small`
- `QUERY_LLM_MODEL`: defaults to `google/gemini-2.5-flash`
- `VITE_API_URL`: frontend API base URL, normally `http://localhost:8000/api/v1`

## Notes

- `CUAD_v1/` and `reference_materials/` are local inputs and are ignored by git.
- Uploaded PDFs are stored in MinIO, not in the repository.
- The PDF parser is an MVP heuristic parser for selectable-text contracts; scanned
  PDFs fail clearly instead of being silently indexed.
