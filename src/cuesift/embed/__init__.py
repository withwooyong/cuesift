"""임베딩 계층 (FR-4.2)."""

from __future__ import annotations

from cuesift.embed.cosine import cosine
from cuesift.embed.openai_compat import OpenAICompatibleEmbedder
from cuesift.embed.provider import (
    Embedder,
    EmbeddingError,
    EmbeddingNotFoundError,
    EmbeddingUnsupportedError,
    FatalEmbeddingError,
    RetryableEmbeddingError,
)

__all__ = [
    "Embedder",
    "EmbeddingError",
    "EmbeddingNotFoundError",
    "EmbeddingUnsupportedError",
    "FatalEmbeddingError",
    "OpenAICompatibleEmbedder",
    "RetryableEmbeddingError",
    "cosine",
]
