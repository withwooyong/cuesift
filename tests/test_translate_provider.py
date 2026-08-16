"""프로바이더 계약과 예외 계층 (요구사항정의서 FR-2.5, FR-2.6)."""

from __future__ import annotations

import math
from typing import get_args

import pytest

from cuesift.translate.provider import (
    ChatMessage,
    Completion,
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
