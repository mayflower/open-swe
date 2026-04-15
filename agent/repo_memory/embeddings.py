from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass, field

from openai import OpenAI

from .config import RepoMemoryConfig

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


class EmbeddingProvider:
    provider_name: str
    dimensions: int
    version: str

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


@dataclass(slots=True)
class HashEmbeddingProvider(EmbeddingProvider):
    provider_name: str = "hashed"
    dimensions: int = 16
    version: str = "sha256-token-v1"

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = [token.lower() for token in _TOKEN_RE.findall(text)]
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            slot = digest[0] % self.dimensions
            sign = 1.0 if digest[1] % 2 == 0 else -1.0
            weight = 1.0 + (digest[2] / 255.0)
            vector[slot] += sign * weight
        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude == 0:
            return vector
        return [value / magnitude for value in vector]


@dataclass(slots=True)
class OpenAIEmbeddingProvider(EmbeddingProvider):
    provider_name: str = "openai"
    dimensions: int = 1536
    version: str = "text-embedding-3-small:1536"
    model_name: str = "text-embedding-3-small"
    api_key: str | None = None
    _client: OpenAI | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if ":" not in self.version:
            self.version = f"{self.model_name}:{self.dimensions}"

    def _client_or_raise(self) -> OpenAI:
        if self._client is not None:
            return self._client
        api_key = self.api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when REPO_MEMORY_EMBEDDING_PROVIDER=openai"
            )
        self._client = OpenAI(api_key=api_key)
        return self._client

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        vectors = [[0.0] * self.dimensions for _ in texts]
        pending: list[tuple[int, str]] = [
            (index, text.strip())
            for index, text in enumerate(texts)
            if text and text.strip()
        ]
        if not pending:
            return vectors

        response = self._client_or_raise().embeddings.create(
            model=self.model_name,
            input=[text for _index, text in pending],
            dimensions=self.dimensions,
        )
        for item in response.data:
            original_index = pending[item.index][0]
            vectors[original_index] = list(item.embedding)
        return vectors


def build_embedding_provider(config: RepoMemoryConfig) -> EmbeddingProvider:
    provider = config.embedding_provider.lower()
    if provider == "auto":
        provider = "openai" if os.getenv("OPENAI_API_KEY") else "hashed"
    if provider in {"hashed", "hash", "local"}:
        return HashEmbeddingProvider(
            dimensions=config.embedding_dimensions,
            version=config.embedding_version,
        )
    if provider in {"openai", "openai-api"}:
        return OpenAIEmbeddingProvider(
            dimensions=config.embedding_dimensions,
            model_name=config.embedding_model,
            version=config.embedding_version,
        )
    raise ValueError(f"Unsupported repo-memory embedding provider: {config.embedding_provider}")


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    return dot / (left_norm * right_norm)
