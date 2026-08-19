-- ============================================================
-- Teaching Warning System :: PostgreSQL + pgvector Initial DDL
-- Version : v0.5.1 (ENGLISH ONLY - zero encoding risk on Windows psql)
-- Requires: PostgreSQL 15+ with pgvector extension installed
-- Usage   : psql -U postgres -h 127.0.0.1 -d teaching_warning -f 001_init.sql
-- ============================================================

-- [CRITICAL] Force client encoding to UTF-8 so Windows GBK psql.exe
-- never mis-decodes the file even if codepage is 936.
SET client_encoding = 'UTF8';

-- ----------------------------------------------------------------
-- 0. Idempotent: Wipe any half-built leftover objects from failed runs
--    (run DROP ... CASCADE so uploaded_files -> student_rows FK link is torn down cleanly)
-- ----------------------------------------------------------------
DROP INDEX IF EXISTS idx_student_rows_embedding_hnsw CASCADE;
DROP INDEX IF EXISTS idx_qa_embedding_hnsw         CASCADE;
DROP VIEW  IF EXISTS v_class_summary                CASCADE;
DROP VIEW  IF EXISTS v_student_summary              CASCADE;
DROP TABLE IF EXISTS qa_sediment                    CASCADE;
DROP TABLE IF EXISTS messages                       CASCADE;
DROP TABLE IF EXISTS conversations                  CASCADE;
DROP TABLE IF EXISTS student_rows                   CASCADE;
DROP TABLE IF EXISTS uploaded_files                 CASCADE;

-- ----------------------------------------------------------------
-- 1. vector extension (idempotent)
-- ----------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;

-- ----------------------------------------------------------------
-- 2. schema_migrations - migration tracking
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT
);
INSERT INTO schema_migrations (version, description)
VALUES ('001_init', 'Initial schema: uploaded_files, student_rows, conversations, messages, qa_sediment, 2 views, HNSW indexes')
ON CONFLICT (version) DO NOTHING;

-- ============================================================
-- 3. uploaded_files - 1 row per uploaded source file
--    UNIQUE(file_hash) => content-level dedup
-- ============================================================
CREATE TABLE uploaded_files (
    id              BIGSERIAL PRIMARY KEY,
    file_hash       CHAR(32)       NOT NULL,
    safe_name       VARCHAR(512)   NOT NULL,
    original_name   VARCHAR(512)   NOT NULL,
    relative_path   VARCHAR(1024)  NOT NULL DEFAULT '',
    file_size       BIGINT         NOT NULL DEFAULT 0,
    source_type     VARCHAR(64)    NOT NULL DEFAULT '',   -- touge / mooc / quiz / unit / attendance / knowledge
    experiment_name VARCHAR(512)   NOT NULL DEFAULT '',
    rows_total      INTEGER        NOT NULL DEFAULT 0,
    rows_student    INTEGER        NOT NULL DEFAULT 0,
    rows_noise      INTEGER        NOT NULL DEFAULT 0,
    rows_teacher    INTEGER        NOT NULL DEFAULT 0,
    status          VARCHAR(32)    NOT NULL DEFAULT 'ingested',
    error_msg       TEXT,
    uploaded_at     TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    metadata        JSONB          NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT uq_uploaded_files_hash UNIQUE (file_hash)
);

CREATE INDEX IF NOT EXISTS idx_uploaded_files_safe_name ON uploaded_files(safe_name);
CREATE INDEX IF NOT EXISTS idx_uploaded_files_status    ON uploaded_files(status);

COMMENT ON TABLE uploaded_files IS 'Source file registry; UNIQUE(file_hash) provides content-level dedup.';

-- ============================================================
-- 4. student_rows - CORE business table (1 row = 1 student score line)
--    UNIQUE(file_id, line_no) => idempotent bulk upsert
-- ============================================================
CREATE TABLE student_rows (
    id              BIGSERIAL PRIMARY KEY,
    file_id         BIGINT         NOT NULL REFERENCES uploaded_files(id) ON DELETE CASCADE,
    line_no         INTEGER        NOT NULL,
    row_type        VARCHAR(32)    NOT NULL DEFAULT 'student',   -- student / header_noise / teacher_noise / data_noise
    -- --- structured filter columns (WHERE in hybrid search + SQL aggregation) ---
    student_id      VARCHAR(64)    NOT NULL DEFAULT '',
    name            VARCHAR(64)    NOT NULL DEFAULT '',
    class_name      VARCHAR(256)   NOT NULL DEFAULT '',
    experiment_name VARCHAR(512)   NOT NULL DEFAULT '',
    source_type     VARCHAR(64)    NOT NULL DEFAULT '',
    final_score     NUMERIC(10,2),
    weak_count      INTEGER        NOT NULL DEFAULT 0,
    task_count      INTEGER        NOT NULL DEFAULT 0,
    -- --- raw + vector columns ---
    row_text        TEXT           NOT NULL,                     -- natural-language line, used for Embedding + LLM context
    extra_cols      JSONB          NOT NULL DEFAULT '{}'::JSONB, -- ALL original columns preserved (zero info loss)
    embedding       vector(512)    NOT NULL,                     -- 512-dim BAAI/bge-small-zh-v1.5 cosine-normalised vector
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_student_rows_file_line UNIQUE (file_id, line_no)
);

-- --- Structural btree indexes (WHERE filter acceleration) ---
CREATE INDEX IF NOT EXISTS idx_student_rows_student_id    ON student_rows(student_id);
CREATE INDEX IF NOT EXISTS idx_student_rows_name          ON student_rows(name);
CREATE INDEX IF NOT EXISTS idx_student_rows_class_name    ON student_rows(class_name);
CREATE INDEX IF NOT EXISTS idx_student_rows_experiment    ON student_rows(experiment_name);
CREATE INDEX IF NOT EXISTS idx_student_rows_source_type   ON student_rows(source_type);
CREATE INDEX IF NOT EXISTS idx_student_rows_file_id       ON student_rows(file_id);
CREATE INDEX IF NOT EXISTS idx_student_rows_row_type      ON student_rows(row_type);
CREATE INDEX IF NOT EXISTS idx_student_rows_final_score   ON student_rows(final_score);
CREATE INDEX IF NOT EXISTS idx_student_rows_cls_exp       ON student_rows(class_name, experiment_name);

-- --- pgvector HNSW index (cosine distance operator <=>) ---
-- m = 16 links per layer, ef_construction = 64 candidate pool during build (good balance)
CREATE INDEX IF NOT EXISTS idx_student_rows_embedding_hnsw
    ON student_rows USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

COMMENT ON TABLE student_rows                  IS 'Core student score-line table; used by hybrid search (structured WHERE + vector cosine ranking).';
COMMENT ON COLUMN student_rows.embedding       IS '512-dim BAAI/bge-small-zh-v1.5, cosine-normalised (vector_cosine_ops).';

-- ============================================================
-- 5. conversations + messages - chat history (replaces old JSON file store)
-- ============================================================
CREATE TABLE conversations (
    id          VARCHAR(64)   PRIMARY KEY,
    title       VARCHAR(512)  NOT NULL DEFAULT 'New chat',
    user_id     VARCHAR(128)  NOT NULL DEFAULT 'default',
    pinned      BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conv_user_pinned ON conversations(user_id, pinned DESC, created_at DESC);

CREATE TABLE messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            VARCHAR(16) NOT NULL,     -- user / assistant
    content         TEXT        NOT NULL,
    tokens          INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_msg_conv_created ON messages(conversation_id, created_at);

-- ============================================================
-- 6. qa_sediment - historical Q&A (replaces old qa_logs.jsonl)
--    embedding column allows semantic search of previous answers
-- ============================================================
CREATE TABLE qa_sediment (
    id              BIGSERIAL PRIMARY KEY,
    user_question   TEXT        NOT NULL,
    assistant_reply TEXT        NOT NULL,
    hit_knowledge   TEXT[]      NOT NULL DEFAULT '{}',
    conversation_id VARCHAR(64),
    embedding       vector(512),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_qa_created ON qa_sediment(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_conv    ON qa_sediment(conversation_id);

CREATE INDEX IF NOT EXISTS idx_qa_embedding_hnsw
    ON qa_sediment USING hnsw (embedding vector_cosine_ops)
    WITH (m = 12, ef_construction = 48);

-- ============================================================
-- 7. VIEW 1: v_student_summary (class x student aggregation)
-- ============================================================
CREATE OR REPLACE VIEW v_student_summary AS
SELECT
    student_id,
    name,
    class_name,
    COUNT(*)                                                             AS experiment_count,
    ROUND(AVG(final_score)::numeric, 2)                                  AS avg_score,
    SUM(CASE WHEN final_score < 60 THEN 1 ELSE 0 END)                    AS weak_count,
    ROUND(100.0 * SUM(CASE WHEN final_score < 60 THEN 1 ELSE 0 END)::numeric
            / NULLIF(COUNT(*), 0), 2)                                    AS weak_rate_percent,
    ARRAY_AGG(DISTINCT experiment_name)                                  AS experiments,
    MIN(created_at)                                                      AS first_seen,
    MAX(created_at)                                                      AS last_seen
FROM student_rows
WHERE row_type = 'student'
  AND student_id <> ''
  AND name <> ''
GROUP BY student_id, name, class_name;

COMMENT ON VIEW v_student_summary IS 'Dashboard-ready aggregation: per (student, class) avg score / weak-rate / experiment count.';

-- ============================================================
-- 8. VIEW 2: v_class_summary (class x experiment aggregation)
-- ============================================================
CREATE OR REPLACE VIEW v_class_summary AS
SELECT
    class_name,
    experiment_name,
    source_type,
    COUNT(*)                                                                 AS student_count,
    ROUND(AVG(final_score)::numeric, 2)                                      AS avg_score,
    ROUND(100.0 * SUM(CASE WHEN final_score < 60 THEN 1 ELSE 0 END)::numeric
            / NULLIF(COUNT(*), 0), 2)                                        AS weak_rate_percent,
    MIN(final_score)                                                         AS min_score,
    MAX(final_score)                                                         AS max_score,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY final_score)                 AS median_score
FROM student_rows
WHERE row_type = 'student'
  AND class_name <> ''
  AND experiment_name <> ''
GROUP BY class_name, experiment_name, source_type;

COMMENT ON VIEW v_class_summary IS 'Dashboard-ready aggregation: per (class, experiment) student-count / avg-score / weak-rate / distribution.';
