"""STT 프로바이더 계약 (요구사항정의서 FR-1.2 · 설계 D3).

**이 모듈은 HTTP를 모른다.** 계약과 방어만 두고 왕복은 `openai_compat.py`가 한다 -
`translate/provider.py`와 `translate/openai_compat.py`의 관계와 같다.

`Transcript`는 **초 단위 float**을 담는다. 밀리초 변환(D5)은 인제스트가 하는데,
그 이유는 `Segment` 조립 자체가 인제스트 정책이기 때문이다(D3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TranscriptCue:
    """전사 큐 하나. **초 단위**다 (D5는 인제스트에서 적용된다).

    `__post_init__`의 셋은 전부 **`ProviderError` 밖으로 새는 예외를 막는 것**이다.
    이 방어가 없으면 인제스트의 `round(start_s * 1000)`이 아래를 낸다 (실측):

    | 값 | 예외 | ProviderError 자손인가 |
    | --- | --- | --- |
    | `nan` | `ValueError` | ❌ |
    | `inf` | `OverflowError` | ❌ |
    | `"1.5"` | `TypeError` | ❌ |

    셋 다 호출부의 `except FatalProviderError`를 지나쳐 미처리 traceback이 되고
    종료 코드 1이 된다 - **이 저장소에서 1은 "규격 위반 발견"이라 STT 결함이
    자막 결함으로 오보된다.** `translate/provider.py`의 `TokenUsage`가 같은
    이유로 같은 방어를 갖고 있다.
    """

    start_s: float
    end_s: float
    text: str

    def __post_init__(self) -> None:
        for name, value in (("start_s", self.start_s), ("end_s", self.end_s)):
            # **`isinstance` 검사가 `isfinite`보다 먼저여야 한다.**
            # `math.isfinite("1.5")`는 `TypeError`를 내고 그것은 이 함수가
            # 막으려는 예외 그 자체다 (실측).
            #
            # **`bool`을 따로 뺀다.** `isinstance(True, int | float)`가 `True`라
            # 타입 검사만으로는 통과하고, `round(True * 1000)`은 1000이 되어
            # **1초짜리 큐가 예외 없이 생긴다.** 조용히 틀리는 부류다.
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{name}({value!r})이 유한한 수가 아니다")
            if not math.isfinite(value):
                raise ValueError(f"{name}({value!r})이 유한한 수가 아니다")
            if value < 0:
                # 음수는 `Segment`도 안 본다 - `__post_init__`은 역전만 검사한다.
                # 여기서 놓치면 음수 밀리초가 CPS를 음수로 만든다.
                raise ValueError(f"{name}({value})이 음수다")
        if self.end_s < self.start_s:
            # 역전은 Whisper 계열이 실제로 낸다(설계 §7).
            raise ValueError(f"end_s({self.end_s})가 start_s({self.start_s})보다 작다")
        if not isinstance(self.text, str):
            # `None`이면 `Segment.source_text`가 `None`이 되고 Tier 0 신호가
            # 전부 `AttributeError`로 죽는다 - 그것도 `IngestError` 밖이다.
            raise ValueError(f"text는 str이어야 한다: {type(self.text).__name__}")


@dataclass(frozen=True, slots=True)
class Transcript:
    """전사 결과 전체 (D3).

    **`Segment`를 담지 않는다.** id 부여·`index` 재부여·플래그는 인제스트
    정책이고, 프로바이더가 그것을 알면 층이 섞인다.

    `language`가 `| None`인 것은 백엔드가 그 필드를 안 낼 수 있기 때문이다
    (§12 Q3 - 능력이 균일하지 않다). 없으면 호출자가 준 값으로 되돌린다(FR-1.5).
    """

    cues: tuple[TranscriptCue, ...]
    language: str | None
    model: str


class SttProvider(Protocol):
    """STT 호출의 계약. `translate/provider.py`의 `Provider`와 같은 규율을 따른다.

    **`@runtime_checkable`을 붙이지 않는다.** 그 검사는 메서드의 **존재만** 보고
    시그니처는 보지 않아, 인자 이름이 어긋난 구현이 "프로바이더 맞음"으로
    통과한다. 판별해야 하는 지점도 파이프라인에 없다.

    구현이 지켜야 하는 셋은 `Provider`와 동일하다.

    1. 실패는 `RetryableProviderError` 또는 `FatalProviderError`로 던진다.
       기반 `ProviderError`를 직접 던지면 호출부의 폴백을 우회한다.
    2. 타임코드 없는 응답은 **성공이 아니다** - `FatalProviderError`다(D4).
    3. 재시도하지 않는다. 호출부가 한다.
    """

    name: str

    def transcribe(self, audio: Path, *, language: str | None) -> Transcript:
        """오디오 파일 하나를 전사한다.

        `audio`는 **경로**다(P2). 프로바이더가 직접 열며, 읽기 실패는
        `FatalProviderError`로 번역한다 - 재시도해도 같기 때문이다.
        """
        ...
