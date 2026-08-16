"""OpenAI 호환 엔드포인트 어댑터 (FR-2.5, Q3).

로컬 LLM(Ollama, vLLM, LM Studio)과 상용 API가 모두 `/v1/chat/completions`를
제공하므로 이것으로 일원화한다(요구사항정의서 §12 Q3). **단 호환은 전송
규약에 한정되고 능력은 균일하지 않다** - 같은 항이 적어 둔 대로 Ollama는
`logprobs`·`n`을 지원하지 않고 vLLM은 `logprobs`를 지원한다. 그래서 §12 Q3은
미지원 신호를 비활성화하고 **그 사실을 리포트에 명시**하라고 요구한다.
이 모듈은 `logprobs`도 `n`도 보내지 않으므로 **명시할 열화가 없다.** 능력
탐지를 여기 넣지 않은 것은 그 때문이지, 필요 없다고 판단해서가 아니다.

`response_format`(JSON 모드)도 쓰지 않는다. 지원 여부가 서버마다 달라 조용히
무시되거나 400을 내므로, 프롬프트로 JSON을 요구하고 파싱 실패를 정상
경로(개별 폴백)로 다루는 쪽이 이식성이 높다 (설계 §4.3).

이 모듈이 번역 계층의 **유일한 I/O**다. 그래서 여기서 하는 일은 사실상
**실패를 두 갈래로 가르는 것 하나뿐이다** (FR-2.6, 설계 §4.2):

- 재시도 가능을 치명으로 분류하면 일시적 429·503 하나에 실행 전체가 죽는다.
- 치명을 재시도 가능으로 분류하면 API 키 오타 하나가 배치마다 재시도를
  낭비하고, 사용자는 원인이 키라는 것을 모른 채 실패 리포트만 받는다.

**`ProviderError` 밖의 예외는 어떤 경로로도 나가면 안 된다.** engine의 폴백은
`except RetryableProviderError` / `except FatalProviderError` 둘뿐이라
`TypeError`·`ValueError`·`AttributeError`가 새면 폴백을 통째로 우회해
파이프라인이 트레이스백으로 죽는다. 아래 방어가 전부 그 이유로 있다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import httpx

from cuesift.translate.provider import (
    ChatMessage,
    Completion,
    FatalProviderError,
    RetryableProviderError,
    TokenUsage,
)

# 짧으면 긴 배치가 정상인데 끊기고, 길면 죽은 서버에 오래 매달린다.
DEFAULT_TIMEOUT_S = 60.0

# 오류의 시작. 399로 낮추면 리다이렉트 응답이 Fatal이 되고, 401로 올리면
# 인증 실패가 오류로 잡히지 않고 본문 파싱 실패로 위장된다.
_ERROR_MIN_STATUS = 400

# 이 위는 전부 서버 사정이라 잠시 뒤 성공할 수 있다. 499로 낮추면 게이트웨이가
# 쓰는 499(클라이언트가 먼저 끊음)까지 무의미하게 재시도하고, 501로 올리면
# 500(내부 오류)이 Fatal이 되어 일시적 장애 하나가 실행을 죽인다.
_SERVER_ERROR_MIN_STATUS = 500

# 4xx 중 유일한 재시도 대상. 429가 빠지면 일시적 rate limit 하나가 실행
# 전체를 중단시키고, 반대로 401을 넣으면 키 오타가 배치마다 재시도돼
# 실패 호출이 배수로 는다 (설계 §4.2).
_RETRYABLE_STATUS = frozenset({408, 429})

# 이 둘이 아니면 요청 시점에 ProviderError 밖의 예외가 난다. `_require_http_url` 참고.
_ALLOWED_SCHEMES = ("http", "https")

# 오류 메시지에 실을 응답 본문의 최대 길이. 없으면 HTML 오류 페이지 전문이
# 로그와 실패 리포트에 그대로 실린다.
_ERROR_BODY_CHARS = 200


class OpenAICompatibleProvider:
    """OpenAI 호환 `/chat/completions`를 친다. `Provider` 프로토콜의 구현이다."""

    name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        _require_http_url(self._base_url)
        # 끝의 슬래시를 정리하지 않으면 `//chat/completions`가 되고, 경로를
        # 정확히 매칭하는 게이트웨이(nginx·LiteLLM)가 404를 낸다. 404는
        # Fatal이라 실행 전체가 죽는데 원인은 슬래시 하나다.
        self._endpoint = f"{self._base_url}/chat/completions"
        self._model = model
        self._api_key = api_key
        # client 주입은 테스트가 MockTransport를 꽂는 통로다. 주입받은 것은
        # 우리 것이 아니므로 close()가 건드리지 않는다.
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> Completion:
        """한 번만 친다. **재시도하지 않는다.**

        재시도는 engine의 `_call_with_retry`가 한다. 양쪽이 다 하면 총 호출이
        곱해지고, 백오프 대기가 이중으로 쌓인다.

        시그니처는 `Provider` 프로토콜과 **인자 이름·순서·기본값·주석 표기까지**
        같아야 한다. `max_tokens`에 기본값을 붙이는 것도 이탈이다 - 프로토콜에는
        없다. engine이 키워드로 부르므로 어긋나도 런타임에는 조용히 통과하고,
        그것을 잡는 것은 테스트의 `inspect.signature` 단언뿐이다.
        """
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            # `max_tokens: null`에 400을 내는 서버가 있어 None은 아예 뺀다.
            # 조건을 `if max_tokens:`로 쓰면 0이 함께 사라져 무제한 생성이 되고,
            # 그 결과는 None을 보낸 것과 구분되지 않는다.
            payload["max_tokens"] = max_tokens

        headers: dict[str, str] = {}
        if self._api_key:
            # 로컬 LLM은 키를 요구하지 않는다. `is not None`으로 검사하면 빈
            # 문자열에서 `Bearer `가 나가고 서버가 401을 내는데, 401은 Fatal이라
            # "키가 없다"가 "키가 틀렸다"로 둔갑한다.
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            response = self._client.post(self._endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            # TimeoutException은 TransportError의 자손이라 아래 절보다 **먼저**
            # 와야 한다. 순서가 뒤집히면 이 절이 죽은 코드가 되고, 분류는
            # 그대로라 메시지를 보지 않는 테스트는 아무것도 눈치채지 못한다.
            raise RetryableProviderError(f"타임아웃: {e}") from None
        except httpx.TransportError as e:
            # ConnectError·ReadError·RemoteProtocolError·ProxyError가 여기 온다.
            # 좁게 ConnectError만 잡으면 나머지가 ProviderError 밖으로 새어
            # engine의 폴백을 우회한다.
            raise RetryableProviderError(f"연결 실패: {e}") from None

        _raise_for_status(response)
        return _to_completion(response)

    def close(self) -> None:
        """직접 만든 클라이언트만 닫는다.

        주입받은 것을 닫으면 그 클라이언트를 공유하는 다른 호출부가 다음
        요청에서 죽는다. 소유하지 않은 자원은 정리하지 않는다.
        """
        if self._owns_client:
            self._client.close()


def _require_http_url(base_url: str) -> None:
    """호출이 아니라 **생성** 시점에 막는다.

    실측(2026-08-16, httpx 0.28.1)한 세 갈래가 전부 나쁘다.

    - 스킴이 없으면(`localhost:11434/v1`) 요청 시점에 `ValueError`가 나는데
      `ProviderError` 밖이라 engine의 폴백이 받지 못한다.
    - `ftp://`는 `httpx.UnsupportedProtocol`이고 이것은 `TransportError`의
      자손이라 **재시도 대상으로 분류된다** - 설정 오류 하나가 배치마다
      재시도를 낭비한다.
    - 포트가 잘못됐거나 제어 문자가 섞이면 `httpx.InvalidURL`인데 이것은
      `ValueError`도 `ProviderError`도 아니다.

    설정 오류는 세그먼트 하나를 건드리기 전에 한 번 드러나는 편이 맞다.
    `ChatMessage.__post_init__`이 잘못된 role을 같은 이유로 거부한다.
    """
    try:
        scheme = httpx.URL(base_url).scheme
    except httpx.InvalidURL as e:
        raise ValueError(f"base_url을 URL로 읽을 수 없다: {base_url!r} ({e})") from None
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"base_url은 http:// 또는 https://로 시작해야 한다: {base_url!r}")


def _raise_for_status(response: httpx.Response) -> None:
    """상태 코드를 재시도 가능성으로 가른다 (설계 §4.2).

    분류가 양쪽으로 위험하다는 것이 이 함수의 전부다. 모듈 독스트링 참고.
    """
    status = response.status_code
    if status < _ERROR_MIN_STATUS:
        return
    if status in _RETRYABLE_STATUS or status >= _SERVER_ERROR_MIN_STATUS:
        raise RetryableProviderError(
            f"{status}: {response.text[:_ERROR_BODY_CHARS]}",
            retry_after_s=_parse_retry_after(response.headers.get("Retry-After")),
        )
    # 나머지 4xx는 다시 걸어도 같다 - 401 인증, 400 스키마, 404 모델 없음.
    # 상태 코드를 메시지 앞에 두는 이유: 이것이 없으면 사용자는 401(키)과
    # 404(모델 이름)를 구분하지 못한 채 "치명적 오류"만 본다.
    raise FatalProviderError(f"{status}: {response.text[:_ERROR_BODY_CHARS]}")


def _parse_retry_after(raw: str | None) -> float | None:
    """초 단위 Retry-After만 읽는다. 나머지는 전부 `None`("모른다")이다.

    HTTP-date 형식도 규격상 유효하지만 파싱하지 않는다. 파싱 실패로 예외를
    내면 **재시도 가능한 상황이 치명적 오류로 승격된다** - 대기 시간을 몰라도
    지수 백오프로 물러설 수 있으므로 모르는 편이 안전하다.

    `float()`가 `-5`·`inf`·`nan`을 **통과시킨다**는 것이 함정이다. 그대로
    실려 나가면 호출부의 `time.sleep()`이 `ValueError`/`OverflowError`를 내는데,
    그것은 `except RetryableProviderError` 핸들러 **본문 안에서** 터지므로 그
    핸들러가 잡지 못한다. `RetryableProviderError.__init__`이 같은 값을
    정규화하지만 여기서도 막는다 - 한쪽에만 기대는 것은 우연한 정합이라
    저쪽 정규화가 바뀌면 이쪽이 조용히 무방비가 된다.
    """
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0.0:
        return None
    return seconds


def _to_completion(response: httpx.Response) -> Completion:
    try:
        body = response.json()
    except ValueError as e:
        # 200에 HTML 오류 페이지를 싣는 프록시가 있다. JSONDecodeError는
        # ValueError의 하위라 여기서 잡힌다.
        raise FatalProviderError(f"응답이 JSON이 아니다: {e}") from None

    # **순서가 계약이다.** `_extract_text`가 통과했다는 것은 `body["choices"]`가
    # 동작했다는 뜻이고, `json`이 만들 수 있는 타입 중 문자열 첨자를 받는 것은
    # 사전뿐이다. 그래서 `_extract_usage`는 body가 사전임을 전제해도 된다.
    # 한 줄로 합치면(`Completion(text=..., usage=...)`) 이 순서가 파이썬의 인자
    # 평가 순서라는 우연에 걸리므로 두 줄로 둔다.
    text = _extract_text(body)
    return Completion(text=text, usage=_extract_usage(body))


def _extract_text(body: object) -> str:
    """`choices[0].message.content`를 문자열로 꺼낸다.

    **`content: null`과 "content 키가 없음"을 다르게 다룬다.** OpenAI 규격에서
    `content`는 nullable이라 null을 보낸 서버는 규격을 지킨 것이고, 실패한 것은
    계약이 아니라 이번 생성이다(도구 호출만 있는 응답·내용 필터 등). 같은
    요청을 다시 보내면 달라질 여지가 있으므로 **재시도 대상**이다. 반대로 키
    자체가 없거나 값이 문자열이 아니면 이 서버가 OpenAI 호환이 아니라는
    뜻이라 재시도해도 같은 형태가 온다.

    분류를 뒤집으면 양쪽 다 나쁘다. null을 Fatal로 두면 빈 생성 하나가 실행
    전체를 중단시켜 "세그먼트 하나의 실패가 파일 전체를 죽이지 않는다"는
    FR-2.6의 취지와 정반대가 되고, 구조 오류를 Retryable로 두면 호환되지 않는
    서버에 무의미한 재시도만 는다.

    **그냥 흘려보내는 것이 최악이다.** `Completion.text`의 타입은 `str`이고,
    `None`이 실려 나가면 `batch.parse_translations`가 `raw.strip()`에서
    `AttributeError`를 내는데 그것은 `ProviderError` 밖이라 폴백이 받지 못한다.
    """
    try:
        message = body["choices"][0]["message"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as e:
        # 최상위가 사전이 아니거나(TypeError), choices가 없거나(KeyError),
        # 비어 있으면(IndexError) 전부 여기로 온다.
        raise FatalProviderError(f"OpenAI 호환 응답이 아니다(choices): {e}") from None

    if not isinstance(message, dict) or "content" not in message:
        seen = repr(message)[:_ERROR_BODY_CHARS]
        raise FatalProviderError(f"응답에 message.content가 없다: {seen}")

    content = message["content"]
    if content is None:
        raise RetryableProviderError("모델이 빈 응답(content=null)을 냈다")
    if not isinstance(content, str):
        raise FatalProviderError(f"content가 문자열이 아니다: {repr(content)[:_ERROR_BODY_CHARS]}")
    return content


def _extract_usage(body: dict) -> TokenUsage:
    """usage를 읽되 **못 읽어도 죽지 않는다** (NFR-2).

    비용 리포트가 부정확한 것과 파이프라인이 트레이스백으로 죽는 것은 다른
    문제다. 서버는 이 자리에 무엇이든 보낼 수 있고, `int(usage.get(k, 0))`을
    그대로 부르면 `null`은 `TypeError`, 문자열 usage는 `AttributeError`,
    `"abc"`는 `ValueError`, 음수는 `TokenUsage`의 `ValueError`를 낸다.
    전부 `ProviderError` 밖이라 engine의 폴백이 받지 못한다.

    **`calls=1`만은 어떤 경우에도 센다.** 여기서 0이 되면 NFR-2 비용 리포트가
    "호출 0회"를 조용히 보고하고, 그때 사용자는 청구서를 보고서야 안다.

    `body`가 사전이라는 것은 `_to_completion`의 호출 순서가 보장한다. 여기서
    `isinstance(body, dict)`를 한 번 더 검사해 봤지만 **어떤 변이로도 죽지 않는
    죽은 가지**였다 - 도달 조건이 없는 방어는 계약이 아니라 장식이다.
    """
    raw = body.get("usage")
    return TokenUsage(
        prompt_tokens=_token_count(raw, "prompt_tokens"),
        completion_tokens=_token_count(raw, "completion_tokens"),
        calls=1,
    )


def _token_count(raw: object, key: str) -> int:
    """토큰 수 하나를 0 이상의 int로 떨어뜨린다. 못 읽으면 0이다.

    숫자 문자열(`"12"`)도 받지 않는다. 관대해질수록 "무엇을 받아들이는가"의
    경계가 흐려지는데, 여기서 얻는 것은 비용 리포트의 정확도뿐이라 값이
    맞지 않는다.
    """
    if not isinstance(raw, dict):
        return 0
    value = raw.get(key)
    # bool은 int의 하위라 먼저 걸러야 True가 1로 새어 들어온다.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    if not math.isfinite(value):
        # int(inf)는 OverflowError, int(nan)은 ValueError다. 파이썬의 json은
        # 규격에 없는 `Infinity`·`NaN` 리터럴을 기본으로 받아들이므로 도달한다.
        return 0
    # 음수를 그대로 넘기면 TokenUsage가 ValueError를 내는데, 그것이 바로
    # 이 함수가 막으려는 예외다.
    return max(0, int(value))
