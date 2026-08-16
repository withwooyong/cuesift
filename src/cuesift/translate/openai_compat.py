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

# 리다이렉트의 시작. 이 위 400 미만을 따로 가르지 않으면 3xx가 본문 파싱까지
# 흘러가 "응답이 JSON이 아니다"라는 **엉뚱한 원인**으로 보고된다 - 실제 원인은
# 게이트웨이의 http->https 리다이렉트 오설정이고 고칠 곳은 base_url이다.
_REDIRECT_MIN_STATUS = 300

# 오류의 시작. 399로 낮추면 위 리다이렉트 절이 죽은 코드가 되고, 401로 올리면
# 인증 실패가 오류로 잡히지 않고 본문 파싱 실패로 위장된다.
_ERROR_MIN_STATUS = 400

# 이 위를 전부 재시도한다. **501·505는 사실 영구 실패다**(Not Implemented,
# HTTP Version Not Supported). 그래도 재시도 쪽에 두는 것은 의도적인
# fail-open이다 - 비용이 `max_retries+1`회로 한정된 뒤 세그먼트 실패로 우아하게
# 강등되는 반면, Fatal로 오분류하면 일시적 5xx 하나가 실행 전체를 죽인다.
# 비대칭이 커서 경계를 세분하지 않는다. 499로 낮추면 게이트웨이가 쓰는
# 499(클라이언트가 먼저 끊음)까지 무의미하게 재시도한다.
_SERVER_ERROR_MIN_STATUS = 500

# 4xx 중 재시도하는 **둘**. 429가 빠지면 일시적 rate limit 하나가 실행
# 전체를 중단시키고, 408(Request Timeout)이 빠지면 게이트웨이가 스스로
# 끊은 요청이 Fatal로 승격돼 같은 일이 벌어진다. 반대로 401을 넣으면 키
# 오타가 배치마다 재시도돼 실패 호출이 배수로 는다 (설계 §4.2).
_RETRYABLE_STATUS = frozenset({408, 429})

# 이 둘이 아니면 요청 시점에 ProviderError 밖의 예외가 난다. `_require_http_url` 참고.
_ALLOWED_SCHEMES = ("http", "https")

# 오류 메시지에 실을 응답 본문의 최대 길이. 없으면 HTML 오류 페이지 전문이
# 로그와 실패 리포트에 그대로 실린다.
_ERROR_BODY_CHARS = 200

# 토큰 수의 상한. float64가 정수를 정확히 담는 마지막 값(2**53)이다.
#
# **이 상한이 없으면 "크래시 없음"이 "안전"으로 오독된다.** `_token_count`는
# int에 `math.isfinite`를 걸지 않는데(int는 정의상 유한하다) 그 판단은 그
# 함수 안에서만 옳다. 값이 클램프 없이 `TokenUsage`에 실려 나가고,
# **NFR-2의 비용 = 토큰 x 단가는 float 연산이다** - 실측: 310자리 정수에
# 단가를 곱하면 `OverflowError: int too large to convert to float`가 나고,
# 그것은 `ArithmeticError`라 **`ProviderError` 밖이다.** 이 모듈이 전체를
# 걸어 막고 있는 바로 그 부류의 누수이고, 터지는 자리만 engine이 아니라
# WP7b의 비용 리포트다.
#
# 더 크게 잡으면 `float(x)`가 정수 정밀도를 잃어 비용 추정이 **조용히**
# 틀린다. 더 작게 잡으면 정상 실행이 잘린다 - 실사용은 1e7 토큰 규모라
# 이 값은 약 1e9배의 여유가 있다.
_MAX_TOKEN_COUNT = 2**53


class OpenAICompatibleProvider:
    """OpenAI 호환 `/chat/completions`를 친다. `Provider` 프로토콜의 구현이다."""

    name = "openai-compatible"

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
            # 함께 주면 timeout이 **조용히 무시된다** - 주입한 클라이언트가 이미
            # 자기 timeout을 갖고 있다. 호출부는 설정했다고 믿는데 값은 다른
            # 것이 쓰인다. 둘 중 하나만 받으면 그 거짓 믿음이 사라진다.
            raise ValueError("client를 주면 timeout은 그 클라이언트의 것이다. 함께 줄 수 없다")
        self._base_url = base_url.rstrip("/")
        _require_http_url(self._base_url)
        _require_ascii_api_key(api_key)
        # 끝의 슬래시를 정리하지 않으면 `//chat/completions`가 되고, 경로를
        # 정확히 매칭하는 게이트웨이(nginx·LiteLLM)가 404를 낸다. 404는
        # Fatal이라 실행 전체가 죽는데 원인은 슬래시 하나다.
        self._endpoint = f"{self._base_url}/chat/completions"
        self._model = model
        self._api_key = api_key
        # client 주입은 테스트가 MockTransport를 꽂는 통로다. 주입받은 것은
        # 우리 것이 아니므로 close()가 건드리지 않는다.
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=DEFAULT_TIMEOUT_S if timeout is None else timeout
        )

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
        if not isinstance(temperature, int | float) or not math.isfinite(temperature):
            # httpx의 `encode_json`이 `allow_nan=False`를 쓰므로 nan·inf는
            # `ValueError`가 되고, 수가 아니면 `math.isfinite`가 `TypeError`를
            # 낸다. 둘 다 `ProviderError` 밖이라 engine의 폴백이 받지 못한다.
            # 도달 경로가 실재한다 - temperature는 설정에서 오고 **YAML 1.1은
            # `.nan`·`.inf`를 float으로 파싱한다.** 설정 오류라 재시도는 무의미하다.
            raise FatalProviderError(f"temperature가 유한한 수가 아니다: {temperature!r}")

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
        except (httpx.HTTPError, httpx.InvalidURL, httpx.CookieConflict, httpx.StreamError) as e:
            # **`TransportError`로 좁히면 `DecodingError`가 샌다.** 실측(httpx
            # 0.28.1): `issubclass(httpx.DecodingError, httpx.TransportError)`는
            # `False`이고 `RequestError`의 자손일 뿐이다. `TooManyRedirects`도
            # 같다. 둘 다 위 두 절을 지나쳐 `ProviderError` 밖으로 나갔다.
            #
            # 발생 지점이 `post()` **안**(`Response.read()`의 압축 해제)이라
            # 상태 코드 분류에는 닿지도 않는다. httpx가 기본으로
            # `Accept-Encoding: gzip, deflate`를 보내므로 이중 압축 프록시나
            # 끊긴 gzip 본문에서 실제로 도달한다.
            #
            # 재시도 쪽에 두는 것은 fail-open이다. 깨진 압축이 재시도로 풀릴
            # 보장은 없지만 비용이 `max_retries+1`회로 한정되는 반면, Fatal로
            # 두면 프록시의 일시적 오작동이 실행 전체를 죽인다.
            #
            # **네 이름 중 실제로 일하는 것은 `HTTPError` 하나다.** 실측: 나머지
            # 셋을 지우고 `except httpx.HTTPError`로 좁혀도 죽는 테스트가 0개다
            # (등가 변이). `InvalidURL`은 `__init__`이 먼저 막고, `CookieConflict`와
            # `StreamError`는 이 모듈이 쿠키도 스트리밍도 쓰지 않아 도달 경로가
            # 없다. 그럼에도 남기는 것은 도달 조건이 **미래의 코드 변경에**
            # 달렸기 때문이다 - `stream=True`를 쓰기 시작하면 그날로 도달한다.
            # 관찰되지 않는다는 사실을 여기 적어 두는 편이, 관찰되는 척하는
            # 것보다 낫다.
            raise RetryableProviderError(f"응답 처리 실패: {e}") from None

        _raise_for_status(response)
        return _to_completion(response)

    def close(self) -> None:
        """직접 만든 클라이언트만 닫는다.

        주입받은 것을 닫으면 그 클라이언트를 공유하는 다른 호출부가 다음
        요청에서 죽는다. 소유하지 않은 자원은 정리하지 않는다.

        **닫은 뒤 재사용하지 마라.** `close()` 다음의 `complete()`는 httpx가
        내는 맨 `RuntimeError("Cannot send a request, as the client has been
        closed.")`가 되고, 그것은 `ProviderError` 밖이라 engine의 폴백이 받지
        못한다. 여기서 막지 않는 것은 되살릴 방법이 없어서다 - 막아도 할 수
        있는 일은 다른 예외로 바꾸는 것뿐이고, 호출 순서가 틀린 것은 설정이
        아니라 코드의 결함이라 조용히 감싸는 편이 더 나쁘다.
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

    **스킴만 보면 이 독스트링이 막겠다고 적은 실패가 그대로 재현된다.**
    `"http://"`는 호출부의 `rstrip("/")`을 지나면 `"http:"`가 되는데
    `httpx.URL("http:").scheme`은 여전히 `"http"`라 검사를 통과했고, 실제
    클라이언트로 호출하면 `UnsupportedProtocol`이 나와 **재시도 대상으로
    분류됐다.** `http://${{OLLAMA_HOST}}/v1`에서 변수가 비면 나오는 형태다.
    `rstrip` 순서를 바꾸는 것으로는 고쳐지지 않는다 - 호스트를 봐야 한다.
    """
    try:
        url = httpx.URL(base_url)
    except httpx.InvalidURL as e:
        raise ValueError(f"base_url을 URL로 읽을 수 없다: {base_url!r} ({e})") from None
    if url.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"base_url은 http:// 또는 https://로 시작해야 한다: {base_url!r}")
    if not url.host:
        raise ValueError(f"base_url에 호스트가 없다: {base_url!r}")
    if url.query or url.fragment:
        # 붙여 쓰면 `http://x/v1?key=1/chat/completions`가 되어 실제 경로가
        # `/v1`로 간다(실측). 404 -> Fatal이라 사용자는 "모델이 없다"로 읽는다.
        # `rstrip("/")`을 넣은 이유와 똑같은 실패 모드다.
        raise ValueError(f"base_url에 쿼리·프래그먼트를 붙일 수 없다: {base_url!r}")


def _require_ascii_api_key(api_key: str | None) -> None:
    """HTTP 헤더는 ASCII다. 아니면 요청 시점에 `UnicodeEncodeError`가 샌다.

    키를 복사하다 전각 문자나 스마트 따옴표가 섞이는 것은 `base_url` 오타와
    **같은 부류의 설정 오류**인데 한쪽만 가드가 있었다.

    **키 값을 메시지에 싣지 않는다.** 비밀이라 로그와 실패 리포트에 남으면
    안 된다. 위치만 알려도 사용자는 고칠 수 있다.
    """
    if api_key is None:
        return
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError as e:
        raise ValueError(
            f"api_key에 ASCII가 아닌 문자가 있다(위치 {e.start}). 값은 표시하지 않는다"
        ) from None


def _raise_for_status(response: httpx.Response) -> None:
    """상태 코드를 재시도 가능성으로 가른다 (설계 §4.2).

    분류가 양쪽으로 위험하다는 것이 이 함수의 전부다. 모듈 독스트링 참고.
    """
    status = response.status_code
    if status < _REDIRECT_MIN_STATUS:
        return
    if status < _ERROR_MIN_STATUS:
        # 리다이렉트는 따라가지 않는다 - OpenAI 호환 엔드포인트가 그것을
        # 요구하면 설정 오류다. 여기서 가르지 않으면 본문 파싱까지 흘러가
        # "응답이 JSON이 아니다"라는 엉뚱한 원인으로 보고된다(실측 301).
        location = response.headers.get("Location", "")
        raise FatalProviderError(f"{status} 리다이렉트({location}). base_url을 확인하라")
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
    #
    # 두 줄로 두는 것은 **가독성 때문이지 정확성 때문이 아니다.** 한 줄로
    # 합쳐도(`Completion(text=..., usage=...)`) 왼쪽에서 오른쪽 평가는 언어
    # 규격의 보장이라 동작이 같다(등가 변이, 0건 사망). 위험한 것은 순서를
    # 실제로 뒤집는 쪽이고 그것은 변이에서 3개를 죽인다.
    text = _extract_text(body)
    return Completion(text=text, usage=_extract_usage(body))


def _extract_text(body: object) -> str:
    """`choices[0].message.content`를 문자열로 꺼낸다.

    **분류의 축은 "표기"가 아니라 "구조"다.**

    `content: null`과 `content` 키 부재를 **같게** 다룬다. 규격상 `content`는
    nullable이지만, 둘의 차이는 원인이 아니라 **직렬화 정책**인 경우가 흔하다 -
    Pydantic·FastAPI 기반 OpenAI 호환 서버가 `exclude_none=True`로 덤프하면
    `"content": null`이 키째로 사라진다. 표기로 가르면 같은 "이번 생성이
    비었다"가 서버 A에서는 Retryable(비용이 `max_retries+1`회로 유한)이고 서버
    B에서는 **Fatal(실행 전체 사망)** 이 된다. 표기 차이로 실행의 생사가
    갈리면 안 되므로 판별 신호로 쓸 수 없다.

    그래서 둘 다 **재시도 대상**이다. 실패한 것은 계약이 아니라 이번
    생성이고(도구 호출만 있는 응답·내용 필터 등) 다시 보내면 달라질 여지가
    있다. Fatal로 두면 빈 생성 하나가 실행 전체를 중단시켜 "세그먼트 하나의
    실패가 파일 전체를 죽이지 않는다"는 FR-2.6의 취지와 정반대가 된다.

    비호환 판정은 **구조**에만 맡긴다 - `choices` 부재, `message`가 객체가
    아님, `content`가 문자열도 null도 아님. 이 셋은 재시도해도 같은 형태가
    온다. 반대로 구조 오류를 Retryable로 두면 무의미한 재시도만 는다.

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

    if not isinstance(message, dict):
        # 구조 오류다. 아래 "content가 비었다"와 한 조건에 묶여 있었는데
        # 성격이 다르다 - 이쪽은 서버가 호환이 아니라는 뜻이다.
        raise FatalProviderError(f"message가 객체가 아니다: {repr(message)[:_ERROR_BODY_CHARS]}")

    content = message.get("content")
    if content is None:
        # 키 부재와 null을 여기서 합류시킨다. 위 독스트링 참고.
        raise RetryableProviderError("모델이 빈 응답(content가 없거나 null)을 냈다")
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
    if isinstance(value, float) and not math.isfinite(value):
        # int(inf)는 OverflowError, int(nan)은 ValueError다. 파이썬의 json은
        # 규격에 없는 `Infinity`·`NaN` 리터럴을 기본으로 받아들이므로 도달한다.
        #
        # **`isinstance(value, float)` 없이 부르면 큰 int에서 죽는다.**
        # `math.isfinite`가 인자를 float로 변환하므로 `math.isfinite(10**309)`가
        # `OverflowError`(= `ArithmeticError`, `ProviderError` 밖)를 낸다.
        # int는 정의상 항상 유한하므로 검사할 것이 없다.
        #
        # 경계가 비대칭이라는 것도 적어 둔다. 4300자리 이상은 오히려 안전하다 -
        # 파이썬 3.11+의 int 문자열 변환 한도에 `json`이 먼저 걸려 `ValueError`를
        # 내고 `_to_completion`이 그것을 Fatal로 받는다. **310~4299자리만 샜다.**
        return 0
    # 음수를 그대로 넘기면 TokenUsage가 ValueError를 내는데, 그것이 바로
    # 이 함수가 막으려는 예외다. 상한은 `_MAX_TOKEN_COUNT` 주석 참고.
    return max(0, min(int(value), _MAX_TOKEN_COUNT))
