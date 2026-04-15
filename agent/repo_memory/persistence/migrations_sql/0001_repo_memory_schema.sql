CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS repo_memory_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS repositories (
    repo TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS files (
    repo TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    current_observed_seq INTEGER NOT NULL,
    PRIMARY KEY (repo, path)
);

CREATE TABLE IF NOT EXISTS file_revisions (
    repo TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    observed_seq INTEGER NOT NULL,
    content TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (repo, path, observed_seq)
);

CREATE TABLE IF NOT EXISTS entities (
    entity_id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    kind TEXT NOT NULL,
    current_observed_seq INTEGER NOT NULL,
    predecessor_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS entity_revisions (
    entity_id TEXT NOT NULL,
    repo TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    observed_seq INTEGER NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    parent_qualified_name TEXT NULL,
    docstring TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    retrieval_text TEXT NOT NULL DEFAULT '',
    start_line INTEGER NULL,
    end_line INTEGER NULL,
    embedding VECTOR({{VECTOR_DIMENSIONS}}) NULL,
    PRIMARY KEY (entity_id, observed_seq)
);

CREATE TABLE IF NOT EXISTS entity_links (
    entity_id TEXT NOT NULL,
    related_entity_id TEXT NOT NULL,
    link_type TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (entity_id, related_entity_id, link_type)
);

CREATE TABLE IF NOT EXISTS repo_events (
    event_id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    observed_seq INTEGER NOT NULL,
    path TEXT NULL,
    entity_id TEXT NULL,
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS repo_core_blocks (
    repo TEXT NOT NULL,
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    value TEXT NOT NULL,
    token_budget INTEGER NOT NULL,
    read_only BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (repo, label)
);

CREATE TABLE IF NOT EXISTS sync_state (
    repo TEXT PRIMARY KEY,
    last_observed_seq INTEGER NOT NULL DEFAULT 0,
    last_compiled_seq INTEGER NOT NULL DEFAULT 0,
    dreaming_cursor INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS memory_claims (
    claim_id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    claim_key TEXT NOT NULL,
    source_identity_key TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_ref TEXT NOT NULL,
    claim_kind TEXT NOT NULL,
    text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    status TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL DEFAULT 0,
    score_components JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    last_revalidated_at TIMESTAMPTZ NULL,
    revalidation_mode TEXT NOT NULL,
    embedding VECTOR({{VECTOR_DIMENSIONS}}) NULL,
    embedding_provider TEXT NOT NULL DEFAULT 'openai',
    embedding_dimensions INTEGER NOT NULL DEFAULT {{VECTOR_DIMENSIONS}},
    embedding_version TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (repo, claim_key),
    UNIQUE (repo, source_identity_key)
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    evidence_id TEXT NOT NULL,
    repo TEXT NOT NULL,
    claim_key TEXT NOT NULL,
    run_id TEXT NULL,
    evidence_kind TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    evidence_text TEXT NOT NULL DEFAULT '',
    weight DOUBLE PRECISION NOT NULL DEFAULT 0,
    observed_at TIMESTAMPTZ NOT NULL,
    source_thread_id TEXT NULL,
    source_path TEXT NULL,
    source_entity_id TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (repo, evidence_id)
);

CREATE TABLE IF NOT EXISTS repo_core_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    compiled_at TIMESTAMPTZ NOT NULL,
    source_watermark INTEGER NOT NULL DEFAULT 0,
    blocks JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_claim_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS dream_runs (
    run_id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    run_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NULL,
    worker_id TEXT NULL,
    cursor_before INTEGER NOT NULL DEFAULT 0,
    cursor_after INTEGER NOT NULL DEFAULT 0,
    signal_count INTEGER NOT NULL DEFAULT 0,
    claim_candidate_count INTEGER NOT NULL DEFAULT 0,
    merged_count INTEGER NOT NULL DEFAULT 0,
    promoted_count INTEGER NOT NULL DEFAULT 0,
    snapshot_id TEXT NULL,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS dreaming_leases (
    repo TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entity_revisions_repo_seq
ON entity_revisions (repo, observed_seq DESC);

CREATE INDEX IF NOT EXISTS idx_repo_events_repo_seq
ON repo_events (repo, observed_seq DESC);

CREATE INDEX IF NOT EXISTS idx_entities_repo_path
ON entities (repo, path);

CREATE INDEX IF NOT EXISTS idx_memory_claims_repo_kind_status
ON memory_claims (repo, claim_kind, status);

CREATE INDEX IF NOT EXISTS idx_repo_core_snapshots_repo_compiled
ON repo_core_snapshots (repo, compiled_at DESC);

CREATE INDEX IF NOT EXISTS idx_entity_revisions_embedding_cosine
ON entity_revisions
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 32);

CREATE INDEX IF NOT EXISTS idx_memory_claims_embedding_cosine
ON memory_claims
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 32);

ALTER TABLE sync_state
ADD COLUMN IF NOT EXISTS dreaming_cursor INTEGER NOT NULL DEFAULT 0;
