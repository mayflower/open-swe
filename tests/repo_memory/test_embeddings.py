from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.embeddings import OpenAIEmbeddingProvider, build_embedding_provider


def test_build_embedding_provider_returns_openai_provider_with_explicit_contract() -> None:
    config = RepoMemoryConfig(
        embedding_provider="openai",
        embedding_dimensions=8,
        embedding_model="text-embedding-3-small",
        embedding_version="text-embedding-3-small:8",
    )

    provider = build_embedding_provider(config)

    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.provider_name == "openai"
    assert provider.dimensions == 8
    assert provider.version == "text-embedding-3-small:8"


def test_openai_embedding_provider_batches_non_blank_texts_only() -> None:
    client = MagicMock()
    client.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(index=0, embedding=[0.25, 0.5, 0.75, 1.0])]
    )

    with patch("agent.repo_memory.embeddings.OpenAI", return_value=client):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False):
            provider = OpenAIEmbeddingProvider(
                dimensions=4,
                model_name="text-embedding-3-small",
                version="text-embedding-3-small:4",
            )
            vectors = provider.embed_many(["normalize helper", "   "])

    assert vectors == [[0.25, 0.5, 0.75, 1.0], [0.0, 0.0, 0.0, 0.0]]
    client.embeddings.create.assert_called_once_with(
        model="text-embedding-3-small",
        input=["normalize helper"],
        dimensions=4,
    )
