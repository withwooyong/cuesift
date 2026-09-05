"""`/v1/embeddings` 어댑터 (FR-4.2 · 설계 §4.2).

`httpx.MockTransport`를 쓴다 - httpx가 이미 런타임 의존성이라 의존성 추가
없이 HTTP 계층을 전부 검증할 수 있다. 실제 네트워크는 한 번도 치지 않는다.

**이 파일이 지키는 것은 "예외가 네 갈래로 갈리는가" 하나다.** 특히 501과
404를 합치면 사용자가 취할 행동이 뒤바뀐다.
"""

from __future__ import annotations

import httpx
import pytest

from cuesift.embed import (
    EmbeddingNotFoundError,
    EmbeddingUnsupportedError,
    FatalEmbeddingError,
    RetryableEmbeddingError,
)
from cuesift.embed.openai_compat import OpenAICompatibleEmbedder


def _embedder(handler) -> OpenAICompatibleEmbedder:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleEmbedder(
        base_url="http://localhost:11434/v1", model="bge-m3", client=client
    )


def _ok_body(vectors: list[list[float]]) -> dict:
    return {"data": [{"embedding": v, "index": i} for i, v in enumerate(vectors)]}


def test_입력_순서대로_벡터를_낸다():
    embedder = _embedder(lambda r: httpx.Response(200, json=_ok_body([[1.0, 0.0], [0.0, 1.0]])))
    assert embedder.embed(["가", "나"]) == [[1.0, 0.0], [0.0, 1.0]]


def test_index로_정렬한다():
    # 서버가 순서를 뒤집어 보내도 입력 순서로 맞춘다. 뒤집힌 채 쓰면
    # 원문과 역번역문이 바뀌어 코사인은 같지만 원자료 기록이 거짓이 된다.
    body = {"data": [{"embedding": [0.0, 1.0], "index": 1}, {"embedding": [1.0, 0.0], "index": 0}]}
    embedder = _embedder(lambda r: httpx.Response(200, json=body))
    assert embedder.embed(["가", "나"]) == [[1.0, 0.0], [0.0, 1.0]]


def test_개수가_모자라면_거부한다():
    embedder = _embedder(lambda r: httpx.Response(200, json=_ok_body([[1.0, 0.0]])))
    with pytest.raises(FatalEmbeddingError, match="2개를 요청했는데 1개"):
        embedder.embed(["가", "나"])


def test_501은_능력_부재다():
    embedder = _embedder(lambda r: httpx.Response(501, text="not implemented"))
    with pytest.raises(EmbeddingUnsupportedError):
        embedder.embed(["가"])


def test_404는_경로_부재다():
    # **501과 갈려야 한다.** 없는 것과 못 하는 것은 대응이 정반대다.
    embedder = _embedder(lambda r: httpx.Response(404, text="not found"))
    with pytest.raises(EmbeddingNotFoundError):
        embedder.embed(["가"])


def test_503은_재시도_대상이다():
    embedder = _embedder(lambda r: httpx.Response(503, text="busy"))
    with pytest.raises(RetryableEmbeddingError):
        embedder.embed(["가"])


def test_401은_치명적이다():
    embedder = _embedder(lambda r: httpx.Response(401, text="unauthorized"))
    with pytest.raises(FatalEmbeddingError):
        embedder.embed(["가"])


def test_probe는_차원을_낸다():
    embedder = _embedder(lambda r: httpx.Response(200, json=_ok_body([[1.0, 0.0, 0.0]])))
    assert embedder.probe() == 3


def test_빈_입력은_호출하지_않는다():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("빈 입력에 요청이 나갔다")

    assert _embedder(handler).embed([]) == []


def test_슬래시가_겹치지_않는다():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_ok_body([[1.0]]))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    OpenAICompatibleEmbedder(
        base_url="http://localhost:11434/v1/", model="bge-m3", client=client
    ).embed(["가"])
    assert seen == ["http://localhost:11434/v1/embeddings"]
