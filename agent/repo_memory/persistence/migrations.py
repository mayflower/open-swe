from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from ..config import RepoMemoryConfig
from .models import build_metadata

MIGRATIONS_DIR = Path(__file__).with_name("migrations_sql")
MIGRATION_TABLE = "repo_memory_schema_migrations"


@dataclass(frozen=True, slots=True)
class RepoMemoryMigration:
    version: str
    path: Path


def list_repo_memory_migrations() -> list[RepoMemoryMigration]:
    migrations = [
        RepoMemoryMigration(
            version=path.stem.split("_", 1)[0],
            path=path,
        )
        for path in sorted(MIGRATIONS_DIR.glob("*.sql"))
    ]
    if not migrations:
        raise RuntimeError(f"No repo-memory migrations found in {MIGRATIONS_DIR}")
    return migrations


def latest_repo_memory_schema_version() -> str:
    return list_repo_memory_migrations()[-1].version


async def apply_repo_memory_migrations_async(
    database_url: str,
    *,
    vector_dimensions: int,
) -> list[str]:
    conn = await asyncpg.connect(database_url)
    applied: list[str] = []
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        rows = await conn.fetch(f"SELECT version FROM {MIGRATION_TABLE}")
        applied_versions = {row["version"] for row in rows}
        for migration in list_repo_memory_migrations():
            if migration.version in applied_versions:
                continue
            sql = migration.path.read_text(encoding="utf-8").replace(
                "{{VECTOR_DIMENSIONS}}",
                str(vector_dimensions),
            )
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    f"""
                    INSERT INTO {MIGRATION_TABLE} (version)
                    VALUES ($1)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    migration.version,
                )
            applied.append(migration.version)
    finally:
        await conn.close()
    return applied


def apply_repo_memory_migrations(
    database_url: str,
    *,
    vector_dimensions: int,
) -> list[str]:
    return asyncio.run(
        apply_repo_memory_migrations_async(
            database_url,
            vector_dimensions=vector_dimensions,
        )
    )


async def validate_repo_memory_schema_async(
    database_url: str,
    *,
    vector_dimensions: int | None = None,
) -> str:
    expected_version = latest_repo_memory_schema_version()
    metadata = build_metadata()
    conn = await asyncpg.connect(database_url)
    try:
        table_present = await conn.fetchval("SELECT to_regclass($1)", MIGRATION_TABLE)
        if table_present is None:
            raise RuntimeError(
                "Repo-memory schema is not migrated. Run `uv run repo-memory-migrate` first."
            )
        applied_version = await conn.fetchval(
            f"SELECT version FROM {MIGRATION_TABLE} ORDER BY version DESC LIMIT 1"
        )
        if applied_version != expected_version:
            raise RuntimeError(
                "Repo-memory schema version mismatch: "
                f"expected {expected_version}, found {applied_version!r}. "
                "Run `uv run repo-memory-migrate`."
            )
        vector_enabled = await conn.fetchval(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        )
        if vector_enabled != 1:
            raise RuntimeError(
                "Postgres pgvector extension is not installed. "
                "Start the repo's pgvector-backed Postgres harness."
            )
        for table_name in metadata.tables:
            if await conn.fetchval("SELECT to_regclass($1)", table_name) is None:
                raise RuntimeError(
                    "Repo-memory schema is incomplete: "
                    f"missing table {table_name!r}. Run `uv run repo-memory-migrate`."
                )
        if vector_dimensions is not None:
            for table_name in ("entity_revisions", "memory_claims"):
                stored_dimensions = await conn.fetchval(
                    """
                    SELECT NULLIF(a.atttypmod, -1) - 4
                    FROM pg_attribute a
                    JOIN pg_class c ON c.oid = a.attrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = current_schema()
                      AND c.relname = $1
                      AND a.attname = 'embedding'
                    """,
                    table_name,
                )
                if stored_dimensions is not None and stored_dimensions != vector_dimensions:
                    raise RuntimeError(
                        "Repo-memory vector dimension mismatch: "
                        f"{table_name}.embedding is {stored_dimensions}, "
                        f"config expects {vector_dimensions}. "
                        "Run `uv run repo-memory-migrate --vector-dimensions ...` "
                        "against a fresh schema or align the embedding config."
                    )
        return expected_version
    finally:
        await conn.close()


def validate_repo_memory_schema(
    database_url: str,
    *,
    vector_dimensions: int | None = None,
) -> str:
    return asyncio.run(
        validate_repo_memory_schema_async(
            database_url,
            vector_dimensions=vector_dimensions,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply repo-memory Postgres migrations.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Database URL for the repo-memory Postgres backend.",
    )
    parser.add_argument(
        "--vector-dimensions",
        type=int,
        default=None,
        help="Embedding vector width to bake into the schema.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate that the latest repo-memory schema is already applied.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config = RepoMemoryConfig()
    database_url = args.database_url or config.database_url
    if not database_url:
        raise SystemExit("REPO_MEMORY_DATABASE_URL or --database-url is required.")
    if args.validate_only:
        validate_repo_memory_schema(database_url, vector_dimensions=config.embedding_dimensions)
        return 0
    vector_dimensions = args.vector_dimensions or config.embedding_dimensions or 1536
    apply_repo_memory_migrations(
        database_url,
        vector_dimensions=vector_dimensions,
    )
    validate_repo_memory_schema(database_url, vector_dimensions=vector_dimensions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
