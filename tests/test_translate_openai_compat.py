"""OpenAI 호환 어댑터 (FR-2.5).

`httpx.MockTransport`를 쓴다 - httpx가 이미 런타임 의존성이라 의존성 추가
없이 HTTP 계층을 전부 검증할 수 있다 (설계 §9.1). 실제 네트워크는 한 번도
치지 않는다 (NFR-7).

**이 파일이 지키는 것은 "예외가 두 갈래로 갈리는가" 하나다.** 어댑터가
`ProviderError` 밖의 예외를 흘리면 engine의 폴백(`except
RetryableProviderError` / `except FatalProviderError`)이 받지 못해
파이프라인이 트레이스백으로 죽는다. 그래서 서버가 보낼 수 있는 쓰레기
입력마다 "무엇으로 갈리는가"를 하나씩 못박는다.
"""

from __future__ import annotations

import inspect
import json
import math
from collections.abc import Callable

import httpx
import pytest

from cuesift.translate.openai_compat import (
    DEFAULT_TIMEOUT_S,
    OpenAICompatibleProvider,
    _parse_retry_after,
)
from cuesift.translate.provider import (
    ChatMessage,
    FatalProviderError,
    Provider,
    ProviderError,
    RetryableProviderError,
)

_MESSAGES = [ChatMessage(role="user", content="안녕")]

_Handler = Callable[[httpx.Request], httpx.Response]


def _provider(handler: _Handler, **kwargs: object) -> OpenAICompatibleProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(
        base_url="http://x/v1",
        model="m",
        api_key="k",
        client=client,
        **kwargs,  # type: ignore[arg-type]
    )


def _ok_body(text: str = "hello") -> dict:
    """실제 OpenAI 호환 서버의 성공 응답 형태다.

    임의로 단순화하지 않는다 - 가짜가 실제보다 관대하면 파싱 코드의 결함이
    테스트를 통과한다. `finish_reason`·`index`·`role`은 이 모듈이 읽지
    않지만, 읽지 않는다는 사실 자체가 응답에 그것들이 **있을 때** 검증돼야
    한다.
    """
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1_755_300_000,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    }


def _recorder() -> tuple[list[httpx.Request], _Handler]:
    """요청을 모으는 핸들러. 성공 응답을 돌려준다."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_ok_body())

    return seen, handler


def _payload(request: httpx.Request) -> dict:
    return json.loads(request.content)


def _call(provider: OpenAICompatibleProvider, **kwargs: object) -> object:
    merged: dict = {"temperature": 0.0, "max_tokens": None}
    merged.update(kwargs)
    return provider.complete(_MESSAGES, **merged)


# --------------------------------------------------------------------------
# 프로토콜 준수 (요구 A)
# --------------------------------------------------------------------------


def test_Provider_시그니처를_글자_그대로_지킨다() -> None:
    """`Provider`는 `@runtime_checkable`이 아니고 CI에 타입 검사기도 없다.

    그래서 `*`를 빠뜨렸거나 인자 이름이 어긋난 구현도 engine의 키워드
    호출에서는 정상 동작해 전부 통과한다 - 가장 흔한 이탈이 가장 안 잡힌다.
    이 단언이 이 저장소에서 프로토콜 준수를 강제하는 유일한 수단이다
    (`tests/test_translate_engine.py`의 가짜 프로바이더 단언과 같은 장치다).

    **이것은 타입 검사가 아니라 주석 텍스트 비교다.** 두 모듈 모두
    `from __future__ import annotations`를 쓰므로 `inspect.signature`가 보는
    주석은 전부 문자열이다(실측 2026-08-16, CPython 3.14.6). 다만 그 문자열은
    소스 원문이 아니라 **컴파일러가 AST를 unparse한 정규형**이라, 공백만
    다른 `int|None`은 `int | None`과 같은 값이 되어 여기서 죽지 않는다.
    반대로 뜻이 같은 `Optional[int]`는 텍스트가 달라 **죽는다** - 즉 이
    단언은 타입이 아니라 표기를 고정한다. 실측한 변이 결과가 그렇다.

    좁게 보면 과하지만, 기본값(`max_tokens = None`)과 키워드 전용
    표시(`*`)와 인자 이름의 이탈을 잡는 수단이 이것뿐이라 그대로 둔다.

    `name`도 함께 본다. 프로토콜의 멤버는 `name`과 `complete` **둘**인데
    `complete`만 검사하면 `name` 줄을 통째로 지워도 죽는 테스트가 없다.
    """
    assert inspect.signature(OpenAICompatibleProvider.complete) == inspect.signature(
        Provider.complete
    )
    assert isinstance(getattr(OpenAICompatibleProvider, "name", None), str)


def test_name을_노출한다() -> None:
    provider = _provider(lambda _r: httpx.Response(200, json=_ok_body()))
    assert provider.name == "openai-compatible"


# --------------------------------------------------------------------------
# 요청 조립
# --------------------------------------------------------------------------


def test_정상_응답을_Completion으로_바꾼다() -> None:
    provider = _provider(lambda _r: httpx.Response(200, json=_ok_body()))
    completion = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert completion.text == "hello"
    assert completion.usage.prompt_tokens == 7
    assert completion.usage.completion_tokens == 3
    assert completion.usage.calls == 1


def test_요청_본문에_모델과_메시지가_들어간다() -> None:
    seen, handler = _recorder()
    _provider(handler).complete(_MESSAGES, temperature=0.3, max_tokens=100)
    body = _payload(seen[0])
    assert body["model"] == "m"
    assert body["messages"] == [{"role": "user", "content": "안녕"}]
    assert body["temperature"] == 0.3
    assert body["max_tokens"] == 100


def test_메시지_순서와_역할이_그대로_실린다() -> None:
    # 위치가 뒤집히면 시스템 지시가 모델의 마지막 입력이 되어 번역 대상이
    # 통째로 무시된다. 한 개짜리 목록으로만 검사하면 순서 변이가 살아남는다.
    seen, handler = _recorder()
    messages = [
        ChatMessage(role="system", content="너는 번역가다"),
        ChatMessage(role="user", content="첫째"),
        ChatMessage(role="assistant", content="ok"),
        ChatMessage(role="user", content="둘째"),
    ]
    _provider(handler).complete(messages, temperature=0.0, max_tokens=None)
    assert _payload(seen[0])["messages"] == [
        {"role": "system", "content": "너는 번역가다"},
        {"role": "user", "content": "첫째"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "둘째"},
    ]


def test_max_tokens가_None이면_보내지_않는다() -> None:
    # 일부 서버가 max_tokens: null에 400을 낸다.
    seen, handler = _recorder()
    _provider(handler).complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert "max_tokens" not in _payload(seen[0])


def test_max_tokens가_0이면_보낸다() -> None:
    # 경계값이다. 조건을 `if max_tokens:`로 쓰면 0이 조용히 사라져 서버가
    # 무제한 생성을 하고, 그 차이는 None과 구분되지 않는다.
    seen, handler = _recorder()
    _provider(handler).complete(_MESSAGES, temperature=0.0, max_tokens=0)
    assert _payload(seen[0])["max_tokens"] == 0


def test_temperature_0도_보낸다() -> None:
    # 경계값이다. `if temperature:`로 거르면 0.0이 빠지고 서버 기본값(보통
    # 1.0)이 쓰여 번역이 매 실행 달라진다 - 결정론이 조용히 깨진다.
    seen, handler = _recorder()
    _provider(handler).complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert _payload(seen[0])["temperature"] == 0.0


def test_이식성을_깨는_필드를_보내지_않는다() -> None:
    # response_format은 서버마다 지원이 달라 조용히 무시되거나 400을 낸다
    # (설계 §4.3). logprobs와 n은 백엔드에 따라 조용히 사라진다
    # (요구사항정의서 §12 Q3의 "이어지는 제약").
    seen, handler = _recorder()
    _provider(handler).complete(_MESSAGES, temperature=0.0, max_tokens=None)
    body = _payload(seen[0])
    assert "response_format" not in body
    assert "logprobs" not in body
    assert "n" not in body
    assert "stream" not in body


def test_POST로_JSON을_보낸다() -> None:
    seen, handler = _recorder()
    _provider(handler).complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert seen[0].method == "POST"
    assert seen[0].headers["content-type"] == "application/json"


def test_chat_completions_경로를_친다() -> None:
    seen, handler = _recorder()
    _provider(handler).complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert str(seen[0].url) == "http://x/v1/chat/completions"


def test_base_url_끝의_슬래시를_정리한다() -> None:
    # 정리하지 않으면 `//chat/completions`가 되고, 경로를 정확히 매칭하는
    # 게이트웨이(nginx·LiteLLM)가 404를 낸다. 404는 Fatal이라 실행 전체가
    # 죽는데 원인은 슬래시 하나다.
    seen, handler = _recorder()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    OpenAICompatibleProvider(base_url="http://x/v1///", model="m", client=client).complete(
        _MESSAGES, temperature=0.0, max_tokens=None
    )
    assert str(seen[0].url) == "http://x/v1/chat/completions"


def test_api_key가_있으면_Authorization을_붙인다() -> None:
    seen, handler = _recorder()
    _provider(handler).complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert seen[0].headers.get("authorization") == "Bearer k"


def test_api_key가_없으면_Authorization을_안_붙인다() -> None:
    # 로컬 LLM은 키를 요구하지 않는다. 빈 Bearer를 보내면 거부하는 서버가 있다.
    seen, handler = _recorder()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    OpenAICompatibleProvider(base_url="http://x/v1", model="m", client=client).complete(
        _MESSAGES, temperature=0.0, max_tokens=None
    )
    assert seen[0].headers.get("authorization") is None


def test_api_key가_빈_문자열이어도_Authorization을_안_붙인다() -> None:
    # 환경변수 미설정이 빈 문자열로 들어오는 경로가 흔하다. `is not None`으로
    # 검사하면 `Bearer `가 나가고 서버가 401을 내는데, 401은 Fatal이라
    # "키가 없다"가 아니라 "키가 틀렸다"로 보고된다.
    seen, handler = _recorder()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    OpenAICompatibleProvider(base_url="http://x/v1", model="m", api_key="", client=client).complete(
        _MESSAGES, temperature=0.0, max_tokens=None
    )
    assert seen[0].headers.get("authorization") is None


# --------------------------------------------------------------------------
# 상태 코드 분류 (요구 D)
# --------------------------------------------------------------------------

# 경계를 1씩 민 값을 전부 넣는다. 408/429만 재시도 대상이므로 407·409·430이
# Fatal로 남아야 집합의 크기가 고정되고, 499/500이 갈려야 `>= 500` 경계가
# 고정된다.
_FATAL_STATUSES = [400, 401, 403, 404, 407, 409, 422, 430, 499]
_RETRYABLE_STATUSES = [408, 429, 500, 502, 503, 504, 599]


@pytest.mark.parametrize("status", _FATAL_STATUSES)
def test_되돌릴_수_없는_상태는_Fatal이다(status: int) -> None:
    provider = _provider(lambda _r: httpx.Response(status, json={"error": "no"}))
    with pytest.raises(FatalProviderError) as exc:
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    # 상태 코드가 메시지에 없으면 사용자는 401(키)과 404(모델 이름)를
    # 구분하지 못한 채 "치명적 오류"만 본다.
    assert str(status) in str(exc.value)


@pytest.mark.parametrize("status", _RETRYABLE_STATUSES)
def test_일시적_상태는_Retryable이다(status: int) -> None:
    provider = _provider(lambda _r: httpx.Response(status, json={"error": "busy"}))
    with pytest.raises(RetryableProviderError) as exc:
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert str(status) in str(exc.value)


def test_Fatal과_Retryable은_서로를_잡지_않는다() -> None:
    """분류 두 갈래를 통째로 맞바꾸는 변이를 잡는 자리다.

    `pytest.raises(FatalProviderError)`는 형제 예외를 잡지 않지만, 둘 다
    `ProviderError`의 자손이라 상속 구조가 흔들리면 위의 두 파라미터
    테이블이 서로를 통과시킬 수 있다. 여기서 양방향을 못박는다.
    """
    fatal = _provider(lambda _r: httpx.Response(401, json={}))
    retryable = _provider(lambda _r: httpx.Response(429, json={}))
    with pytest.raises(ProviderError) as f:
        fatal.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    with pytest.raises(ProviderError) as r:
        retryable.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert not isinstance(f.value, RetryableProviderError)
    assert not isinstance(r.value, FatalProviderError)


def test_399는_오류가_아니다() -> None:
    # 오류 경계는 400이다. 399를 오류로 보면 리다이렉트 응답이 Fatal이 되고,
    # 400을 정상으로 보면 스키마 오류가 본문 파싱 실패로 위장된다.
    provider = _provider(lambda _r: httpx.Response(399, json=_ok_body()))
    assert provider.complete(_MESSAGES, temperature=0.0, max_tokens=None).text == "hello"


# --------------------------------------------------------------------------
# Retry-After
# --------------------------------------------------------------------------


def test_Retry_After_헤더를_읽는다() -> None:
    provider = _provider(
        lambda _r: httpx.Response(429, headers={"Retry-After": "12"}, json={"error": "slow"})
    )
    with pytest.raises(RetryableProviderError) as exc:
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert exc.value.retry_after_s == 12.0


def test_Retry_After는_5xx에서도_읽는다() -> None:
    # 429에서만 읽으면 503 + Retry-After를 보내는 게이트웨이의 지시를 어겨
    # 일시적 제한이 영구 차단으로 승격될 수 있다.
    provider = _provider(lambda _r: httpx.Response(503, headers={"Retry-After": "30"}, json={}))
    with pytest.raises(RetryableProviderError) as exc:
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert exc.value.retry_after_s == 30.0


def test_Retry_After_헤더_이름은_대소문자를_가리지_않는다() -> None:
    # HTTP 헤더 이름은 대소문자 무관이고 실제 서버는 소문자로 보낸다.
    # httpx의 대소문자 무시 조회 대신 원시 딕셔너리를 뒤지도록 바꾸면
    # 여기서 죽는다.
    provider = _provider(lambda _r: httpx.Response(429, headers={"retry-after": "5"}, json={}))
    with pytest.raises(RetryableProviderError) as exc:
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert exc.value.retry_after_s == 5.0


def test_Retry_After가_0이면_0이다() -> None:
    # 경계값이다. `float(raw) or None` 같은 형태로 쓰면 0이 None으로 접히고,
    # "지금 바로 다시 걸어도 된다"가 "모른다"로 바뀐다.
    provider = _provider(lambda _r: httpx.Response(429, headers={"Retry-After": "0"}, json={}))
    with pytest.raises(RetryableProviderError) as exc:
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert exc.value.retry_after_s == 0.0


def test_Retry_After가_없으면_None이다() -> None:
    provider = _provider(lambda _r: httpx.Response(429, json={}))
    with pytest.raises(RetryableProviderError) as exc:
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert exc.value.retry_after_s is None


@pytest.mark.parametrize(
    "raw",
    [
        "Wed, 21 Oct 2026 07:28:00 GMT",  # HTTP-date: 규격상 유효하지만 안 읽는다
        "",  # 헤더는 있는데 값이 빈 경우
        "   ",
        "soon",
        "12s",
        "-5",  # 음수는 도메인 밖이다
        "inf",  # float()가 통과시킨다. 그대로 자면 OverflowError다
        "nan",
    ],
)
def test_읽을_수_없는_Retry_After는_None이다(raw: str) -> None:
    """파싱 실패로 예외를 내면 재시도 가능한 상황이 치명적 오류로 승격된다.

    `-5`·`inf`·`nan`은 `float()`를 **통과한다**. 그대로 실려 나가면 호출부의
    `time.sleep()`이 `ValueError`/`OverflowError`를 내는데 그것은
    `ProviderError` 밖이라 engine의 `except RetryableProviderError` 핸들러
    본문 안에서 터져 폴백을 통째로 우회한다. `RetryableProviderError`가
    같은 값을 정규화하지만 여기서도 막는 것은, 정규화에 기대는 것이
    우연한 정합이기 때문이다.
    """
    provider = _provider(lambda _r: httpx.Response(429, headers={"Retry-After": raw}, json={}))
    with pytest.raises(RetryableProviderError) as exc:
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert exc.value.retry_after_s is None


def test_소수점_Retry_After도_읽는다() -> None:
    provider = _provider(lambda _r: httpx.Response(429, headers={"Retry-After": "1.5"}, json={}))
    with pytest.raises(RetryableProviderError) as exc:
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert exc.value.retry_after_s == 1.5


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("12", 12.0),
        ("1.5", 1.5),
        ("0", 0.0),
        ("-0", 0.0),
        ("-5", None),
        ("-0.1", None),
        ("inf", None),
        ("-inf", None),
        ("nan", None),
        ("1e400", None),  # float()가 inf로 만든다
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
        ("", None),
        ("12s", None),
    ],
)
def test_parse_retry_after가_스스로_도메인_밖을_거른다(
    raw: str | None, expected: float | None
) -> None:
    """위의 종단 테스트만으로는 이 함수의 가드가 검증되지 않는다.

    `RetryableProviderError.__init__`이 도메인 밖 값을 `None`으로 정규화하므로,
    여기서 `isfinite`와 음수 검사를 통째로 지워도 종단 결과가 같다. 즉 그
    가드는 종단 테스트로는 **관찰되지 않는다.** 그래서 함수를 직접 부른다.
    """
    assert _parse_retry_after(raw) == expected


# --------------------------------------------------------------------------
# 전송 계층 실패
# --------------------------------------------------------------------------


def _raising(error: Exception) -> _Handler:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise error

    return handler


def test_타임아웃은_Retryable이다() -> None:
    # 메시지까지 보는 이유: TimeoutException은 TransportError의 자손이라
    # 두 except 절의 순서가 뒤집히면 타임아웃 분기가 죽은 코드가 된다.
    # 분류는 그대로라 메시지를 안 보면 순서 변이가 살아남는다.
    with pytest.raises(RetryableProviderError) as exc:
        _call(_provider(_raising(httpx.ReadTimeout("too slow"))))
    assert "타임아웃" in str(exc.value)


def test_연결_실패는_Retryable이다() -> None:
    with pytest.raises(RetryableProviderError) as exc:
        _call(_provider(_raising(httpx.ConnectError("refused"))))
    assert "연결 실패" in str(exc.value)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectTimeout("connect"),
        httpx.PoolTimeout("pool"),
        httpx.ReadError("read"),
        httpx.RemoteProtocolError("half-closed"),
        httpx.ProxyError("proxy"),
    ],
)
def test_전송_계층_실패는_전부_Retryable이다(error: Exception) -> None:
    # TransportError의 자손을 좁게 나열하면(ConnectError만 등) 나머지가
    # ProviderError 밖으로 새어 engine의 폴백을 우회한다.
    with pytest.raises(RetryableProviderError):
        _call(_provider(_raising(error)))


# --------------------------------------------------------------------------
# 본문 파싱 - 전부 ProviderError 안으로 떨어져야 한다
# --------------------------------------------------------------------------


def test_JSON이_아니면_Fatal이다() -> None:
    # HTML 오류 페이지를 200으로 돌려주는 프록시가 있다.
    provider = _provider(lambda _r: httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(FatalProviderError):
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)


def test_스키마가_다르면_Fatal이다() -> None:
    # choices가 없다는 것은 이 서버가 OpenAI 호환이 아니라는 뜻이다.
    # 재시도해도 같으므로 Retryable로 두면 무의미한 재시도만 는다.
    provider = _provider(lambda _r: httpx.Response(200, json={"output": "hello"}))
    with pytest.raises(FatalProviderError, match="choices"):
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)


@pytest.mark.parametrize(
    "body",
    [
        {"output": "hello"},  # choices 키 자체가 없다
        {"choices": []},  # 빈 목록 - IndexError
        {"choices": "abc"},  # 목록이 아니다 - 첨자가 문자열을 낸다
        {"choices": {"0": {}}},  # 사전 - KeyError
        {"choices": [{}]},  # message 키가 없다
        {"choices": [{"message": "hi"}]},  # message가 사전이 아니다(문자열)
        {"choices": [{"message": 42}]},  # message가 `in`을 받지 않는다
        {"choices": [{"message": ["content"]}]},  # `in`은 받지만 사전이 아니다
        {"choices": [None]},  # 원소가 None
        [1, 2, 3],  # 최상위가 목록
        "hello",  # 최상위가 문자열
        42,  # 최상위가 숫자
        None,  # 최상위가 null
    ],
)
def test_응답_구조가_어긋나면_Fatal이다(body: object) -> None:
    """전부 `FatalProviderError`여야 한다.

    하나라도 `TypeError`·`KeyError`·`IndexError`로 새면 engine의 폴백이
    받지 못해 파이프라인이 트레이스백으로 죽는다. `pytest.raises`는 다른
    예외를 통과시키지 않으므로 이 표가 곧 "새지 않는다"의 증명이다.
    """
    provider = _provider(lambda _r: httpx.Response(200, json=body))
    with pytest.raises(FatalProviderError):
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)


def test_content가_null이면_Retryable이다() -> None:
    """요구 B. `message.content`는 OpenAI 규격에서 **nullable**이다.

    즉 null을 보낸 서버는 규격을 지킨 것이고, 실패한 것은 계약이 아니라
    이번 생성이다. 같은 요청을 다시 보내면 달라질 여지가 있으므로
    `Retryable`로 분류한다. `Fatal`로 두면 빈 생성 하나가 실행 전체를
    중단시키는데, 그것은 "세그먼트 하나의 실패는 파일 전체를 죽이지
    않는다"는 FR-2.6의 취지와 정반대다.

    무엇보다 **그냥 흘려보내면 안 된다.** `Completion.text`의 타입은 `str`
    이고, `None`이 실려 나가면 `batch.parse_translations`가 `raw.strip()`
    에서 `AttributeError`를 내는데 그것은 `ProviderError` 밖이라 폴백이
    받지 못한다.
    """
    body = {"choices": [{"message": {"role": "assistant", "content": None}}]}
    provider = _provider(lambda _r: httpx.Response(200, json=body))
    with pytest.raises(RetryableProviderError):
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)


def test_content_키가_없으면_Fatal이다() -> None:
    # null과 구분한다. 규격은 content를 **항상 싣되 null일 수 있다**고
    # 정하므로, 키 자체가 없는 것은 서버가 호환이 아니라는 신호다.
    # 재시도해도 같은 형태가 온다.
    body = {"choices": [{"message": {"role": "assistant"}}]}
    provider = _provider(lambda _r: httpx.Response(200, json=body))
    with pytest.raises(FatalProviderError):
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)


@pytest.mark.parametrize("content", [42, 3.5, True, ["a"], {"text": "a"}])
def test_content가_문자열도_null도_아니면_Fatal이다(content: object) -> None:
    # 형태 자체가 규격 밖이다. 재시도해도 같은 형태가 온다.
    body = {"choices": [{"message": {"content": content}}]}
    provider = _provider(lambda _r: httpx.Response(200, json=body))
    with pytest.raises(FatalProviderError):
        provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)


def test_content가_빈_문자열이면_성공이다() -> None:
    # 빈 문자열은 정상 응답이다. 여기서 예외를 내면 파싱 실패를 정상
    # 경로(개별 폴백)로 다룬다는 설계 §4.3이 무너진다 - 파싱은 batch가
    # 하고 어댑터는 전달만 한다.
    body = {"choices": [{"message": {"content": ""}}]}
    provider = _provider(lambda _r: httpx.Response(200, json=body))
    assert provider.complete(_MESSAGES, temperature=0.0, max_tokens=None).text == ""


def test_choices가_여럿이면_첫_번째를_쓴다() -> None:
    body = {
        "choices": [
            {"index": 0, "message": {"content": "첫째"}},
            {"index": 1, "message": {"content": "둘째"}},
        ]
    }
    provider = _provider(lambda _r: httpx.Response(200, json=body))
    assert provider.complete(_MESSAGES, temperature=0.0, max_tokens=None).text == "첫째"


# --------------------------------------------------------------------------
# usage 파싱 (요구 C) - 못 읽어도 죽지 않고, calls는 반드시 센다
# --------------------------------------------------------------------------


def _usage_for(usage: object, *, omit: bool = False) -> tuple[int, int, int]:
    body: dict = {"choices": [{"message": {"content": "hi"}}]}
    if not omit:
        body["usage"] = usage
    provider = _provider(lambda _r: httpx.Response(200, json=body))
    completion = provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    u = completion.usage
    return u.prompt_tokens, u.completion_tokens, u.calls


def test_usage가_없어도_동작한다() -> None:
    # 일부 서버가 usage를 생략한다. 비용 보고가 0이 될 뿐 번역은 성립한다.
    assert _usage_for(None, omit=True) == (0, 0, 1)


@pytest.mark.parametrize(
    "usage",
    [
        None,  # "usage": null
        "unknown",  # 문자열 - .get이 AttributeError를 냈다
        [],  # 목록
        42,
        {},  # 빈 사전
    ],
)
def test_usage가_쓰레기여도_calls는_센다(usage: object) -> None:
    """요구 C. 비용 리포트가 부정확한 것과 파이프라인이 죽는 것은 다르다.

    `usage.get(...)`을 무방비로 부르면 문자열·목록에서 `AttributeError`가
    나고 그것은 `ProviderError` 밖이라 engine의 폴백이 받지 못한다.
    **`calls=1`은 어떤 경우에도 세야 한다** - 여기서 0이 되면 NFR-2 비용
    리포트가 "호출 0회"를 조용히 보고하고, 그때 사용자는 청구서를 보고서야
    안다.
    """
    assert _usage_for(usage) == (0, 0, 1)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"prompt_tokens": 7, "completion_tokens": 3}, (7, 3)),
        ({"prompt_tokens": None, "completion_tokens": 3}, (0, 3)),  # int(None)은 TypeError
        ({"prompt_tokens": -5, "completion_tokens": 3}, (0, 3)),  # TokenUsage가 ValueError
        ({"prompt_tokens": 7}, (7, 0)),  # 한쪽만 있다
        ({"completion_tokens": 3}, (0, 3)),
        ({"prompt_tokens": "12"}, (0, 0)),  # 숫자 문자열도 받지 않는다
        ({"prompt_tokens": "abc"}, (0, 0)),  # int("abc")는 ValueError
        ({"prompt_tokens": 7.9}, (7, 0)),  # 버림
        ({"prompt_tokens": True}, (0, 0)),  # bool은 int의 하위지만 토큰 수가 아니다
        ({"prompt_tokens": [1]}, (0, 0)),
        ({"prompt_tokens": {"a": 1}}, (0, 0)),
        ({"prompt_tokens": 0, "completion_tokens": 0}, (0, 0)),  # 경계
    ],
)
def test_usage_필드는_0_이상의_정수로_떨어진다(raw: dict, expected: tuple[int, int]) -> None:
    assert _usage_for(raw) == (*expected, 1)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_usage가_유한하지_않으면_0이다(value: float) -> None:
    """`int(inf)`는 `OverflowError`, `int(nan)`은 `ValueError`다.

    둘 다 `ProviderError` 밖이라 engine의 폴백이 받지 못한다. JSON 규격에는
    없는 값이지만 파이썬의 `json`은 `Infinity`·`NaN` 리터럴을 기본으로
    받아들이므로 실제로 도달한다.
    """
    body = {"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": value}}
    provider = _provider(lambda _r: httpx.Response(200, content=json.dumps(body).encode()))
    assert provider.complete(_MESSAGES, temperature=0.0, max_tokens=None).usage.prompt_tokens == 0


# --------------------------------------------------------------------------
# 생성자
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    [
        "localhost:11434/v1",  # 스킴 없음 - 요청 시점에 맨 ValueError
        "/v1",
        "",
        "x",
        "ftp://x/v1",  # UnsupportedProtocol = TransportError의 자손
        "http://x:port/v1",  # httpx.InvalidURL
        "http://x\n/v1",  # httpx.InvalidURL
    ],
)
def test_HTTP가_아닌_base_url은_생성_시점에_거부한다(base_url: str) -> None:
    """잘못된 base_url은 호출 시점에 **`ProviderError` 밖의 예외**를 낸다.

    실측(2026-08-16, httpx 0.28.1):

    - `client.post("localhost:11434/v1/...")` -> `ValueError("unknown url
      type: ...")`. `ProviderError` 밖이라 engine의 폴백이 받지 못한다.
    - `ftp://x/v1` -> `httpx.UnsupportedProtocol`. `TransportError`의
      자손이라 어댑터가 **재시도 대상으로** 분류한다 - 설정 오류 하나가
      배치마다 재시도를 낭비한다.
    - `httpx.URL("http://x:port/v1")` -> `httpx.InvalidURL`. `ValueError`도
      `ProviderError`도 아니다.

    그래서 호출이 아니라 생성에서 막는다. 설정 오류는 세그먼트 하나를
    건드리기 전에 한 번 드러나는 편이 맞다. `ChatMessage.__post_init__`이
    잘못된 role을 같은 이유로 `ValueError`로 거부한다.
    """
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatibleProvider(base_url=base_url, model="m")


@pytest.mark.parametrize("base_url", ["http://x/v1", "https://x/v1", "HTTPS://X/v1"])
def test_HTTP_계열_base_url은_받는다(base_url: str) -> None:
    provider = OpenAICompatibleProvider(base_url=base_url, model="m")
    try:
        assert provider.name == "openai-compatible"
    finally:
        provider.close()


def test_기본_타임아웃은_60초다() -> None:
    # 짧으면 긴 배치가 정상인데 끊기고, 길면 죽은 서버에 오래 매달린다.
    assert DEFAULT_TIMEOUT_S == 60.0
    provider = OpenAICompatibleProvider(base_url="http://x/v1", model="m")
    try:
        assert provider._client.timeout == httpx.Timeout(60.0)
    finally:
        provider.close()


def test_timeout을_클라이언트에_전달한다() -> None:
    # 받아만 두고 쓰지 않아도 다른 테스트는 전부 통과한다 - 주입한
    # 클라이언트에는 이 값이 닿지 않기 때문이다. 그래서 여기서만 실제
    # 클라이언트를 만들어 확인한다(소켓은 열리지 않는다).
    provider = OpenAICompatibleProvider(base_url="http://x/v1", model="m", timeout=1.5)
    try:
        assert provider._client.timeout == httpx.Timeout(1.5)
    finally:
        provider.close()


def test_주입한_클라이언트를_그대로_쓴다() -> None:
    seen, handler = _recorder()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(base_url="http://x/v1", model="m", client=client)
    provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert len(seen) == 1
    assert provider._client is client


def test_주입한_클라이언트는_닫지_않는다() -> None:
    # 호출부가 만든 것을 어댑터가 닫으면 같은 클라이언트를 공유하는 다른
    # 프로바이더가 죽는다. 소유하지 않은 자원은 정리하지 않는다.
    seen, handler = _recorder()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(base_url="http://x/v1", model="m", client=client)
    provider.close()
    assert not client.is_closed
    provider.complete(_MESSAGES, temperature=0.0, max_tokens=None)
    assert len(seen) == 1


def test_직접_만든_클라이언트는_닫는다() -> None:
    provider = OpenAICompatibleProvider(base_url="http://x/v1", model="m")
    client = provider._client
    provider.close()
    assert client.is_closed


def test_호출은_한_번만_나간다() -> None:
    # 어댑터는 재시도하지 않는다. 재시도는 engine의 `_call_with_retry`가
    # 하고, 양쪽이 다 하면 총 호출이 곱해진다.
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503, json={})

    with pytest.raises(RetryableProviderError):
        _call(_provider(handler))
    assert len(seen) == 1
