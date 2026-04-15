from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from .config import RepoMemoryConfig

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


class EmbeddingProvider:
    dimensions: int

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


@dataclass(slots=True)
class HashEmbeddingProvider(EmbeddingProvider):
    dimensions: int = 16

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


def build_embedding_provider(config: RepoMemoryConfig) -> EmbeddingProvider:
    provider = config.embedding_provider.lower()
    if provider in {"hashed", "hash", "local"}:
        return HashEmbeddingProvider(dimensions=config.embedding_dimensions)
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
