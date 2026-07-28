"""검수 큐 선별 정책 (요구사항정의서 FR-6.3, 스펙 §6.2).

두 정책 모두 **새 리스트를 반환하고 입력을 변형하지 않는다.** 같은 위험도
목록에 여러 예산을 적용하는 것이 스펙 §6.1의 예산 스윕이므로, 입력을
변형하면 두 번째 예산부터 결과가 오염된다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace

from cuesift.segment import SegmentRisk


def _selected_copy(risk: SegmentRisk) -> SegmentRisk:
    return replace(risk, selected=True)


def _sorted_desc(risks: Sequence[SegmentRisk]) -> list[SegmentRisk]:
    """위험도 내림차순. 동점은 세그먼트 ID로 깨뜨린다.

    NFR-3(재현성) — 동점에서 순서가 흔들리면 벤치마크 숫자가
    실행마다 달라진다.
    """
    return sorted(risks, key=lambda r: (-r.risk_score, r.segment_id))


def select_by_budget(risks: Sequence[SegmentRisk], budget_ratio: float) -> list[SegmentRisk]:
    """상위 `budget_ratio` 비율을 검수 큐에 담는다 (FR-6.3 ①).

    hard fail은 예산과 무관하게 항상 포함된다 (FR-6.2). 따라서 반환된
    개수가 `len(risks) * budget_ratio`를 넘을 수 있다 — 이것이 스펙 §6.2가
    "요청 예산"과 "실제 검수 비율"을 구분하는 이유다.
    """
    if not 0.0 <= budget_ratio <= 1.0:
        raise ValueError(f"budget_ratio는 0.0~1.0이어야 한다 (받은 값: {budget_ratio})")
    if not risks:
        return []

    hard = [r for r in risks if r.hard_fail]
    rest = _sorted_desc([r for r in risks if not r.hard_fail])

    # 올림한다. 10건에 5% 예산이면 0.5건인데, 내림하면 0건이 되어
    # 트리아지가 아무것도 안 하고 통과한다.
    quota = math.ceil(len(risks) * budget_ratio)
    remaining = max(0, quota - len(hard))

    return [_selected_copy(r) for r in hard + rest[:remaining]]


def select_by_threshold(risks: Sequence[SegmentRisk], threshold: float) -> list[SegmentRisk]:
    """위험도가 `threshold` 이상인 것을 담는다 (FR-6.3 ②)."""
    picked = [r for r in risks if r.hard_fail or r.risk_score >= threshold]
    return [_selected_copy(r) for r in _sorted_desc(picked)]


def review_ratio(risks: Sequence[SegmentRisk]) -> float:
    """실제로 검수 큐에 들어간 비율 (스펙 §6.2).

    **요청 예산이 아니라 이 값으로 배수를 계산한다.** hard fail이 예산을
    우회하므로 둘은 다르고, 요청 예산으로 나누면 배수가 부풀려진다.
    """
    if not risks:
        return 0.0
    return sum(1 for r in risks if r.selected) / len(risks)
