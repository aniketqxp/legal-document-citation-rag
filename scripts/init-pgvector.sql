-- ============================================================
-- PostgreSQL init script: enable pgvector extension
-- Runs automatically on first container startup via
-- /docker-entrypoint-initdb.d/
-- ============================================================

-- Enable pgvector for 1536-dim embedding storage
CREATE EXTENSION IF NOT EXISTS vector;

-- Verify installation
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_extension WHERE extname = 'vector'
  ) THEN
    RAISE EXCEPTION 'pgvector extension failed to install';
  END IF;
END;
$$;
