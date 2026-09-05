"""임베딩 프로바이더의 계약 (FR-4.2 · 설계 §4.1).

**`translate.Provider`와 형제가 아니라 남남이다.** 반환형이 `Completion`이
아니고 메시지 개념도 없으며 온도·max_tokens를 하나도 공유하지 않는다.
억지로 묶으면 한쪽에서만 의미 있는 인자가 다른 쪽 서명에 남는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


class EmbeddingError(Exception):
    """임베딩 계층의 모든 예외의 뿌리."""


class EmbeddingUnsupportedError(EmbeddingError):
    """경로는 있는데 모델이 임베딩을 못 한다 (HTTP 501).

    **`EmbeddingNotFoundError`와 합치면 안 된다.** 사용자가 취할 행동이
    정반대다 - 이쪽은 임베딩 모델을 지정하면 해결되고, 저쪽은 백엔드를
    바꿔야 한다. 실측(2026-09-04): `bge-m3`를 지정하지 않은 로컬 Ollama가
    `/v1/embeddings`에 501을 냈고, 대조군 `/v1/nonexistent`는 404였다.
    """


class EmbeddingNotFoundError(EmbeddingError):
    """엔드포인트 자체가 없다 (HTTP 404)."""


class RetryableEmbeddingError(EmbeddingError):
    """다시 걸면 될 수 있다 (503 · 타임아웃)."""


class FatalEmbeddingError(EmbeddingError):
    """다시 걸어도 같다 (401 인증 · 400 스키마)."""


@runtime_checkable
class Embedder(Protocol):
    """텍스트를 벡터로 만든다 (FR-4.2)."""

    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """입력 순서 그대로 벡터를 낸다.

        **개수가 입력과 다르면 구현이 예외를 던져야 한다.** 호출부가
        `v_source, v_back = embed([a, b])`로 언패킹하므로, 하나만 오면
        `ValueError`가 나기는 하지만 원인이 임베딩 서버라는 것이 메시지에
        드러나지 않는다.
        """
        ...

    def close(self) -> None:
        """보유한 자원을 정리한다."""
        ...
