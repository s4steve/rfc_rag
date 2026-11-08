CREATE EXTENSION IF NOT EXISTS vector;

-- Every RFC in the index (metadata only, for graph lookups) plus active DNS Internet-Drafts.
CREATE TABLE documents (
    doc_id       text PRIMARY KEY,            -- 'RFC1035' or 'draft-ietf-dnsop-grease'
    doc_type     text NOT NULL,               -- 'rfc' | 'draft'
    title        text NOT NULL,
    status       text NOT NULL,               -- index current-status, or 'INTERNET-DRAFT'
    stream       text,
    wg           text,
    area         text,
    pub_date     text,
    abstract     text,
    obsoletes    text[] NOT NULL DEFAULT '{}',
    obsoleted_by text[] NOT NULL DEFAULT '{}',
    updates      text[] NOT NULL DEFAULT '{}',
    updated_by   text[] NOT NULL DEFAULT '{}',
    is_current   boolean GENERATED ALWAYS AS (doc_type = 'rfc' AND obsoleted_by = '{}' AND status <> 'HISTORIC') STORED,  -- drafts are never 'current'
    in_scope     boolean NOT NULL DEFAULT false,  -- chunked + embedded
    draft_rev    text,
    draft_state  text,
    expires      text,
    url          text
);
CREATE INDEX ON documents USING gin (obsoletes);
CREATE INDEX ON documents USING gin (updates);

-- Parent chunks: whole sections, returned for context expansion.
CREATE TABLE sections (
    doc_id     text NOT NULL REFERENCES documents ON DELETE CASCADE,
    section_id text NOT NULL,                 -- '4.1.1', 'A.2', 'abstract'
    parent_id  text,
    title      text NOT NULL,
    depth      int  NOT NULL,
    ord        int  NOT NULL,
    text       text NOT NULL,
    PRIMARY KEY (doc_id, section_id)
);

-- Child chunks: what gets embedded and searched.
CREATE TABLE chunks (
    chunk_id     text PRIMARY KEY,            -- 'RFC1035#4.1.1#0', 'RFC1035#errata-562'
    doc_id       text NOT NULL REFERENCES documents ON DELETE CASCADE,
    section_id   text,
    kind         text NOT NULL,               -- 'text' | 'erratum'
    ord          int  NOT NULL,
    context      text NOT NULL,               -- header prepended for embedding
    content      text NOT NULL,
    content_hash text NOT NULL,               -- of context + content + model
    model        text NOT NULL,
    embedding    vector(768) NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', context || ' ' || content)) STORED
);
CREATE INDEX ON chunks (doc_id);
CREATE INDEX ON chunks USING gin (tsv);
-- ponytail: no HNSW index; exact scan over ~50k rows is fast and stays correct under status filters.
-- Add `USING hnsw (embedding vector_cosine_ops)` + hnsw.iterative_scan if the corpus grows past ~500k.

CREATE TABLE errata (
    errata_id  text PRIMARY KEY,
    doc_id     text NOT NULL REFERENCES documents ON DELETE CASCADE,
    section_id text,
    type       text,
    status     text,                          -- 'Verified' | 'Held for Document Update'
    original   text,
    corrected  text,
    notes      text
);
CREATE INDEX ON errata (doc_id, section_id);

CREATE TABLE iana (
    registry    text NOT NULL,
    value       text,
    name        text,
    description text,
    refs        text[] NOT NULL DEFAULT '{}'
);
CREATE INDEX ON iana (lower(name));
CREATE INDEX ON iana (value);
