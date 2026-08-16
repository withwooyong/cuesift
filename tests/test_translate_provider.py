"""프로바이더 계약과 예외 계층 (요구사항정의서 FR-2.5, FR-2.6)."""

from __future__ import annotations

import pytest

from cuesift.translate.provider import (
    ChatMessage,
    Completion,
    FatalProviderError,
    ProviderError,
    RetryableProviderError,
    TokenUsage,
)


def test_chat_message_역할이_셋_중_하나여야_한다() -> None:
    assert ChatMessage(role="system", content="너는 번역가다").role == "system"


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
