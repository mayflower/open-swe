-- 0002: concurrency support — per-path index, HNSW vector indexes, and
-- pg_notify triggers used by the cross-worker invalidation listener.

CREATE INDEX IF NOT EXISTS idx_entity_revisions_repo_path
ON entity_revisions (repo, path);

CREATE INDEX IF NOT EXISTS idx_entities_repo_path_seq
ON entities (repo, path, current_observed_seq DESC);

CREATE INDEX IF NOT EXISTS idx_claim_evidence_repo_claim_observed
ON claim_evidence (repo, claim_key, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_claim_evidence_repo_evidence_ref
ON claim_evidence (repo, evidence_ref);

-- HNSW indexes are additive to the IVFFlat ones from migration 0001. Older
-- pgvector builds (<0.5) lack the access method; we skip silently in that
-- case so the migration stays idempotent across environments.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_am WHERE amname = 'hnsw') THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_entity_revisions_embedding_hnsw '
                'ON entity_revisions USING hnsw (embedding vector_cosine_ops)';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_memory_claims_embedding_hnsw '
                'ON memory_claims USING hnsw (embedding vector_cosine_ops)';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION repo_memory_notify_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('repo_memory_event', NEW.repo);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_repo_memory_event_notify ON repo_events;
CREATE TRIGGER trg_repo_memory_event_notify
AFTER INSERT ON repo_events
FOR EACH ROW EXECUTE FUNCTION repo_memory_notify_event();

CREATE OR REPLACE FUNCTION repo_memory_notify_claim() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('repo_memory_claim', NEW.repo);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_repo_memory_claim_notify ON memory_claims;
CREATE TRIGGER trg_repo_memory_claim_notify
AFTER INSERT OR UPDATE ON memory_claims
FOR EACH ROW EXECUTE FUNCTION repo_memory_notify_claim();

CREATE OR REPLACE FUNCTION repo_memory_notify_snapshot() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('repo_memory_snapshot', NEW.repo);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_repo_memory_snapshot_notify ON repo_core_snapshots;
CREATE TRIGGER trg_repo_memory_snapshot_notify
AFTER INSERT OR UPDATE ON repo_core_snapshots
FOR EACH ROW EXECUTE FUNCTION repo_memory_notify_snapshot();
