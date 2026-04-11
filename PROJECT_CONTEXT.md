# MISSION: Evolved Legal AI MVP ("Harvey Clone")

## 1. Project Overview
We are building a highly accurate, tenant-isolated Legal AI Contract Review platform. 
The core user journey: A lawyer uploads a massive PDF contract -> Asks a question -> Receives a 3-paragraph answer where *every sentence* has a clickable citation pointing to the exact page/section in the original PDF document.

## 2. The Reference Material
In the `/reference_material` folder, you have three open-source repositories. Do NOT modify these files. Use them strictly as blueprints to extract logic and patterns into our active workspace:
- `/taxonomy`: Blueprint for the Next.js App Router frontend, dashboard UI, and Clerk authentication.
- `/full-stack-fastapi-template`: Blueprint for the Python backend, Docker/PostgreSQL setup, and secure API routing.
- `/ragflow`: Blueprint for deep PDF document parsing, table extraction, and intelligent chunking.

## 3. The Tech Stack Constraints
- **Frontend:** Next.js 14+ (App Router), Tailwind CSS, Shadcn UI.
- **Backend:** Python 3.11+, FastAPI (async), SQLAlchemy/SQLModel.
- **Database:** PostgreSQL.
- **Vector Store:** PostgreSQL with the `pgvector` extension.
- **Task Queue:** Celery + Redis (for background document ingestion).
- **AI Models:** Google Gemini 1.5 Flash (for fast extraction/ingestion) and Gemini 1.5 Pro (for reasoning).
- **LLM Routing:** All LLM API calls must be routed through OpenRouter to standardize endpoints. Do NOT use the default OpenAI API endpoints or keys.

## 4. Hard Rules (Non-Negotiable)
1. **Tenant Isolation:** Every single database table MUST have a `tenant_id` column. Every single database query and vector search MUST filter by `tenant_id`. Cross-tenant data leakage is an existential failure.
2. **Citation Accuracy:** The RAG pipeline must return exact metadata (page number, section, document name). Do not use fixed-size token chunking; use semantic/section-based chunking adapted from the RagFlow reference material.
3. **No Hallucinations:** The prompt must force the LLM to output "I cannot determine this from the provided documents" if the retrieved context does not explicitly contain the answer.
4. **No Cloud Vector SaaS:** Do NOT use Pinecone, Weaviate Cloud, or any external vector database SaaS. All embeddings must be stored and queried locally within PostgreSQL using the `pgvector` extension to ensure data sovereignty and atomic transactions.
5. **No Blind Mashing:** Do not attempt to merge the reference repos blindly. Build the application phase by phase in the root directory, manually adapting the required logic from `/reference_material` into our stack.