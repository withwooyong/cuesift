"""파이프라인 전체가 주고받는 데이터 모델 (요구사항정의서 §7.3).

**타임코드는 정수 밀리초로 둔다.** §7.3은 `timedelta`로 적었으나 최종
산출물 계약인 §8.4 `review.json`이 `start_ms`/`end_ms`를 쓴다. 두 표현을
섞으면 직렬화 지점마다 변환이 생기고, CPS 계산에서 부동소수 오차가 들어온다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class Span:
    """텍스트 안의 문제 구간. 리포트 하이라이트에 쓴다 (§7.3, FR-7.3).

    `side`는 이 구간이 원문과 번역문 중 **어느 쪽**을 가리키는지다.
    신호마다 다르다 — 미번역·규격 위반은 번역문을, 용어 누락·숫자 누락은
    원문을 가리킨다(누락이므로 번역문에는 없다). 판별자가 없으면
    리포트가 어느 쪽을 칠할지 알 수 없다.
    """

    start: int
    end: int
    side: Literal["source", "target"] = "target"

    _SIDES = ("source", "target")

    def __post_init__(self) -> None:
        # `Literal`은 타입 힌트일 뿐 런타임에 아무것도 막지 않는다 —
        # `side=None`이나 `"TARGET"`이 그대로 통과한다. 그러면 FR-7.3
        # 리포트가 칠할 쪽을 잃는데, 그 사실은 신호를 만든 코드에서 멀리
        # 떨어진 리포트 생성 시점에야 드러난다. 바로 아래 start/end는
        # 이미 막고 있으므로 여기만 비우면 방어가 반쪽이다.
        if self.side not in self._SIDES:
            raise ValueError(f"side({self.side!r})는 'source' 또는 'target'이어야 한다")
        if self.end < self.start:
            raise ValueError(f"end({self.end})가 start({self.start})보다 작다")


@dataclass(slots=True)
class Segment:
    """자막 한 덩어리. 판정의 최소 단위다 (§0.2)."""

    id: str
    index: int
    start_ms: int
    end_ms: int
    source_text: str
    target_text: str | None = None
    speaker: str | None = None  # v0.2 화자분리용 자리
    meta: dict = field(default_factory=dict)
    # STT로 생성한 원문인가 (FR-1.4). **점수에도 hard fail에도 들어가지 않는다.**
    #
    # 넣으면 무엇이 깨지는지가 이 필드의 존재 이유다. STT 입력에서는 **전** 세그먼트가
    # True라, hard fail로 올리면 FR-6.2에 따라 전량이 검수 예산을 우회해
    # `review_ratio()`가 1.0이 된다 — README 최상단의 무작위 베이스라인 대비 배수가
    # **산출 불가능**해지고, 그 숫자가 "AI 래퍼가 아니다"를 증명하는 유일한 자료다.
    # 점수에 가중치로 더해도 전체가 같은 양만큼 올라 **순위에 정보를 하나도 주지
    # 않으면서** 상수만 더한다 (설계 §5의 세 갈래 비교).
    #
    # 기본값 `False`가 자막 경로를 한 줄도 바꾸지 않게 하는 장치다 — 없으면
    # 키워드 인자로 `Segment(...)`를 부르는 기존 호출 전부가 `TypeError`가 된다.
    # 소비처는 `report/json_report.py`와 `report/html_report.py` 둘로 예정돼 있고
    # (WP9 Task 6), 이 커밋 시점에는 표시 소비처가 아직 없다.
    source_from_stt: bool = False

    def __post_init__(self) -> None:
        # 음수 duration은 CPS를 음수로 만들어 규격 검사를 통째로 무의미하게 한다.
        if self.end_ms < self.start_ms:
            raise ValueError(f"end_ms({self.end_ms})가 start_ms({self.start_ms})보다 작다")

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True, slots=True)
class Signal:
    """수집기 하나가 낸 판정 결과 (§7.3).

    `score`는 0.0(안전)~1.0(위험)으로 정규화한다 (FR-6.1).
    `hard_fail`은 가중합을 우회해 무조건 검수 큐에 들어간다 (FR-6.2).
    """

    name: str
    tier: int
    score: float
    hard_fail: bool = False
    spans: tuple[Span, ...] = ()
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"score({self.score})가 0.0~1.0 범위를 벗어났다")


@dataclass(slots=True)
class SegmentRisk:
    """세그먼트 하나의 융합 결과와 선별 여부 (§7.3)."""

    segment_id: str
    signals: list[Signal]
    risk_score: float
    hard_fail: bool
    selected: bool = False
    reasons: list[str] = field(default_factory=list)  # 선별 사유 (FR-6.4)

    def __post_init__(self) -> None:
        # 형제 세 모델과 같은 방어다. 범위를 벗어난 값은 triage의
        # 정렬·임계 비교를 조용히 깨뜨린다. NaN도 이 비교에서 걸린다.
        if not 0.0 <= self.risk_score <= 1.0:
            raise ValueError(f"risk_score({self.risk_score})가 0.0~1.0 범위를 벗어났다")
