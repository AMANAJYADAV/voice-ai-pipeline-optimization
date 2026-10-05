-- =============================================================================
-- Low-latency PostgreSQL full-text search schema
-- =============================================================================

-- 1. Knowledge base table
CREATE TABLE IF NOT EXISTS public.knowledge_items (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category      TEXT        NOT NULL,
    title         TEXT        NOT NULL,
    content       TEXT        NOT NULL,
    keywords      TEXT,
    access_level  TEXT        NOT NULL DEFAULT 'PUBLIC',
    priority      INTEGER     NOT NULL DEFAULT 100,
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT knowledge_items_title_key UNIQUE (title)
);

-- 2. Stored generated tsvector for full-text search
ALTER TABLE public.knowledge_items
    ADD COLUMN IF NOT EXISTS fts tsvector GENERATED ALWAYS AS (
        to_tsvector(
            'english',
            coalesce(title, '') || ' ' ||
            coalesce(content, '') || ' ' ||
            coalesce(keywords, '')
        )
    ) STORED;

-- 3. Indexes
CREATE INDEX IF NOT EXISTS idx_knowledge_fts      ON public.knowledge_items USING GIN (fts);
CREATE INDEX IF NOT EXISTS idx_knowledge_category ON public.knowledge_items (category);
CREATE INDEX IF NOT EXISTS idx_knowledge_active   ON public.knowledge_items (is_active);

-- 4. Search function
-- The tsquery is parsed once per call into a variable, rather than being
-- re-evaluated in the WHERE and ORDER BY clauses.
CREATE OR REPLACE FUNCTION search_knowledge_v1(
    search_query    TEXT,
    category_filter TEXT DEFAULT NULL,
    max_limit       INT  DEFAULT 4
)
RETURNS TABLE (
    id           UUID,
    title        TEXT,
    category     TEXT,
    content      TEXT,
    keywords     TEXT,
    access_level TEXT,
    is_active    BOOLEAN,
    priority     INTEGER
)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    parsed_tsquery tsquery := NULL;
BEGIN
    IF search_query IS NOT NULL AND trim(search_query) <> '' THEN
        -- Match any term: "a b" becomes "a OR b" for web-style parsing.
        parsed_tsquery := websearch_to_tsquery(
            'english',
            replace(trim(search_query), ' ', ' OR ')
        );
    END IF;

    RETURN QUERY
    SELECT
        k.id,
        k.title,
        k.category,
        k.content,
        k.keywords,
        k.access_level,
        k.is_active,
        k.priority
    FROM public.knowledge_items AS k
    WHERE k.is_active = TRUE
      AND (category_filter IS NULL OR lower(k.category) = lower(category_filter))
      AND (
            (parsed_tsquery IS NOT NULL AND k.fts @@ parsed_tsquery)
         OR (search_query IS NOT NULL AND search_query <> ''
             AND k.title ILIKE '%' || search_query || '%')
      )
    ORDER BY
        CASE WHEN parsed_tsquery IS NOT NULL
             THEN ts_rank(k.fts, parsed_tsquery)
             ELSE 0
        END DESC,
        k.priority DESC
    LIMIT max_limit;
END;
$$;
