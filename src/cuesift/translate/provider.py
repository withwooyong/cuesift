"""LLM 프로바이더의 계약과 실패 분류 (FR-2.5, FR-2.6).

**이 모듈에서 프로토콜보다 중요한 것은 예외 계층이다.** FR-2.6은 "실패한
세그먼트를 재시도하고, 실패 시 해당 세그먼트만 표시 후 진행"이라고만 적혀
있어서, 곧이곧대로 구현하면 인증 실패도 "세그먼트 실패"로 취급되어 파일
전체가 실패 표시로 완주한다. 사용자는 800건 실패 리포트를 받고 원인이 키
하나였다는 것을 모른다 (설계 §4.2).

축은 종료 코드가 이미 그은 것과 같다 - exit 2("명령줄이 틀림")와
exit 66("파일 내용이 틀림")의 구분, 즉 "호출자가 틀렸나, 데이터가 틀렸나"다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]

_ROLES = ("system", "user", "assistant")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """프로바이더에 보내는 메시지 한 개."""

    role: Role
    content: str

    def __post_init__(self) -> None:
        # Literal은 런타임에 아무것도 막지 않는다. 잘못된 역할은 서버가 400을
        # 내고 그 400은 FatalProviderError가 되어 전체를 중단시키는데, 그때는
        # 원인이 프롬프트 조립 코드라는 사실이 보이지 않는다. Span.__post_init__
        # 이 같은 이유로 side를 검사한다.
        if self.role not in _ROLES:
            raise ValueError(f"role({self.role!r})은 {_ROLES} 중 하나여야 한다")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """호출 하나 또는 누적분의 토큰 사용량 (NFR-2 비용 투명성)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """배치 루프가 빈 값부터 누적할 수 있게 한다."""
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            calls=self.calls + other.calls,
        )


@dataclass(frozen=True, slots=True)
class Completion:
    """프로바이더가 돌려준 것."""

    text: str
    usage: TokenUsage


class ProviderError(Exception):
    """프로바이더 호출 실패의 최상위. 호출부가 전부를 한 번에 잡을 수 있게 한다."""


class RetryableProviderError(ProviderError):
    """다시 걸면 성공할 수 있는 실패 - 429, 5xx, 타임아웃, 연결 끊김.

    `retry_after_s`는 서버가 지정한 대기다. 무시하면 서버가 지정한 대기를
    어겨 일시적 제한이 영구 차단으로 승격될 수 있다.
    """

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class FatalProviderError(ProviderError):
    """다시 걸어도 같은 결과인 실패 - 401, 403 인증, 400 스키마, 404 모델 없음.

    이것을 재시도하면 실패 1회가 실패 N회로 늘어날 뿐이고, 진짜 원인이
    대량의 세그먼트 실패 아래 묻힌다.
    """


class Provider(Protocol):
    """LLM 호출의 계약. 표면을 최소로 두는 것이 NFR-5(코드 수정 없이 추가)를 돕는다.

    **`@runtime_checkable`을 붙이지 않는다.** 붙이면 `isinstance`가 통과하는데
    그 검사는 `complete`의 존재만 보고 시그니처는 보지 않는다 - 인자 이름과
    키워드 전용 여부가 어긋난 구현이 "프로바이더 맞음"으로 통과하고, 실패는
    검사 지점이 아니라 실제 호출 지점에서 드러난다. `signals/base.py`가 같은
    이유로 프로토콜 `isinstance` 대신 `hasattr`로 갈랐다.
    """

    name: str

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> Completion: ...
