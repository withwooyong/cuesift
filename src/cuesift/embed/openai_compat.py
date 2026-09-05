"""OpenAI 호환 `/v1/embeddings` 어댑터 (FR-4.2 · 설계 §4.2).

**상태 코드 분류가 `translate/openai_compat.py`와 다르다.** 그쪽은 501을
5xx로 묶어 재시도 대상으로 보내는데, 채팅 경로에서는 게이트웨이의 일시적
응답일 수 있어 옳다. 임베딩 경로에서 501은 **모델이 임베딩을 못 한다**는
뜻이라 재시도해도 영원히 같다 - 실측(2026-09-04, 로컬 Ollama)이 그렇고,
대조군 `/v1/nonexistent`가 404인 것으로 "경로는 있다"를 확인했다.

`translate` 패키지에서 아무것도 임포트하지 않는다. `embed/`를 `translate/`와
남남으로 두는 것이 설계의 취지다(`provider.py` 참고) - 상수 하나(`_SERVER_ERROR_MIN_STATUS`)와
`_RETRYABLE_STATUS`가 그쪽과 값이 같아 보여도 따로 정의하는 이유가 그것이다.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from cuesift.embed.provider import (
    EmbeddingNotFoundError,
    EmbeddingUnsupportedError,
    FatalEmbeddingError,
    RetryableEmbeddingError,
)

DEFAULT_TIMEOUT_S = 60.0
_ERROR_BODY_CHARS = 200
_PROBE_TEXT = "cuesift"

# 4xx 중 재시도하는 둘. 429가 빠지면 일시적 rate limit 하나가 실행 전체를
# 중단시키고, 408(Request Timeout)이 빠지면 게이트웨이가 스스로 끊은 요청이
# Fatal로 승격돼 같은 일이 벌어진다. `translate/openai_compat.py:67`과 값을
# 맞춘다 - 브리프 초안은 409·425도 넣었으나 설계 스펙 §4.2가 정한 재시도
# 대상은 503과 타임아웃뿐이고 두 코드를 넣을 근거가 없다. 같은 저장소의
# 두 어댑터가 같은 상태 코드를 다르게 분류하면 나중에 한쪽만 고쳐져 갈린다.
_RETRYABLE_STATUS = frozenset({408, 429})

# 이 위(501 제외)를 전부 재시도한다. 501을 이 검사보다 먼저 걸러내지 않으면
# 501이 이 조건에 걸려 영원히 안 될 요청을 재시도 횟수만큼 반복한 뒤
# "일시적 장애"라고 보고한다 - `_raise_for_embedding_status`의 순서가 그래서
# 501 검사를 먼저 둔다.
_SERVER_ERROR_MIN_STATUS = 500


class OpenAICompatibleEmbedder:
    """`Embedder` 프로토콜의 구현 (FR-4.2).

    `name` 속성을 두지 않는다 - `provider.py`의 `Embedder` 프로토콜은
    `embed`·`close` 두 메서드만 요구하고, 계획서에 있던 `name: str` 멤버는
    리뷰에서 스펙에 없다는 것이 확인돼 Task 1에서 삭제됐다.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if client is not None and timeout is not None:
            # 함께 주면 timeout이 조용히 무시된다. 호출부는 설정했다고
            # 믿는데 다른 값이 쓰인다 (translate 어댑터와 같은 계약).
            raise ValueError("client를 주면 timeout은 그 클라이언트의 것이다. 함께 줄 수 없다")
        self._base_url = base_url.rstrip("/")
        # 끝의 슬래시를 정리하지 않으면 `//embeddings`가 되고, 경로를 정확히
        # 매칭하는 게이트웨이가 404를 낸다 - 원인은 슬래시 하나인데 사용자는
        # "이 백엔드는 임베딩이 없다"로 읽는다.
        self._endpoint = f"{self._base_url}/embeddings"
        self._model = model
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=DEFAULT_TIMEOUT_S if timeout is None else timeout
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """입력 순서 그대로 벡터를 낸다 (FR-4.2)."""
        if not texts:
            # 빈 입력에 요청을 보내면 서버에 따라 400이 오고, 그것이
            # FatalEmbeddingError로 승격돼 실행이 죽는다.
            return []
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = self._client.post(
                self._endpoint,
                json={"model": self._model, "input": list(texts)},
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            # 로컬 Ollama가 긴 입력에서 ReadTimeout을 낸다(실측). 재시도
            # 가능으로 분류하지 않으면 한 건의 타임아웃이 측정 전체를 죽인다.
            raise RetryableEmbeddingError(f"타임아웃: {exc}") from exc
        except httpx.HTTPError as exc:
            raise RetryableEmbeddingError(f"전송 실패: {exc}") from exc
        _raise_for_embedding_status(response)
        return _extract_vectors(response, expected=len(texts))

    def probe(self) -> int:
        """임베딩이 실제로 되는지 한 번 확인하고 차원 수를 낸다 (설계 §7).

        **Tier 1 실행 전에 부른다.** 뒤로 미루면 비싼 역번역을 수백 회 한 뒤
        유사도 단계에서 전부 버리게 된다.
        """
        vectors = self.embed([_PROBE_TEXT])
        return len(vectors[0])

    def close(self) -> None:
        # 주입받은 클라이언트는 우리 것이 아니므로 건드리지 않는다.
        if self._owns_client:
            self._client.close()


def _raise_for_embedding_status(response: httpx.Response) -> None:
    """상태 코드를 네 갈래로 가른다 (설계 §4.2).

    **501 검사가 5xx 검사보다 먼저 와야 한다.** 순서가 뒤집히면 501이
    재시도 대상으로 분류돼, 영원히 안 될 요청을 재시도 횟수만큼 반복한 뒤
    "일시적 장애"라고 보고한다.
    """
    status = response.status_code
    if status < 300:
        return
    body = response.text[:_ERROR_BODY_CHARS]
    if status == 501:
        raise EmbeddingUnsupportedError(
            f"501: 이 모델은 임베딩을 내지 못한다. --embed-model에 임베딩 모델을 지정하라 ({body})"
        )
    if status == 404:
        raise EmbeddingNotFoundError(
            f"404: {response.request.url}에 임베딩 엔드포인트가 없다 ({body})"
        )
    if status in _RETRYABLE_STATUS or status >= _SERVER_ERROR_MIN_STATUS:
        raise RetryableEmbeddingError(f"{status}: {body}")
    raise FatalEmbeddingError(f"{status}: {body}")


def _extract_vectors(response: httpx.Response, *, expected: int) -> list[list[float]]:
    """응답에서 벡터를 꺼내 **입력 순서로** 정렬한다.

    `index`로 정렬하지 않으면 서버가 순서를 바꿔 보낼 때 원문과 역번역문의
    벡터가 뒤바뀐다. 코사인은 대칭이라 점수는 같지만 **원자료 기록이
    거짓이 되어** 다음 사람이 재현할 때 다른 결론에 이른다.
    """
    try:
        body = response.json()
    except ValueError as exc:
        raise FatalEmbeddingError(
            f"응답이 JSON이 아니다: {response.text[:_ERROR_BODY_CHARS]}"
        ) from exc
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise FatalEmbeddingError(f"data 배열이 없다: {str(body)[:_ERROR_BODY_CHARS]}")
    items = body["data"]
    if len(items) != expected:
        raise FatalEmbeddingError(f"{expected}개를 요청했는데 {len(items)}개가 왔다")
    ordered: list[list[float]] = [[] for _ in range(expected)]
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            raise FatalEmbeddingError(f"data 원소가 객체가 아니다: {str(item)[:_ERROR_BODY_CHARS]}")
        index = item.get("index", position)
        if not isinstance(index, int) or not 0 <= index < expected:
            raise FatalEmbeddingError(f"index가 범위를 벗어났다: {index}")
        vector = item.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise FatalEmbeddingError(f"embedding이 비었다 (index={index})")
        ordered[index] = [float(x) for x in vector]
    return ordered
