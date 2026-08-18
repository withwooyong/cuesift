"""트리아지 결과 모델 (요구사항정의서 FR-7.2 · 설계 §7.1).

**화면 요약과 `review.json`의 공통 출처다.** 두 소비자가 수치를 각자 세면
조용히 갈라지는데, 그때 프로그램은 정상 종료하고 파일도 정상이며 종료 코드도
0이라 어떤 게이트에도 걸리지 않는다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from cuesift.segment import Segment, SegmentRisk
from cuesift.translate.provider import TokenUsage
from cuesift.triage import review_ratio as _review_ratio


@dataclass(frozen=True, slots=True)
class TriageOutcome:
    """대상 언어 하나의 트리아지 결과.

    **`risks`는 `select_by_*`가 돌려준 전체 목록이다.** 선별분만 담으면
    `review_ratio`가 언제나 1.0이 되고, 그 값이 스펙 §6.2의 "실제 검수 비율"이자
    README 배수의 분모라 조용히 틀리면 프로젝트의 핵심 주장이 무너진다.

    **`policy_label`과 `policy_kind`/`policy_value`는 중복이 아니다.** 라벨은
    사용자가 친 원본 문자열(`"10%"`)을 보존해 화면에 그대로 되돌려 주고,
    kind/value는 정규화된 값이라 `review.json`이 쓴다. 라벨을 kind/value에서
    재생성하면 화면 출력이 `"예산 10.0%"`로 바뀐다.

    **`segments`는 `risks`와 같은 집합이다**(번역 실패분이 빠진 것). `SegmentRisk`는
    `segment_id`만 갖고 타임코드·원문·번역문은 `Segment`에 있어 FR-7.2가 요구한
    필드를 채우려면 둘이 함께 필요하다.
    """

    source_lang: str
    target_lang: str
    profile_name: str
    policy_label: str
    policy_kind: str  # "budget" | "threshold"
    policy_value: float
    risks: tuple[SegmentRisk, ...]
    segments: tuple[Segment, ...]
    excluded_failures: int
    usage: TokenUsage | None

    def __post_init__(self) -> None:
        # 형제 모델 넷(`Span`·`Segment`·`Signal`·`SegmentRisk`)과 같은 자리의 방어다.
        # 둘이 어긋나면 리포트 생성기의 `by_id[risk.segment_id]`가 KeyError로 죽는데,
        # 그 시점은 파일을 쓰는 도중이라 **어느 조합이 어긋났는지가 스택에 없다.**
        # 여기서 막으면 실패가 생성 시점으로 앞당겨진다.
        if len(self.segments) != len(self.risks):
            raise ValueError(
                f"segments({len(self.segments)})와 risks({len(self.risks)})의 길이가 다르다"
            )
        # 음수면 `total_segments`가 `triaged_segments`보다 작아져 화면이 "2개 중
        # 5개 검수"라는 불가능한 요약을 낸다. 프로그램은 정상 종료하고 종료 코드도
        # 0이라 어떤 게이트에도 걸리지 않는다.
        if self.excluded_failures < 0:
            raise ValueError(f"excluded_failures({self.excluded_failures})가 음수다")

    @property
    def triaged_segments(self) -> int:
        """트리아지 대상 수. **`review_ratio`의 분모다** (설계 §6.2)."""
        return len(self.risks)

    @property
    def total_segments(self) -> int:
        """트랙 전체. `triaged + excluded`가 이 값이 되어야 파일 안에서 검산된다.

        **`triaged_segments`를 재사용한다.** `len(self.risks)`로 따로 세면 분모의
        정의가 2곳이 되고, 그때 `triaged`의 의미만 바뀌면 위 검산식이 조용히
        거짓이 된다 — 두 값이 지금 같다는 것은 우연이지 보장이 아니다.
        """
        return self.triaged_segments + self.excluded_failures

    @property
    def selected(self) -> tuple[SegmentRisk, ...]:
        """검수 큐에 담긴 것. `review.json`의 `segments[]`가 이것이다 (설계 D3)."""
        return tuple(r for r in self.risks if r.selected)

    @property
    def selected_for_review(self) -> int:
        return len(self.selected)

    @property
    def hard_fail_count(self) -> int:
        return sum(1 for r in self.risks if r.hard_fail)

    @property
    def signal_hits(self) -> dict[str, int]:
        """신호별 적발 건수. **전체를 본다 - 선별분이 아니다.**

        선별분으로 좁히면 예산 밖으로 밀린 위험이 사라져 사용자가 다음 예산을
        정할 근거를 잃는다. 정렬은 NFR-3(재현성)이다 - `Counter`의 순서는 삽입
        순이라 세그먼트 순서가 바뀌면 출력이 달라진다.

        **`reasons`는 0점 신호를 담지 않는다**(`fuse.py:73` - "0점 신호를 사유에
        넣으면 리포트가 '이것 때문에 뽑혔다'고 거짓말한다"). 따라서 이 집계가 곧
        "적발 **건수**"이지 "신호가 달린 횟수"가 아니다. 이 근거가 없으면 뒷사람이
        `signals`를 세도록 고쳐 놓고 이름은 그대로 두게 된다.
        """
        counts: Counter[str] = Counter()
        for risk in self.risks:
            counts.update(risk.reasons)
        return dict(sorted(counts.items()))

    @property
    def review_ratio(self) -> float:
        """실제 검수 비율 (0.0~1.0). 라이브러리 함수를 그대로 쓴다."""
        return _review_ratio(self.risks)
