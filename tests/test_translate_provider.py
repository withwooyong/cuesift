"""프로바이더 계약과 예외 계층 (요구사항정의서 FR-2.5, FR-2.6).

파일 뒤쪽 절반은 `CountingProvider` 검증이다 (FR-7.4 · 설계 D6·D7).
**"몇 번 불렀나"가 아니라 "몇 토큰을 썼나"를 센다.** 캐시 히트는 실제로
토큰을 쓰지 않으므로 잡히지 않는 것이 정확한 동작이다 - `cost`는 청구서에
가까운 물건이다(D7).
"""

from __future__ import annotations

import math
from typing import get_args

import pytest

from cuesift.translate.provider import (
    ChatMessage,
    Completion,
    CountingProvider,
    FatalProviderError,
    ProviderError,
    RetryableProviderError,
    Role,
    TokenUsage,
)


@pytest.mark.parametrize("role", get_args(Role))
def test_chat_message_허용된_역할을_전부_받는다(role: str) -> None:
    # 허용목록을 하나씩 확인하지 않고 대표값 하나만 통과시키면, _ROLES가
    # ("system",)으로 줄어도 모든 테스트가 통과한다. get_args(Role)에서 값을
    # 끌어오므로 Literal과 _ROLES가 갈라지는 것도 여기서 잡힌다.
    assert ChatMessage(role=role, content="너는 번역가다").role == role


def test_chat_message_잘못된_역할은_거부한다() -> None:
    # Literal은 타입 힌트일 뿐 런타임에 아무것도 막지 않는다. 잘못된 역할은
    # 서버가 400을 내는데, 그 400은 FatalProviderError로 분류되어 전체를
    # 중단시킨다. 조립 시점에 막지 않으면 원인이 프롬프트 조립 코드라는
    # 사실이 호출 실패 지점에서 보이지 않는다.
    with pytest.raises(ValueError, match="role"):
        ChatMessage(role="tool", content="x")  # type: ignore[arg-type]


@pytest.mark.parametrize("content", [123, None, ["a"], {"text": "a"}, 1.5, True])
def test_chat_message_문자열이_아닌_내용을_거부한다(content: object) -> None:
    """`role`을 막는 것과 **같은 실패 모드**다.

    막지 않으면 그 값이 요청 본문에 그대로 실린다 - 실측:
    `{"role": "user", "content": 123}`. 서버는 400을 내고 그 400은
    `FatalProviderError`가 되어 실행 전체를 중단시키는데, 그때 **원인이
    프롬프트 조립 코드라는 사실은 어디에도 보이지 않는다.**

    `role` 가드가 같은 클래스에서 이미 이 근거를 적어 두고 막는다.
    한쪽 필드만 막는 것이 비대칭이었다.

    `True`가 목록에 있는 것은 `bool`이 `int`의 하위라서다 - 타입 검사를
    `isinstance(content, str)`이 아닌 형태로 쓰면 새어 들어올 수 있다.
    """
    with pytest.raises(ValueError, match="content"):
        ChatMessage(role="user", content=content)  # type: ignore[arg-type]


def test_token_usage_합산() -> None:
    a = TokenUsage(prompt_tokens=10, completion_tokens=5, calls=1)
    b = TokenUsage(prompt_tokens=3, completion_tokens=2, calls=1)
    total = a + b
    assert (total.prompt_tokens, total.completion_tokens, total.calls) == (13, 7, 2)


def test_token_usage_기본값은_전부_0이다() -> None:
    # 배치 루프가 빈 TokenUsage부터 누적하므로 기본값이 없으면 호출부가
    # 매번 0을 세 개 적어야 한다.
    assert TokenUsage() + TokenUsage(calls=1) == TokenUsage(calls=1)


@pytest.mark.parametrize("field", ["prompt_tokens", "completion_tokens", "calls"])
def test_token_usage_음수를_거부한다(field: str) -> None:
    # 음수가 통과하면 NFR-2 비용 총계가 누적 도중에 조용히 줄어든다. 합산 뒤에는
    # 개별 항이 남지 않아 어느 호출이 넣었는지 역추적할 수 없다.
    with pytest.raises(ValueError, match=field):
        TokenUsage(**{field: -100})


def test_token_usage_합산_결과도_가드를_지난다() -> None:
    # __add__가 object.__new__로 우회하지 않고 생성자를 거친다는 것을 실제로
    # 확인한다. frozen을 뚫고 음수를 심어야만 확인되는 성질이다 - 정상 경로로는
    # 음수 피연산자를 만들 수 없기 때문이다.
    tainted = TokenUsage()
    object.__setattr__(tainted, "calls", -5)
    with pytest.raises(ValueError, match="calls"):
        _ = tainted + TokenUsage()


def test_completion은_사용량을_동반한다() -> None:
    c = Completion(text="hello", usage=TokenUsage(prompt_tokens=1))
    assert c.text == "hello"
    assert c.usage.prompt_tokens == 1


def test_예외_계층_두_갈래가_공통_조상을_갖는다() -> None:
    # 호출부가 "프로바이더 문제 전부"를 한 번에 잡을 수 있어야 한다.
    assert issubclass(RetryableProviderError, ProviderError)
    assert issubclass(FatalProviderError, ProviderError)


def test_재시도_가능_실패는_서로_구분된다() -> None:
    # 이 구분이 없으면 인증 실패도 세그먼트 실패로 취급되어 파일 전체가
    # 실패 표시로 완주한다 (설계 §4.2).
    assert not issubclass(FatalProviderError, RetryableProviderError)
    assert not issubclass(RetryableProviderError, FatalProviderError)


def test_retry_after를_실어_나른다() -> None:
    err = RetryableProviderError("429", retry_after_s=2.5)
    assert err.retry_after_s == 2.5


def test_retry_after는_없을_수_있다() -> None:
    assert RetryableProviderError("timeout").retry_after_s is None


def test_retry_after_0초는_유효하다() -> None:
    # 도메인은 "0 이상"이다. 0을 무효로 떨어뜨리면 "지금 바로 다시 걸어도 된다"는
    # 서버의 지시가 지수 백오프로 바뀌어 불필요하게 느려진다.
    assert RetryableProviderError("429", retry_after_s=0).retry_after_s == 0


# id를 손으로 붙이지 않는다. pytest가 파라미터 id의 비ASCII를 이스케이프해
# `[음수]` 같은 잡음을 출력하는데, float의 자동 id(`[-1.0]`·`[nan]`)는
# ASCII이면서 값을 그대로 보여 준다.
@pytest.mark.parametrize("bad", [-1.0, float("inf"), float("-inf"), float("nan")])
def test_도메인_밖_retry_after는_None으로_떨어진다(bad: float) -> None:
    # 이 값들이 그대로 실려 나가면 호출부의 sleep()이 ValueError(음수·nan) 또는
    # OverflowError(inf)를 내는데, 둘 다 ProviderError 밖이라 호출부의
    # `except RetryableProviderError` 핸들러 **본문 안에서** 터져 아무도 잡지
    # 못한 채 번역 루프 밖으로 샌다 (설계 §4.2).
    assert RetryableProviderError("429", retry_after_s=bad).retry_after_s is None


def test_숫자가_아닌_retry_after도_None으로_떨어진다() -> None:
    # 파싱하지 않은 Retry-After 헤더가 그대로 오는 경우다. isinstance 검사가
    # 없으면 math.isfinite가 TypeError를 내는데, 그 TypeError는 예외를 만드는
    # 도중에 발생해 원래 실패 원인(429)을 그 자리에서 지운다.
    assert RetryableProviderError("429", retry_after_s="2.5").retry_after_s is None  # type: ignore[arg-type]


def test_nan은_비교만으로는_걸러지지_않는다() -> None:
    # 위 도메인 검사가 math.isfinite를 쓰는 이유를 고정한다. `< 0`만으로 거르면
    # nan은 어떤 비교에도 False라 그대로 통과한다 - 이 성질이 파이썬에서
    # 바뀌지 않는 한 isfinite를 빼면 안 된다.
    assert not (math.nan < 0)
    assert not (math.nan >= 0)


# --- CountingProvider (FR-7.4 · 설계 D6·D7) ---


class _고정프로바이더:
    """호출마다 같은 usage를 내는 가짜."""

    name = "fake"

    def __init__(self, usage: TokenUsage) -> None:
        self._usage = usage
        self.calls = 0

    def complete(self, messages, *, temperature, max_tokens) -> Completion:
        self.calls += 1
        return Completion(text="ok", usage=self._usage)


def _메시지() -> list[ChatMessage]:
    return [ChatMessage(role="user", content="안녕")]


def test_통과한_토큰을_누적한다() -> None:
    inner = _고정프로바이더(TokenUsage(prompt_tokens=10, completion_tokens=5, calls=1))
    counting = CountingProvider(inner)

    for _ in range(3):
        counting.complete(_메시지(), temperature=1.0, max_tokens=None)

    assert counting.usage.prompt_tokens == 30
    assert counting.usage.completion_tokens == 15
    assert counting.usage.calls == 3


def test_한_번도_안_부르면_0이다() -> None:
    """Tier 1을 켰지만 후보가 0건인 실행이 이 상태다."""
    counting = CountingProvider(_고정프로바이더(TokenUsage()))
    assert counting.usage.prompt_tokens == 0
    assert counting.usage.completion_tokens == 0
    assert counting.usage.calls == 0


def test_결과를_그대로_돌려준다() -> None:
    inner = _고정프로바이더(TokenUsage(prompt_tokens=1, completion_tokens=1, calls=1))
    completion = CountingProvider(inner).complete(_메시지(), temperature=0.5, max_tokens=99)
    assert completion.text == "ok"


def test_인자를_그대로_넘긴다() -> None:
    """`temperature`·`max_tokens`를 바꿔 넘기면 캐시 키가 어긋나 전량 미스가 된다."""
    받은: dict[str, object] = {}

    class _기록프로바이더:
        name = "rec"

        def complete(self, messages, *, temperature, max_tokens) -> Completion:
            받은.update(temperature=temperature, max_tokens=max_tokens, n=len(messages))
            return Completion(text="", usage=TokenUsage())

    CountingProvider(_기록프로바이더()).complete(_메시지(), temperature=0.7, max_tokens=4096)
    assert 받은 == {"temperature": 0.7, "max_tokens": 4096, "n": 1}


def test_예외를_삼키지_않는다() -> None:
    """**삼키면 `SelfConsistency`가 `FatalProviderError`를 다시 던지는 설계가 죽는다.**

    401을 삼키면 그 실행은 "Tier 1이 돌았고 아무것도 안 걸렸다"로 보인다.
    """

    class _터지는프로바이더:
        name = "boom"

        def complete(self, messages, *, temperature, max_tokens) -> Completion:
            raise FatalProviderError("401")

    with pytest.raises(FatalProviderError):
        CountingProvider(_터지는프로바이더()).complete(_메시지(), temperature=1.0, max_tokens=None)


def test_name을_위임한다() -> None:
    """`identity` 조립이 프로바이더 이름을 읽으므로 래퍼 이름이 새면 캐시가 갈라진다."""
    assert CountingProvider(_고정프로바이더(TokenUsage())).name == "fake"
