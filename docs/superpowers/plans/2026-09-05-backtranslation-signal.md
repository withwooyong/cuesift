# FR-4.2 역번역 유사도 신호 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 번역문을 원문 언어로 역번역하고 임베딩 코사인 유사도로 의미 반전을 잡는 Tier 1 신호를 구현하고, 벤치마크로 그 효과를 실측한다.

**Architecture:** 임베딩 호출을 `embed/` 패키지로 분리해 기존 `Provider`(채팅) 프로토콜과 남남으로 둔다. 신호는 기존 `translate_segments` 를 번역 방향만 뒤집어 재사용하므로 재시도·실패 분류가 공짜로 따라온다. 벤치는 `--tier1` 이 꺼져 있으면 지금과 한 줄도 다르지 않게 돈다.

**Tech Stack:** Python 3.11+ · httpx · typer · pytest. **의존성을 추가하지 않는다.**

**Spec:** [`docs/superpowers/specs/2026-09-05-backtranslation-signal-design.md`](../specs/2026-09-05-backtranslation-signal-design.md)

## Global Constraints

- Python 실행은 반드시 `.venv/Scripts/python.exe` 를 쓴다. 시스템 Python 은 3.14 라 다르다
- **의존성은 고정이다.** 런타임 4개(`typer`·`pysubs2`·`pyyaml`·`httpx`), dev 3개(`pytest`·`pytest-cov`·`ruff`). 추가하지 않는다
- 모든 모듈 첫 줄에 `from __future__ import annotations`
- 독스트링과 주석은 **한국어**, 근거 FR·§ 번호를 병기한다 (예: `FR-4.2`, `§5.1`)
- 주석에는 "왜 이 값인가"가 아니라 **"이 값이 아니면 무엇이 깨지는가"** 를 적는다
- ruff: `line-length = 100`, 규칙 `E,F,I,UP,B,SIM`
- 커밋 메시지는 **한국어**. 커밋과 푸시를 한 명령에 묶지 않는다
- 게이트는 CI 와 같은 대상 `.` 으로 돌린다. **`src tests` 로 좁히면 안 된다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
python scripts/check_links.py
npx --yes markdownlint-cli2
```

- 게이트는 "통과했나"가 아니라 **"무엇을 대상으로 통과했나"** 를 본다. `pytest` 수집 개수와 markdownlint 의 `Linting: N files` 를 매번 읽는다
- **게이트를 만들면 반드시 실패시켜 봐야 한다.** 회귀 테스트는 버그 코드에서 실제로 실패하는 것을 확인한 뒤에야 회귀 테스트다

## 파일 구조

| 파일 | 책임 | 태스크 |
| --- | --- | --- |
| `src/cuesift/embed/__init__.py` | 공개 이름 재수출 | 1 |
| `src/cuesift/embed/provider.py` | `Embedder` 프로토콜 · 예외 4종 | 1 |
| `src/cuesift/embed/cosine.py` | 코사인 유사도 (표준 라이브러리만) | 1 |
| `src/cuesift/embed/openai_compat.py` | `/v1/embeddings` httpx 구현 · `probe()` | 2 |
| `src/cuesift/signals/base.py` | `Tier1Context` 에 `embedder` 필드 추가 | 3 |
| `src/cuesift/signals/backtranslation.py` | `BackTranslation` 신호 | 3 |
| `src/cuesift/tier1.py` | `triage_with_tier1` 에 `embedder` 배선 | 4 |
| `src/cuesift/cli.py` | `--embed-*` 옵션 · 가용성 탐지 | 5 |
| `bench/classify_negation.py` | 정답지 잡음 분류 | 6 |
| `bench/run.py` · `bench/report.py` | Tier 1 측정 · 원자료 · 비교표 | 7 |

---

### Task 1: 임베딩 프로토콜과 코사인 유사도

**Files:**

- Create: `src/cuesift/embed/__init__.py`
- Create: `src/cuesift/embed/provider.py`
- Create: `src/cuesift/embed/cosine.py`
- Test: `tests/test_embed_cosine.py`

**Interfaces:**

- Consumes: 없다 (가장 안쪽 계층)
- Produces:
  - `Embedder` 프로토콜 — `embed(texts: Sequence[str]) -> list[list[float]]` · `close() -> None`
  - `EmbeddingError` · `EmbeddingUnsupportedError` · `EmbeddingNotFoundError` · `RetryableEmbeddingError` · `FatalEmbeddingError`
  - `cosine(a: Sequence[float], b: Sequence[float]) -> float`

- [ ] **Step 1: 코사인 유사도의 실패 테스트를 쓴다**

`tests/test_embed_cosine.py` 를 만든다.

```python
"""코사인 유사도 (FR-4.2 · 설계 §5.1).

**손으로 계산한 값과 대조한다.** 라이브러리를 부르지 않으므로 기댓값을
구현과 같은 방법으로 만들면 서로를 검증하지 못한다.
"""

from __future__ import annotations

import math

import pytest

from cuesift.embed import cosine


def test_같은_벡터는_1이다():
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_직교_벡터는_0이다():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_반대_방향은_음수다():
    # **치역이 [-1, 1]이라는 것이 이 테스트의 요점이다.** 문자 단위
    # `similarity`는 [0, 1]이었고, 신호가 쓰는 clamp가 이제 실제로
    # 값을 자른다 (설계 §5.1).
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_손계산값과_일치한다():
    # a·b = 1*3 + 2*4 = 11,  |a| = sqrt(5),  |b| = 5
    expected = 11.0 / (math.sqrt(5.0) * 5.0)
    assert cosine([1.0, 2.0], [3.0, 4.0]) == pytest.approx(expected)


def test_영벡터는_거부한다():
    # **0.0을 내면 "완전히 다르다"로 읽혀 위험도 1.0이 된다.** 실제로는
    # 방향이 없어 판정이 불가능한 것이고, 둘은 다르다.
    with pytest.raises(ValueError, match="영벡터"):
        cosine([0.0, 0.0], [1.0, 2.0])


def test_차원이_다르면_거부한다():
    # 임베딩 모델이 바뀌면 차원이 달라진다. 조용히 짧은 쪽에 맞추면
    # 캐시나 설정 실수가 "유사도가 낮다"로 위장된다.
    with pytest.raises(ValueError, match="차원"):
        cosine([1.0, 2.0], [1.0, 2.0, 3.0])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_embed_cosine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cuesift.embed'`

- [ ] **Step 3: 예외 계층과 프로토콜을 쓴다**

`src/cuesift/embed/provider.py`:

```python
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
```

`src/cuesift/embed/cosine.py`:

```python
"""코사인 유사도 (FR-4.2 · 설계 §5.1).

**넘파이를 쓰지 않는다.** 의존성이 런타임 4개로 고정돼 있고, 벡터 하나가
1024차원이라 순수 파이썬으로도 벤치 최대 규모(1,000회)에서 무시할 수 있다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """두 벡터의 코사인 유사도 -1.0~1.0 (FR-4.2).

    **치역이 [-1, 1]이라 호출부의 clamp가 실제로 값을 자른다.** 문자 단위
    `signals.similarity`가 [0, 1]이었던 것과 다르고, `signals/llm.py`의
    clamp 주석이 §12 Q4가 닫히면 벌어질 일로 예견해 둔 상황이다.
    """
    if len(a) != len(b):
        # 임베딩 모델이 바뀌면 차원이 달라진다. 짧은 쪽에 맞춰 자르면
        # 설정 실수가 "유사도가 낮다"로 위장돼 위험도로 새어 든다.
        raise ValueError(f"차원이 다르다: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        # **0.0을 내면 "완전히 다르다"가 되어 위험도 1.0이 된다.**
        # 실제로는 방향이 없어 판정 불가이고, 판정 불가와 최고 위험은 다르다.
        raise ValueError("영벡터는 방향이 없어 코사인이 정의되지 않는다")
    return dot / (norm_a * norm_b)
```

`src/cuesift/embed/__init__.py`:

```python
"""임베딩 계층 (FR-4.2)."""

from __future__ import annotations

from cuesift.embed.cosine import cosine
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
    "RetryableEmbeddingError",
    "cosine",
]
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_embed_cosine.py -v`
Expected: PASS 6건

- [ ] **Step 5: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/embed tests/test_embed_cosine.py
git commit -m "구현: 임베딩 프로토콜과 코사인 유사도를 더한다 (FR-4.2)"
```

---

### Task 2: OpenAI 호환 임베딩 어댑터

**Files:**

- Create: `src/cuesift/embed/openai_compat.py`
- Modify: `src/cuesift/embed/__init__.py` (재수출 추가)
- Test: `tests/test_embed_openai_compat.py`

**Interfaces:**

- Consumes: Task 1 의 예외 4종
- Produces: `OpenAICompatibleEmbedder(*, base_url: str, model: str, api_key: str | None = None, timeout: float | None = None, client: httpx.Client | None = None)` · `.embed(texts) -> list[list[float]]` · `.probe() -> int` · `.close() -> None`

**핵심:** 기존 `translate/openai_compat.py` 의 `_raise_for_status` 는 **501 을 5xx 로 묶어 재시도 대상으로 분류한다.** 채팅 경로에서는 옳지만 임베딩 경로에서 501 은 "모델이 못 한다"라 재시도해도 같다. 그래서 501 검사가 5xx 검사보다 **먼저** 와야 한다.

- [ ] **Step 1: 실패 테스트를 쓴다**

`tests/test_embed_openai_compat.py`:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_embed_openai_compat.py -v`
Expected: FAIL — `ModuleNotFoundError: cuesift.embed.openai_compat`

- [ ] **Step 3: 어댑터를 구현한다**

`src/cuesift/embed/openai_compat.py`:

```python
"""OpenAI 호환 `/v1/embeddings` 어댑터 (FR-4.2 · 설계 §4.2).

**상태 코드 분류가 `translate/openai_compat.py`와 다르다.** 그쪽은 501을
5xx로 묶어 재시도 대상으로 보내는데, 채팅 경로에서는 게이트웨이의 일시적
응답일 수 있어 옳다. 임베딩 경로에서 501은 **모델이 임베딩을 못 한다**는
뜻이라 재시도해도 영원히 같다 - 실측(2026-09-04, 로컬 Ollama)이 그렇고,
대조군 `/v1/nonexistent`가 404인 것으로 "경로는 있다"를 확인했다.
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
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429})


class OpenAICompatibleEmbedder:
    """`Embedder` 프로토콜의 구현."""

    name = "openai-compatible-embed"

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
    if status in _RETRYABLE_STATUS or status >= 500:
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
        raise FatalEmbeddingError(f"응답이 JSON이 아니다: {response.text[:_ERROR_BODY_CHARS]}") from exc
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
```

`src/cuesift/embed/__init__.py` 의 import 와 `__all__` 에 `OpenAICompatibleEmbedder` 를 더한다.

```python
from cuesift.embed.openai_compat import OpenAICompatibleEmbedder
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_embed_openai_compat.py -v`
Expected: PASS 10건

- [ ] **Step 5: 501 이 재시도로 분류되는 버그를 넣어 게이트를 실패시켜 본다**

`_raise_for_embedding_status` 에서 `if status == 501:` 블록을 잠시 지우고 돌린다.

Run: `.venv/Scripts/python.exe -m pytest tests/test_embed_openai_compat.py::test_501은_능력_부재다 -v`
Expected: FAIL — `RetryableEmbeddingError` 가 나온다

**확인 후 반드시 되돌린다.** 파괴 실험은 정의상 리포를 망가뜨린 채 도는 코드다.

- [ ] **Step 6: 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/embed tests/test_embed_openai_compat.py
git commit -m "구현: OpenAI 호환 임베딩 어댑터를 더한다 (FR-4.2)"
```

---

### Task 3: `BackTranslation` 신호

**Files:**

- Create: `src/cuesift/signals/backtranslation.py`
- Modify: `src/cuesift/signals/base.py` (`Tier1Context` 에 `embedder` 필드)
- Modify: `src/cuesift/signals/__init__.py` (신호 등록 import)
- Test: `tests/test_signals_backtranslation.py`

**Interfaces:**

- Consumes: Task 1 의 `Embedder`·`cosine`, Task 2 의 어댑터 (테스트에서는 가짜)
- Produces: `BackTranslation` 클래스 · `name = "llm.backtranslation"` · `tier = 1` · `Tier1Context.embedder: Embedder | None`

- [ ] **Step 1: 실패 테스트를 쓴다**

`tests/test_signals_backtranslation.py`:

```python
"""역번역 유사도 신호 (FR-4.2 · 설계 §5).

**가짜 임베더가 내는 벡터로 점수를 결정론적으로 만든다.** 실제 모델을
쓰면 값이 흔들려 경계 조건을 못 박을 수 없다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from cuesift.segment import Segment
from cuesift.signals.backtranslation import BackTranslation
from cuesift.signals.base import SignalContext, Tier1Context
from cuesift.spec.profile import load_builtin
from tests.fakes.provider import EchoProvider


class FakeEmbedder:
    """텍스트를 미리 정한 벡터로 바꾼다. 모르는 텍스트는 예외를 낸다."""

    name = "fake-embed"

    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._table[t] for t in texts]

    def close(self) -> None:
        return None


def _segment(source: str, target: str | None) -> Segment:
    return Segment(
        id="00007", index=7, start_ms=0, end_ms=2000, source_text=source, target_text=target
    )


def _ctx(embedder, provider) -> Tier1Context:
    signal = SignalContext(
        profile=load_builtin("ted-en"), glossary=None, source_lang="ko", target_lang="en"
    )
    return Tier1Context(
        signal=signal,
        provider_for=lambda attempt: provider,
        samples=2,
        temperature=1.0,
        embedder=embedder,
    )


def test_원문과_역번역이_같으면_점수가_0이다():
    seg = _segment("비가 온다", "It rains")
    # EchoProvider는 원문을 그대로 되돌려주므로 역번역문 = "It rains"가 된다.
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    signal = BackTranslation().collect_tier1(seg, _ctx(embedder, EchoProvider()))
    assert signal is not None
    assert signal.score == pytest.approx(0.0)


def test_방향이_반대면_clamp가_1에서_자른다():
    # **코사인의 치역이 [-1, 1]이라 1 - cos가 2.0까지 간다.**
    # `signals/llm.py`의 clamp 주석이 예견한 자리이며, 여기서 처음 작동한다.
    seg = _segment("비가 온다", "It rains")
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [-1.0, 0.0]})
    signal = BackTranslation().collect_tier1(seg, _ctx(embedder, EchoProvider()))
    assert signal is not None
    assert signal.score == 1.0


def test_번역이_없으면_None이다():
    seg = _segment("비가 온다", None)
    embedder = FakeEmbedder({})
    assert BackTranslation().collect_tier1(seg, _ctx(embedder, EchoProvider())) is None


def test_embedder가_없으면_예외다():
    # **None을 내면 신호가 전 구간 0건으로 끝나고 그것이 "안전"으로 읽힌다.**
    # 배선 누락은 조용히 넘어갈 사고가 아니다 (설계 D6).
    seg = _segment("비가 온다", "It rains")
    ctx = _ctx(None, EchoProvider())
    with pytest.raises(ValueError, match="embedder"):
        BackTranslation().collect_tier1(seg, ctx)


def test_역번역은_방향을_뒤집는다():
    # 프로바이더가 받은 프롬프트의 번역 방향을 확인한다. 뒤집지 않으면
    # 원본 번역 캐시에 히트해 역번역문이 번역문과 같아지고, 코사인이
    # 1.0에 붙어 신호가 전 구간 0점이 된다 (설계 §6).
    seg = _segment("비가 온다", "It rains")
    provider = EchoProvider()
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    BackTranslation().collect_tier1(seg, _ctx(embedder, provider))
    sent = "\n".join(m.content for m in provider.last_messages)
    assert "en" in sent and "ko" in sent


def test_용어집을_넘기지_않는다():
    # 용어집이 원문 어휘를 강제하면 오류 문장의 역번역도 원문에 가까워져
    # 유사도 격차가 줄고 신호가 둔해진다 (설계 D2).
    seg = _segment("비가 온다", "It rains")
    provider = EchoProvider()
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    BackTranslation().collect_tier1(seg, _ctx(embedder, provider))
    sent = "\n".join(m.content for m in provider.last_messages)
    assert "용어집" not in sent


def test_임베딩은_한_요청에_두_텍스트를_담는다():
    seg = _segment("비가 온다", "It rains")
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    BackTranslation().collect_tier1(seg, _ctx(embedder, EchoProvider()))
    assert embedder.calls == [["비가 온다", "It rains"]]


def test_detail에_역번역문과_코사인이_실린다():
    # FR-6.4 - review.json이 "왜 선별되었는지"를 이것으로 쓴다.
    seg = _segment("비가 온다", "It rains")
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    signal = BackTranslation().collect_tier1(seg, _ctx(embedder, EchoProvider()))
    assert signal is not None
    assert signal.detail["back_translation"] == "It rains"
    assert signal.detail["cosine"] == pytest.approx(1.0)


def test_hard_fail이_아니다():
    # 의미 판단은 결정론적이지 않고, hard fail 오탐은 실제 검수 비율을
    # 부풀려 Recall@Budget 지표 자체를 파괴한다 (FR-6.2).
    seg = _segment("비가 온다", "It rains")
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    signal = BackTranslation().collect_tier1(seg, _ctx(embedder, EchoProvider()))
    assert signal is not None
    assert signal.hard_fail is False
```

**주의:** `EchoProvider` 에 `last_messages` 속성이 없으면 `tests/fakes/provider.py` 에 마지막 요청 메시지를 기록하는 속성을 더한다. 기존 테스트를 깨지 않도록 **속성 추가만** 하고 기존 동작은 바꾸지 않는다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_signals_backtranslation.py -v`
Expected: FAIL — `ModuleNotFoundError: cuesift.signals.backtranslation`

- [ ] **Step 3: `Tier1Context` 에 `embedder` 를 더한다**

`src/cuesift/signals/base.py` 의 `Tier1Context` 에 필드를 **마지막에** 더한다. 기본값이 있으므로 기존 호출자가 한 곳도 깨지지 않는다.

```python
    embedder: Embedder | None = None
    """임베딩 계층 (FR-4.2 · 설계 §4).

    **팩토리가 아니라 값을 직접 담는다.** `provider_for(attempt)`가 팩토리인
    것은 자가일관성이 시도마다 캐시를 갈라야 하기 때문인데, 임베딩은
    결정론적이라 가를 것이 없다.

    기본값이 `None`인 것은 기존 호출자를 깨지 않기 위해서다. 배선이 빠진
    채로 `llm.backtranslation`이 돌면 그 신호가 예외를 던진다 - 조용히
    `None`을 내면 신호가 전 구간 0건으로 끝나고 그것이 "안전"으로 읽힌다.
    """
```

파일 상단에 `from cuesift.embed import Embedder` 를 더한다. `embed` 패키지는 `signals` 를 import 하지 않으므로 순환이 생기지 않는다.

- [ ] **Step 4: 신호를 구현한다**

`src/cuesift/signals/backtranslation.py`:

```python
"""Tier 1 신호 — 역번역 유사도 (FR-4.2 · 설계 §5).

**Tier 0가 원리적으로 못 잡는 것을 노린다.** 문법적으로 완벽한 문장의
의미가 뒤집혔는지는 결정론적 신호로 판단할 수 없다 - 2026-09-04 실측에서
`negation` Recall이 예산 10%에 1.41%로 무작위 기준선(10.28%)보다 낮았다.

**원리적 상한이 실측돼 있다.** 역번역이 제거된 부정을 문맥으로 되살리는
비율이 en 21.8% · ja 17.9%이고, 그 부류에서는 오류 문장과 정상 문장의
유사도 차이가 사실상 0이다(en -0.005 · ja +0.011). **점수 스케일을 어떻게
바꿔도 그 20%는 잡히지 않는다** - 이 신호의 Recall 목표는 80% 언저리가
상한이다.
"""

from __future__ import annotations

from dataclasses import replace

from cuesift.embed import cosine
from cuesift.segment import Segment, Signal
from cuesift.signals.base import Tier1Context, register
from cuesift.translate import translate_segments

# **0.0이 아니면 무엇이 깨지는가.** 온도를 올리면 같은 오류가 실행마다 다른
# 역번역문을 받아 점수가 흔들리고 NFR-3(재현성)이 성립하지 않는다.
# `Tier1Context.temperature`를 쓰지 않는 이유도 이것이다 - 그 필드는
# 자가일관성 전용이라 `__post_init__`이 0보다 클 것을 강제한다.
_BACKTRANSLATION_TEMPERATURE = 0.0

# 역번역은 시도를 가르지 않는다. 캐시 격리는 attempt가 아니라 **번역 방향**이
# 만든다 - 정방향은 ko->en, 역번역은 en->ko라 messages_sha가 다르다 (설계 §6).
_BACKTRANSLATION_ATTEMPT = 0


class BackTranslation:
    """FR-4.2 — 번역문을 원문 언어로 되돌려 원문과의 의미 유사도를 잰다."""

    name = "llm.backtranslation"
    tier = 1

    def collect_tier1(self, seg: Segment, ctx: Tier1Context) -> Signal | None:
        # 번역 실패분은 검수 대상이 아니라 재실행 대상이다.
        if not seg.target_text:
            return None
        if ctx.embedder is None:
            # **None을 내면 무음 열화다** (Q3 · 설계 D6). 신호가 전 구간
            # 0건으로 끝나고 그것이 "판정했고 안전하다"로 읽힌다.
            raise ValueError(
                f"{self.name}은 Tier1Context.embedder를 요구한다. "
                "CLI는 --embed-model로, 라이브러리 호출자는 인자로 배선하라"
            )

        back = self._backtranslate(seg, ctx)
        # 역번역이 실패했거나 빈 문자열이면 판정 불가다. 빈 문자열의
        # 임베딩은 영벡터가 될 수 있고 `cosine`이 거기서 예외를 낸다.
        if not back:
            return None

        vector_source, vector_back = ctx.embedder.embed([seg.source_text, back])
        similarity = cosine(vector_source, vector_back)

        return Signal(
            name=self.name,
            tier=1,
            # **이 clamp가 이번에 처음으로 실제 값을 자른다.** 코사인의
            # 치역이 [-1, 1]이라 원문과 역번역이 의미상 반대인 극단에서
            # `1.0 - similarity`가 2.0까지 간다. 문자 단위 similarity는
            # [0, 1]이라 clamp가 발동한 적이 없었다.
            score=min(1.0, max(0.0, 1.0 - similarity)),
            # hard fail로 두지 않는다. 의미 판단은 결정론적이지 않고,
            # hard fail 오탐은 실제 검수 비율을 부풀려 Recall@Budget 지표
            # 자체를 파괴한다 (FR-6.2).
            hard_fail=False,
            detail={
                # FR-6.4 - review.json이 "왜 선별되었는지"를 이것으로 쓴다.
                # 역번역문 자체를 싣는 이유는 검수자가 점수만 보고는
                # 판정을 재현할 수 없기 때문이다.
                "back_translation": back,
                "cosine": similarity,
                "temperature": _BACKTRANSLATION_TEMPERATURE,
            },
        )

    def _backtranslate(self, seg: Segment, ctx: Tier1Context) -> str | None:
        """번역문을 원문 언어로 되돌린다 (설계 §5.1).

        **`translate_segments`를 방향만 뒤집어 재사용한다.** 재사용되는 것은
        재시도와 실패 분류다 - `RetryableProviderError`는 이미 삼켜져
        `target_text=None`으로 오고, `FatalProviderError`(401 등)는 일부러
        전파된다. 여기서 포괄 `except`로 둘을 함께 삼키면 401이 이 신호를
        전 구간 0건으로 조용히 만든다.

        **용어집을 넘기지 않는다** (설계 D2). 용어집이 원문 어휘를 강제하면
        오류 문장의 역번역도 원문에 가까워져 유사도 격차가 줄어든다.

        **`index=0`으로 재번호한다.** 약한 모델이 항목 하나짜리 요청에서
        프롬프트 예시의 `{"id": 0}`을 그대로 베끼는 것이 실측돼 있고
        (`signals/llm.py`의 Ruling P13), 그러면 `parse_translations`가
        "id가 누락됐다"로 거부한다.
        """
        local_seg = replace(seg, index=0, source_text=seg.target_text, target_text=None)
        result = translate_segments(
            [local_seg],
            provider=ctx.provider_for(_BACKTRANSLATION_ATTEMPT),
            # **방향이 뒤집힌다.** 이것이 캐시 격리의 근거이기도 하다.
            source_lang=ctx.signal.target_lang,
            target_lang=ctx.signal.source_lang,
            glossary=None,
            temperature=_BACKTRANSLATION_TEMPERATURE,
        )
        for translated in result.segments:
            if translated.target_text:
                return translated.target_text
        return None


register(BackTranslation())
```

`src/cuesift/signals/__init__.py` 에 등록 import 를 더한다. 기존 파일이 `signals.llm` 을 import 하는 방식을 그대로 따른다.

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_signals_backtranslation.py -v`
Expected: PASS 9건

- [ ] **Step 6: 캐시 격리 회귀 테스트를 더한다**

`tests/test_signals_backtranslation.py` 에 이어 붙인다. **이것이 이 작업의 최우선 게이트다**(설계 §9.1).

```python
def test_역번역과_정방향_번역이_같은_캐시를_쓰지_않는다(tmp_path):
    """온도가 둘 다 0.0인데도 캐시가 섞이지 않는 것을 못 박는다 (설계 §6).

    `store/cache.py`의 키 주석은 "Tier 1이 temperature=0.0으로 불리면
    성질이 깨진다"고 경고한다. 역번역이 그 조건에 정확히 해당하는데도
    안전한 이유는 **번역 방향이 반대라 messages_sha가 다르기** 때문이다.

    **누군가 역번역을 같은 방향으로 바꾸면 이 테스트가 실패해야 한다.**
    바뀌면 정방향 번역 캐시에 히트해 역번역문이 번역문과 같아지고,
    코사인이 1.0에 붙어 신호가 전 구간 0점이 된다.
    """
    from cuesift.store.cache import CacheRequest
    from cuesift.translate.prompt import build_messages

    forward = CacheRequest(
        identity="test|model",
        temperature=0.0,
        max_tokens=None,
        messages=tuple(build_messages_for("ko", "en", "비가 온다")),
    )
    backward = CacheRequest(
        identity="test|model",
        temperature=0.0,
        max_tokens=None,
        messages=tuple(build_messages_for("en", "ko", "It rains")),
    )
    assert forward.key != backward.key
```

`build_messages_for` 는 `cuesift.translate.prompt` 의 실제 조립 함수를 부르는 얇은 헬퍼로 같은 파일 안에 둔다. **프롬프트를 테스트가 직접 지어 넘기면 안 된다** — 리포트 caveat 두 건이 그렇게 해서 코드와 갈라진 채 1,792건이 통과한 전례가 있다. 실제 조립 함수의 시그니처는 구현 시점에 `src/cuesift/translate/prompt.py` 에서 확인해 맞춘다.

- [ ] **Step 7: 격리를 깨서 게이트가 실제로 실패하는지 확인한다**

`_backtranslate` 의 `source_lang`·`target_lang` 을 정방향과 같게 잠시 바꾸고 돌린다.

Run: `.venv/Scripts/python.exe -m pytest tests/test_signals_backtranslation.py -v`
Expected: `test_역번역은_방향을_뒤집는다` 가 FAIL

**확인 후 반드시 되돌린다.**

- [ ] **Step 8: 전체 스위트를 돌리고 커밋한다**

수집 개수가 늘어난 것 외에 **기존 테스트가 하나도 깨지지 않아야 한다.** `Tier1Context` 에 필드를 더했으므로 그것이 기본값으로 흡수됐는지 여기서 드러난다.

```bash
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/signals tests/test_signals_backtranslation.py tests/fakes/provider.py
git commit -m "구현: 역번역 유사도 신호를 더한다 (FR-4.2)"
```

---

### Task 4: `tier1.py` 배선

**Files:**

- Modify: `src/cuesift/tier1.py` (`triage_with_tier1` 시그니처와 `Tier1Context` 생성부 `tier1.py:221-226`)
- Test: `tests/test_tier1.py`

**Interfaces:**

- Consumes: Task 3 의 `Tier1Context.embedder`
- Produces: `triage_with_tier1(..., embedder: Embedder | None = None)`

- [ ] **Step 1: 실패 테스트를 쓴다**

`tests/test_tier1.py` 에 더한다.

```python
def test_embedder가_Tier1Context로_전달된다():
    """배선이 빠지면 역번역 신호가 예외를 던진다 (설계 D6).

    **`triage_with_tier1`이 받고도 안 넘기는 실수가 조용하다.** 신호가
    없으면 아무 일도 안 일어나므로, 전달 자체를 직접 확인한다.
    """
    seen: list[object] = []

    class Probe:
        name = "probe"
        tier = 1

        def collect_tier1(self, seg, ctx):
            seen.append(ctx.embedder)
            return None

    sentinel = object()
    with registered(Probe()):
        triage_with_tier1(
            segments_fixture(),
            signal_ctx_fixture(),
            budget_ratio=0.5,
            provider=EchoProvider(),
            max_ratio=1.0,
            warn=lambda m: None,
            embedder=sentinel,
        )
    assert seen and seen[0] is sentinel
```

`registered`·`segments_fixture`·`signal_ctx_fixture` 는 `tests/test_tier1.py` 에 이미 있는 도우미를 쓴다. 이름이 다르면 그 파일의 기존 것을 그대로 쓴다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tier1.py -k embedder -v`
Expected: FAIL — `triage_with_tier1() got an unexpected keyword argument 'embedder'`

- [ ] **Step 3: 배선한다**

`src/cuesift/tier1.py` 의 시그니처에 키워드 인자를 더하고 `Tier1Context(...)` 생성부에 넘긴다.

```python
    embedder: Embedder | None = None,
```

```python
    tier1_ctx = Tier1Context(
        signal=ctx,
        provider_for=_provider_factory(provider, cache_dir=cache_dir, identity=identity),
        samples=samples,
        temperature=temperature,
        embedder=embedder,
    )
```

독스트링에 한 문단을 더한다.

```text
    **`embedder`가 없으면 `llm.backtranslation`이 예외를 던진다** (FR-4.2 ·
    설계 D6). 여기서 미리 막지 않는 이유는 어느 tier 1 수집기가 켜졌는지를
    이 함수가 모르기 때문이다 - 자가일관성만 도는 실행에는 임베딩이 필요
    없다. 가용성 탐지는 CLI가 한다.
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tier1.py -v`
Expected: PASS (기존 것 포함 전부)

- [ ] **Step 5: 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/tier1.py tests/test_tier1.py
git commit -m "구현: triage_with_tier1에 임베딩 계층을 배선한다 (FR-4.2)"
```

---

### Task 5: CLI 옵션과 가용성 탐지

**Files:**

- Modify: `src/cuesift/cli.py` (`--tier1` 분기 근처 `cli.py:1483-1580`)
- Test: `tests/test_cli_tier1.py`

**Interfaces:**

- Consumes: Task 2 의 `OpenAICompatibleEmbedder.probe()`, Task 4 의 `embedder` 인자
- Produces: `--embed-base-url` · `--embed-model` 옵션

- [ ] **Step 1: 실패 테스트를 쓴다**

`tests/test_cli_tier1.py` 에 더한다.

```python
def test_tier1인데_embed_model이_없으면_거부한다(tmp_path):
    """역번역 신호가 임베딩을 요구하므로 시작 전에 막는다 (설계 D7).

    **뒤로 미루면 비싼 역번역을 수백 회 한 뒤 전부 버리게 된다.**
    """
    result = run_cli(["triage", str(fixture_srt(tmp_path)), "--tier1", "--review-budget", "0.1"])
    assert result.exit_code == 2
    assert "--embed-model" in result.stderr


def test_임베딩_501이면_역번역_전에_멈춘다(tmp_path, monkeypatch):
    """501은 "모델이 못 한다"이므로 임베딩 모델을 지정하라고 안내한다."""
    calls: list[str] = []

    def fake_probe(self):
        calls.append("probe")
        raise EmbeddingUnsupportedError("501: 이 모델은 임베딩을 내지 못한다")

    monkeypatch.setattr(OpenAICompatibleEmbedder, "probe", fake_probe)
    result = run_cli([...,"--tier1", "--embed-model", "qwen2.5:3b", "--review-budget", "0.1"])
    assert result.exit_code == 2
    assert calls == ["probe"]
    assert "임베딩 모델" in result.stderr


def test_임베딩_404면_다른_메시지다(tmp_path, monkeypatch):
    """없는 것과 못 하는 것은 대응이 정반대다 (설계 §4.2)."""

    def fake_probe(self):
        raise EmbeddingNotFoundError("404: 임베딩 엔드포인트가 없다")

    monkeypatch.setattr(OpenAICompatibleEmbedder, "probe", fake_probe)
    result = run_cli([..., "--tier1", "--embed-model", "bge-m3", "--review-budget", "0.1"])
    assert result.exit_code == 2
    assert "엔드포인트" in result.stderr
```

`run_cli`·`fixture_srt` 는 `tests/test_cli_tier1.py` 의 기존 도우미를 쓴다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_tier1.py -k embed -v`
Expected: FAIL — `--embed-model` 을 모르는 옵션으로 거부한다

- [ ] **Step 3: 옵션과 탐지를 구현한다**

기존 `--stt-base-url`·`--stt-model` 이 하는 것과 같은 모양으로 옵션을 더한다.

```python
    embed_base_url: Annotated[
        str | None,
        typer.Option(
            "--embed-base-url",
            help="임베딩 엔드포인트. 없으면 CUESIFT_EMBED_BASE_URL, 그것도 없으면 --base-url",
        ),
    ] = None,
    embed_model: Annotated[
        str | None,
        typer.Option(
            "--embed-model",
            help="임베딩 모델 이름. 없으면 CUESIFT_EMBED_MODEL. --tier1에 필수입니다",
        ),
    ] = None,
```

**기본값을 두지 않는다.** `bge-m3` 는 개발자 로컬에 우연히 설치된 모델이지 규격이 아니며, 요구사항정의서 §11 R8 이 출처 없는 수치를 기본값으로 넣는 것을 금지한다.

`--tier1` 분기에 탐지를 더한다.

```python
        resolved_embed_model = embed_model or os.environ.get("CUESIFT_EMBED_MODEL")
        if not resolved_embed_model:
            # llm.backtranslation이 임베딩을 요구한다. 여기서 막지 않으면
            # 역번역을 후보 수만큼 부른 뒤 유사도 단계에서 전부 버린다.
            _echo("--tier1은 --embed-model을 요구한다 (FR-4.2)", err=True)
            raise typer.Exit(_EXIT_USAGE)
        embedder = OpenAICompatibleEmbedder(
            base_url=embed_base_url or os.environ.get("CUESIFT_EMBED_BASE_URL") or resolved_base,
            model=resolved_embed_model,
            api_key=api_key,
        )
        try:
            dimensions = embedder.probe()
        except EmbeddingUnsupportedError as exc:
            # 501 - 경로는 있고 모델이 못 한다. 모델을 바꾸면 해결된다.
            _echo(f"임베딩 모델 '{resolved_embed_model}'이 임베딩을 내지 못한다: {exc}", err=True)
            raise typer.Exit(_EXIT_USAGE) from exc
        except EmbeddingNotFoundError as exc:
            # 404 - 엔드포인트 자체가 없다. 백엔드를 바꿔야 한다.
            _echo(f"이 백엔드에는 임베딩 엔드포인트가 없다: {exc}", err=True)
            raise typer.Exit(_EXIT_USAGE) from exc
```

`dimensions` 는 진행 표시에 한 줄로 쓴다(예: `임베딩 준비됨 (bge-m3, 1024차원)`). **쓰지 않고 버리면 `probe()` 가 무엇을 확인했는지 사용자가 알 수 없다.**

`triage_with_tier1(...)` 호출에 `embedder=embedder` 를 더한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_tier1.py -v`
Expected: PASS

- [ ] **Step 5: `--help` 출력이 깨지지 않는지 본다**

rich 하이라이터가 색이 켜진 CI 에서만 옵션 이름을 쪼갠 전례가 있다. 폭이 아니라 색이 원인이다.

Run: `FORCE_COLOR=1 .venv/Scripts/python.exe -m pytest tests/test_cli.py -k help -v`
Expected: PASS

- [ ] **Step 6: 전체 스위트와 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/cli.py tests/test_cli_tier1.py
git commit -m "구현: --embed-* 옵션과 임베딩 가용성 탐지를 더한다 (FR-4.2)"
```

---

### Task 6: negation 정답지 잡음 분류기

**Files:**

- Create: `bench/classify_negation.py`
- Test: `tests/test_bench_classify_negation.py`

**Interfaces:**

- Consumes: 없다 (순수 문자열 판정)
- Produces: `classify(source_text: str, mutated_text: str, lang: str) -> str` — `"clean"` · `"broken_fixed_form"` · `"stranded_adverb"` · `"unnatural"` · `"npi_stranded"` · `"multi_negation"` 중 하나

**목적:** 이월 19번이 ja 자격 313건 중 129건(41.2%)을 결함으로 분류했다. 그 스크립트가 리포에 없어서 **정상 반전 부분집합에서의 Recall 을 낼 수단이 없다.** CI 게이트로는 쓰지 않고 벤치 보고 지표로만 쓴다.

- [ ] **Step 1: 실패 테스트를 쓴다**

이월 19번이 원문 대조로 잡은 오탐 9건과 미탐 4건을 픽스처로 고정한다. **이 13건이 규칙 품질의 유일한 검증 수단이다.**

```python
"""negation 정답지 잡음 분류 (FR-4.2 · 설계 §8.3).

**아래 픽스처는 이월 19번이 원문 대조로 잡은 실제 오탐·미탐이다.**
규칙을 다시 짜면 여기서 먼저 걸린다.
"""

from __future__ import annotations

import pytest

from bench.classify_negation import classify


@pytest.mark.parametrize(
    "mutated",
    [
        # 「しか」패턴이 접속사 「しかし」를 잡았다 - CJK에는 단어 경계가 없다.
        "しかし それは違います",
        # 문두 Yet(그러나)과 but yet(그렇지만)은 NPI가 아니라 접속사다.
        "Yet, we tried again",
        "but yet it worked",
        # 앞 절 부정이 살아 있으면 either는 여전히 호응 대상을 갖는다.
        "I don't know and she doesn't either",
        # 「礼にかなう」는 긍정형이 실제로 쓰인다 - 고정형이 아니다.
        "それは礼にかないます",
    ],
)
def test_오탐이었던_문장은_clean이다(mutated):
    assert classify("원문", mutated, lang="ja" if "し" in mutated else "en") == "clean"


@pytest.mark.parametrize(
    ("mutated", "lang", "expected"),
    [
        # 축약형은 `\bn't\b`로 못 잡는다 - don't의 n 앞이 단어 경계가 아니다.
        ("I do not know anymore", "en", "npi_stranded"),
        # 「しか」가 술어와 떨어진 경우.
        ("それしか方法があります", "ja", "stranded_adverb"),
        # 형용사 부정형.
        ("それは良くあります", "ja", "broken_fixed_form"),
        # 「〜ませんか」는 부정을 떼도 뜻이 같다 - 자격 자체가 아니다.
        ("再建しますか", "ja", "clean"),
    ],
)
def test_미탐이었던_문장을_잡는다(mutated, lang, expected):
    assert classify("원문", mutated, lang=lang) == expected


def test_어절_중간_줄바꿈에_뚫리지_않는다():
    # 자막은 화면 폭에 맞춰 어절 중간에서 줄바꿈된다. 판정은 개행을 지운
    # 사본에 해야 한다 - 이월 19번의 ja-02192가 정확히 이 경로였다.
    assert classify("원문", "現れるかもし\nれます", lang="ja") == classify(
        "원문", "現れるかもします", lang="ja"
    )
```

미탐 3건이 HANDOFF 에 "「しか」가 술어와 떨어진 경우 · 형용사 `くありません` · 「〜ませんか」"로 요약돼 있으므로, 구현 시 각 부류의 실제 문장을 `bench/results/ja-ko-2026-09-04.json` 의 라벨에서 찾아 픽스처를 실제 값으로 바꾼다. **HANDOFF 의 요약을 그대로 픽스처로 쓰면 파생 문서에서 읽은 사실이 된다.**

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_classify_negation.py -v`
Expected: FAIL — `ModuleNotFoundError: bench.classify_negation`

- [ ] **Step 3: 분류기를 구현한다**

핵심 규칙은 셋이다.

| 규칙 | 이유 |
| --- | --- |
| 판정은 `text.replace("\n", "")` 사본에 한다 | 어절 중간 줄바꿈이 문자열 매칭을 통과시킨다 |
| CJK 패턴은 `しか(?!し)` 처럼 **뒤따르는 글자를 배제**한다 | CJK 에는 단어 경계가 없어 `\b` 를 쓸 수 없고, 부분 문자열이 이웃 단어를 문다 |
| ASCII 패턴에만 `\b` 를 쓴다 | 제안된 `\b` 가 CJK 를 전부 깨뜨린 전례가 이 저장소에 있다 |

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_classify_negation.py -v`
Expected: PASS 10건

- [ ] **Step 5: `しか(?!し)` 의 배제를 지워 게이트를 실패시켜 본다**

패턴을 `しか` 로 잠시 바꾸고 돌린다. **치환 전에 `count(old) == 1` 을 확인한다** — 같은 패턴이 파일 앞쪽의 다른 함수에 먼저 걸려 엉뚱한 곳을 때린 전례가 있다.

Expected: `しかし それは違います` 케이스가 FAIL

**확인 후 반드시 되돌린다. 복원은 `finally` 에 두거나 손으로 즉시 한다.**

- [ ] **Step 6: 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add bench/classify_negation.py tests/test_bench_classify_negation.py
git commit -m "측정: negation 정답지 잡음 분류기를 더한다 (FR-4.2 · 이월 19)"
```

---

### Task 7: 벤치 Tier 1 통합과 원자료

**Files:**

- Modify: `bench/run.py` (`main()` 의 인자와 측정 흐름)
- Modify: `bench/report.py` (Tier 0 대 Tier 0+1 비교표)
- Test: `tests/test_bench_report.py`

**Interfaces:**

- Consumes: Task 5 의 CLI 패턴, Task 6 의 `classify`
- Produces: `bench/run.py --tier1 --embed-model MODEL` · `{audit-dir}/{pair}.backtranslation.json`

- [ ] **Step 1: 기본 동작이 안 바뀌는 것을 테스트로 못 박는다**

```python
def test_tier1_없이는_흐름이_같다():
    """`--tier1`이 꺼져 있으면 지금과 한 줄도 다르지 않다 (설계 D9).

    **켜져 있으면 CI가 LLM 백엔드를 요구하게 된다.** 벤치 테스트는
    data/가 .gitignore라 CI에서 이미 skip되는데, 기본값이 바뀌면
    로컬에서만 조용히 다른 것을 재게 된다.
    """
    parser_defaults = build_arg_parser().parse_args(["--pair", "en-ko"])
    assert parser_defaults.tier1 is False
    assert parser_defaults.embed_model is None
```

- [ ] **Step 2: 리포트 비교표의 실패 테스트를 쓴다**

```python
def test_tier1_비교표에_분모가_실린다():
    """부분집합 Recall은 분모 없이 쓰면 소수점이 신뢰받는다 (설계 §8.2).

    ja 표본의 정상 반전은 약 35건이라 해상도가 1/35 = 2.9%다.
    """
    rendered = render_tier1_comparison(
        tier0={"negation_recall": 0.1972, "clean_recall": 0.20, "clean_total": 35},
        tier1={"negation_recall": 0.4507, "clean_recall": 0.60, "clean_total": 35},
        budget=0.30,
    )
    assert "35" in rendered
    assert "2.9" in rendered or "해상도" in rendered
```

- [ ] **Step 3: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bench_report.py -k tier1 -v`
Expected: FAIL

- [ ] **Step 4: 벤치를 배선한다**

`bench/run.py` 에 인자를 더한다.

```python
    parser.add_argument("--tier1", action="store_true", help="Tier 1을 예산 10%·30%에서 측정한다")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--embed-base-url", default=None)
    parser.add_argument("--embed-model", default=None)
    parser.add_argument("--cache-dir", type=Path, default=None)
```

`TIER1_BUDGETS = (0.10, 0.30)` 를 모듈 상수로 둔다. **6개 예산 전부를 돌지 않는 이유를 주석에 적는다** — 예산 지점마다 후보 250건이 새로 고려져 고유 후보가 트랙당 1,500건으로 늘고, 로컬 Ollama 가 긴 입력에서 `ReadTimeout` 을 낸 전례가 있어 장시간 실행의 위험이 크다.

측정 흐름은 `measure(...)` 뒤에 붙인다.

```python
    if args.tier1:
        embedder = _build_embedder(args)
        dimensions = embedder.probe()   # 역번역 전에 멈춘다 (설계 D7)
        print(f"임베딩 준비됨 ({args.embed_model}, {dimensions}차원)")
        tier1_rows = []
        raw_records = []
        for budget in TIER1_BUDGETS:
            risks = triage_with_tier1(
                mutated, ctx,
                budget_ratio=budget,
                provider=provider,
                max_ratio=TIER1_MAX_RATIO,
                warn=print,
                embedder=embedder,
                cache_dir=args.cache_dir,
                identity=provider.cache_identity,
            )
            tier1_rows.append(_score_tier1(risks, labels, mutated, budget))
            raw_records.extend(_collect_raw(risks, mutated, labels, budget))
        _dump_raw(raw_records, args.audit_dir or track_path.parent, args.pair, commit=commit)
```

- [ ] **Step 5: 원자료 기록을 구현한다**

`{audit-dir}/{pair}.backtranslation.json` 에 세그먼트별로 남긴다. **이월 20번이 열린 이유가 정확히 이 형식의 부재다** — 스파이크 결과에 집계값만 있어서 라벨 4건이 교체됐을 때 213회를 통째로 다시 돌려야 했다.

| 필드 | 출처 |
| --- | --- |
| `segment_id` · `source_text` · `target_text` | `mutated` |
| `back_translation` · `cosine` · `score` | 신호의 `detail` |
| `label_kind` | `labels` |
| `negation_class` | Task 6 의 `classify` (negation 라벨에만) |
| `budget_ratio` | 어느 예산 지점의 후보였나 |

메타에 역번역 모델 · 임베딩 모델 · 커밋 · 실행 시각을 담는다. **`bench/results/` 가 아니라 audit-dir 인 이유는 자막 원문을 담아 CC BY-NC-ND 4.0 에 걸려 커밋할 수 없기 때문이다.**

- [ ] **Step 6: 통과를 확인하고 커밋한다**

```bash
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add bench/run.py bench/report.py tests/test_bench_report.py
git commit -m "측정: 벤치에 Tier 1 측정과 역번역 원자료를 더한다 (FR-4.2 · 이월 20)"
```

---

### Task 8: 실측 실행과 문서 갱신

**Files:**

- Create: `bench/results/{en-ko,ja-ko}-2026-09-05.{md,json}` (실행 산출물)
- Modify: `README.md` · `docs/요구사항정의서.md` · `docs/WBS.md` · `CHANGELOG.md` · `HANDOFF.md`

**Interfaces:**

- Consumes: Task 1~7 전부

- [ ] **Step 1: 두 트랙을 돌린다**

**긴 명령의 종료 코드를 볼 때 `; echo "exit=$?"` 를 붙이지 마라** — 파이썬이 죽어도 뒤의 `echo` 가 성공해 exit 0 으로 보고된다. 파일로 리다이렉트하고 `echo "EXIT=$?"` 를 **다음 줄에** 둔다.

```bash
.venv/Scripts/python.exe -m bench.run --pair en-ko --tier1 \
  --base-url http://localhost:11434/v1 --model qwen2.5:3b \
  --embed-model bge-m3 --cache-dir data/bench/cache > /path/to/scratch/en.log 2>&1
echo "EXIT=$?"
```

`ja-ko` 도 같은 형태로 돌린다. **리포트 파일명에 실행 날짜가 들어가므로 같은 날 두 번 돌리면 덮어쓴다.** 그리고 `--audit-dir` 기본값이 트랙과 같은 디렉터리라 `data/bench/*.injected.json` 과 `*.labels.json` 을 덮어쓰므로, 옛 정답지와 비교할 일이 있으면 먼저 복사해 둔다.

- [ ] **Step 2: 완료 판정 7개를 하나씩 확인한다**

| # | 조건 | 확인 방법 |
| --- | --- | --- |
| 1 | `--tier1` 없는 실행이 2026-09-04 리포트와 같은 수치를 낸다 | 두 JSON 의 Tier 0 지표를 대조한다 |
| 2 | 임베딩 모델 없이 `--tier1` 이면 역번역 전에 멈춘다 | `--embed-model` 을 빼고 돌려 exit 2 확인 |
| 3 | 예산 10% · 30% 의 `negation` Recall 이 Tier 0 대비로 실린다 | 리포트 표 |
| 4 | 정상 반전 부분집합 Recall 이 **분모와 함께** 실린다 | 리포트 표 |
| 5 | 원자료에 세그먼트별 역번역문과 코사인이 있다 | JSON 을 열어 확인 |
| 6 | 캐시 격리 회귀 테스트가 버그 버전에서 실패했다 | Task 3 Step 7 에서 확인함 |
| 7 | CI 5잡이 전부 통과한다 | PR 에서 |

- [ ] **Step 3: 문서를 갱신한다**

| 문서 | 무엇을 |
| --- | --- |
| `README.md` | 최상단 배수와 실측 표. Tier 1 을 켠 수치를 **별도 행**으로 둔다 |
| `docs/요구사항정의서.md` | §5.8 의 FR-4.2 상태를 ⬜ → ✅ 로. §12 Q4 유보 ③ 에 실측을 더한다 |
| `docs/WBS.md` | v0.1 완료 개수를 41 → 42 로. **요구사항정의서의 파생물이므로 FR 서술을 여기서만 바꾸지 않는다** |
| `CHANGELOG.md` | `[Unreleased]` 의 Added 절 |
| `HANDOFF.md` | 이월 20번을 닫는다. 못 잡는 20% 를 승계 항목에 남긴다 |

**Recall 목표의 상한이 80% 언저리라는 것을 리포트가 서술해야 한다.** 복원된 건에서는 신호가 0이므로 점수 스케일로 해결되지 않는다.

- [ ] **Step 4: 문서 게이트를 돌린다**

`git add` 를 **먼저** 한다. 추적되기 전의 새 문서는 링크 검사를 아예 받지 않는다.

```bash
git add -A
python scripts/check_links.py
npx --yes markdownlint-cli2
```

**두 도구의 파일 개수가 같은지 본다.** 실측으로 45 대 47 로 갈린 전례가 있다.

- [ ] **Step 5: 전체 게이트를 돌리고 커밋한다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
```

수집 개수를 읽는다. **로컬 `passed` 와 CI `passed` 는 1건 다르다**(`data/` 가 `.gitignore` 라 벤치 테스트 1건이 CI 에서 skip 된다). **수집 개수는 양쪽이 같아야 한다.**

```bash
git add -A
git commit -m "측정: FR-4.2 역번역 신호를 벤치에 태우고 문서를 갱신한다"
```

- [ ] **Step 6: PR 을 연다 (푸시는 사용자 승인 후)**

```bash
git push -u origin feat/fr-4-2-backtranslation
gh pr create --base main
gh pr checks --watch
```

PR 본문에는 **무엇을 · 근거 문서 · 게이트 수치**를 담는다. 게이트 수치는 개수를 그대로 적는다.

---

## Self-Review

**스펙 커버리지**

| 스펙 절 | 태스크 |
| --- | --- |
| §2 D1 (embed/ 분리) | 1 · 2 |
| §2 D2 (용어집 미전달) | 3 |
| §2 D3 · D4 (온도 0.0) | 3 |
| §2 D5 (정규화 없음) | 3 |
| §2 D6 (embedder 없으면 예외) | 3 · 4 |
| §2 D7 (사전 탐지) | 5 |
| §2 D8 (예산 2지점) | 7 |
| §2 D9 (기본 꺼짐) | 7 |
| §2 D10 (원자료 위치) | 7 |
| §4.1 프로토콜 · §4.2 예외 | 1 · 2 |
| §5 신호 정의 | 3 |
| §6 캐시 격리 | 3 (Step 6~7) |
| §7 탐지와 CLI 옵션 | 5 |
| §8.1~8.2 벤치 흐름과 리포트 | 7 |
| §8.3 분류기 | 6 |
| §8.4 원자료 | 7 |
| §9 테스트 전략 | 각 태스크에 분산 |
| §10 완료 판정 | 8 Step 2 |

**타입 일관성**

| 이름 | 정의 | 사용 |
| --- | --- | --- |
| `cosine(a, b) -> float` | Task 1 | Task 3 |
| `Embedder.embed(texts) -> list[list[float]]` | Task 1 | Task 2 · 3 |
| `EmbeddingUnsupportedError` / `EmbeddingNotFoundError` | Task 1 | Task 2 · 5 |
| `OpenAICompatibleEmbedder.probe() -> int` | Task 2 | Task 5 · 7 |
| `Tier1Context.embedder` | Task 3 | Task 4 |
| `triage_with_tier1(..., embedder=)` | Task 4 | Task 7 |
| `classify(source, mutated, lang) -> str` | Task 6 | Task 7 |

**남은 확인 사항** — 구현자가 파일을 열어 맞춰야 하는 것들이다. 계획이 추측으로 채우면 틀린 값이 코드로 굳는다.

| 항목 | 어디서 확인하나 |
| --- | --- |
| `EchoProvider` 의 마지막 메시지 노출 방식 | `tests/fakes/provider.py` |
| `build_messages` 의 실제 시그니처 | `src/cuesift/translate/prompt.py` |
| `tests/test_tier1.py` 의 기존 도우미 이름 | 그 파일 |
| 미탐 3건의 실제 문장 | `bench/results/ja-ko-2026-09-04.json` 의 라벨 |
| `signals/__init__.py` 의 등록 import 방식 | 그 파일 |
