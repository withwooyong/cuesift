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

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """프로바이더에 보내는 메시지 한 개."""

    role: Role
    content: str

    _ROLES = ("system", "user", "assistant")

    def __post_init__(self) -> None:
        # Literal은 런타임에 아무것도 막지 않는다. 잘못된 역할은 서버가 400을
        # 내고 그 400은 FatalProviderError가 되어 전체를 중단시키는데, 그때는
        # 원인이 프롬프트 조립 코드라는 사실이 보이지 않는다. Span.__post_init__
        # 이 같은 이유로 side를 검사한다.
        if self.role not in self._ROLES:
            raise ValueError(f"role({self.role!r})은 {self._ROLES} 중 하나여야 한다")
        # **content도 같은 실패 모드다.** 막지 않으면 그 값이 요청 본문에
        # 그대로 실린다 - 실측: `{"role": "user", "content": 123}`. 서버가 내는
        # 400은 위와 똑같이 Fatal로 분류되고, 원인이 조립 코드라는 사실은
        # 역시 보이지 않는다. 한쪽 필드만 막는 것이 비대칭이었다.
        #
        # `isinstance(self.content, str)`이어야 한다. `bool`이 `int`의 하위라
        # 숫자형을 따로 열거하는 형태로 쓰면 True가 새어 들어온다.
        if not isinstance(self.content, str):
            raise ValueError(f"content는 str이어야 한다: {type(self.content).__name__}")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """호출 하나 또는 누적분의 토큰 사용량 (NFR-2 비용 투명성)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    def __post_init__(self) -> None:
        # 음수가 통과하면 NFR-2 비용 리포트가 **누적 도중에 조용히 줄어든다.**
        # 합산이 끝나면 개별 항이 남지 않으므로 어느 호출이 음수를 넣었는지
        # 역추적할 수 없고, 총계가 틀렸다는 사실조차 드러나지 않는다.
        # 형제 모델 넷(Span·Segment·Signal·SegmentRisk)이 모두 같은 자리에서
        # 방어한다. __add__도 이 생성자를 거치므로 합산 결과 역시 이 검사를 지난다.
        for name, value in (
            ("prompt_tokens", self.prompt_tokens),
            ("completion_tokens", self.completion_tokens),
            ("calls", self.calls),
        ):
            if value < 0:
                raise ValueError(f"{name}({value})은 음수일 수 없다")

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

    `retry_after_s`는 서버가 지정한 대기이고 **도메인은 "0 이상의 유한한 초"** 다.
    이 값은 양쪽으로 위험하므로 한 방향만 막으면 안 된다.

    - **무시하면** 서버가 지정한 대기를 어겨 일시적 제한이 영구 차단으로 승격된다.
    - **그대로 존중하면** 도메인 밖 값이 `time.sleep()`에 들어가 음수·nan은
      `ValueError`를, inf는 `OverflowError`를 낸다. 이 둘은 `ProviderError`의
      자손이 **아니고**, 호출부의 `except RetryableProviderError` **핸들러 본문
      안에서** 발생하므로 그 핸들러가 잡지 못한 채 번역 루프 밖으로 샌다 -
      설계 §4.2가 그은 "호출자가 틀렸나 데이터가 틀렸나" 분기를 통째로 우회한다.

    그래서 도메인 밖 값은 `None`("쓸 수 있는 힌트가 없음")으로 떨어뜨리고 호출부의
    지수 백오프에 맡긴다. **상한은 여기서 다루지 않는다** - `Retry-After: 86400`을
    그대로 자면 CLI가 하루 멈추지만 그것은 계약이 아니라 정책이라 백오프 계산의 몫이다.
    """

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        # 무효값에 예외를 던지지 않는 것이 핵심이다. 예외를 만드는 중에 예외를
        # 던지면 원래 실패 원인(429·503)이 그 자리에서 사라진다.
        #
        # isfinite가 필요한 이유: 아래 조건은 **수용형**("0 이상이면 받는다")이라
        # 음수도 nan도 `>= 0.0`에서 이미 걸린다(nan은 어떤 비교에도 False다).
        # isfinite가 **혼자 막는 값은 `+inf` 하나뿐**이다 - `inf >= 0.0`이 True라
        # 비교를 통과한다. 지우면 inf가 그대로 실려 나가 호출부의 sleep(inf)가
        # OverflowError를 내는데, 그것은 ProviderError 밖이라 호출부의
        # `except RetryableProviderError`가 잡지 못한다 (설계 §4.2).
        # 조건을 거부형(`if x < 0: 버린다`)으로 뒤집으면 이번엔 nan이 샌다 -
        # isfinite는 두 형태 모두에서 필요하고 막는 값만 달라질 뿐이다.
        # isinstance가 필요한 이유: 숫자가 아닌 값(파싱하지 않은 Retry-After 헤더
        # 문자열이 대표적이다)에 isfinite를 걸면 TypeError가 나는데, 그것이 바로
        # 이 줄들이 막으려는 "생성자가 던지는 예외"다.
        usable = (
            isinstance(retry_after_s, int | float)
            and math.isfinite(retry_after_s)
            and retry_after_s >= 0.0
        )
        self.retry_after_s = retry_after_s if usable else None


class FatalProviderError(ProviderError):
    """다시 걸어도 같은 결과인 실패 - 401, 403 인증, 400 스키마, 404 모델 없음.

    이것을 재시도하면 실패 1회가 실패 N회로 늘어날 뿐이고, 진짜 원인이
    대량의 세그먼트 실패 아래 묻힌다.
    """


class Provider(Protocol):
    """LLM 호출의 계약. 표면을 최소로 두는 것이 NFR-5(코드 수정 없이 추가)를 돕는다.

    **`@runtime_checkable`을 붙이지 않는다.** 이 프로토콜은 정적 계약이고,
    런타임에 "이것이 프로바이더인가"를 판별해야 하는 지점이 파이프라인 어디에도
    없다. 붙이면 `isinstance`가 열리지만 그 검사는 `complete`의 **존재만** 보고
    시그니처는 보지 않는다 - 인자 이름이나 키워드 전용(`*`) 여부가 어긋난 구현이
    "프로바이더 맞음"으로 통과하고, 실패는 검사 지점이 아니라 한참 뒤 실제 호출
    지점에서 드러난다. 쓰지 않는 검사 수단을 없애면 그 거짓 안심도 같이 사라진다.
    """

    name: str

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> Completion:
        """한 번 호출하고 결과를 돌려준다. **아래 셋은 계약이다** (NFR-5).

        NFR-5는 "코드 수정 없이 프로바이더를 추가한다"인데, 계약이 시그니처만
        말하면 서드파티 구현이 **engine의 폴백을 통째로 우회하는 방식으로**
        시그니처를 만족시킬 수 있다. 아래는 전부 실측한 실패다.

        | 어긴 구현 | 실제 결과 |
        | --- | --- |
        | 맨 `ProviderError`를 던진다 | **밖으로 샌다.** 호출 1회, 재시도 0회, 실행 사망 |
        | `Completion(text=None)` | `AttributeError` - `ProviderError` 밖이다 |

        1. **실패는 `RetryableProviderError` 또는 `FatalProviderError`로
           던진다.** 기반 클래스 `ProviderError`를 직접 던지면 안 된다 -
           engine의 `_call_with_retry`는 두 자손만 잡으므로 기반 클래스는
           재시도도 폴백도 없이 호출 스택을 그대로 빠져나간다. `ProviderError`가
           "호출부가 전부를 한 번에 잡을 수 있게 한다"는 것은 **호출부가
           그것을 잡을 때** 성립하는 말이고, 주 호출부인 engine은 잡지 않는다.
        2. **`Completion.text`는 반드시 `str`이다.** `None`은 파싱 경로에서
           `AttributeError`가 되는데 그것은 `ProviderError` 밖이라 폴백이
           받지 못한다. 내용이 없으면 빈 문자열을 돌려준다.
        3. **재시도하지 않는다.** engine이 한다. 양쪽이 다 하면 총 호출이
           곱해지고 백오프 대기가 이중으로 쌓인다.

        `openai_compat.py`가 이 셋을 지키는 참조 구현이다.
        """
        ...
