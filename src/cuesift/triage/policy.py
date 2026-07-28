"""검수 큐 선별 정책 (요구사항정의서 FR-6.3, 스펙 §6.2).

두 정책 모두 **전체 목록을 반환하고 입력을 변형하지 않는다.** 선별된 것만
반환하면 그 결과를 그대로 `review_ratio`에 넘겼을 때 언제나 1.0이 나온다 —
그 값이 스펙 §6.2의 "실제 검수 비율"이자 README 최상단 배수의 분모라,
조용히 틀리면 프로젝트의 핵심 주장이 무너진다. 선별된 것만 필요하면
호출자가 `[r for r in result if r.selected]`로 거른다.

같은 위험도 목록에 여러 예산을 적용하는 것이 스펙 §6.1의 예산 스윕이므로,
입력을 변형하면(또는 사본이 원본과 가변 필드를 공유하면) 두 번째 예산부터
결과가 오염된다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace

from cuesift.segment import SegmentRisk


def _copy(risk: SegmentRisk, *, selected: bool) -> SegmentRisk:
    """선별 여부를 반영한 사본. **가변 필드까지 복사한다.**

    `dataclasses.replace`는 얕은 복사라 `signals`·`reasons`를 그대로 두면
    사본이 원본과 같은 리스트 객체를 참조한다. 나중에 사본의 `reasons`에
    선별 사유를 채워 넣는 코드가 `append`하면 원본이 조용히 오염되고,
    예산 스윕(§6.1)이 같은 원본에 여러 예산을 차례로 적용하는 시나리오와
    정확히 충돌한다. 선별되지 않은 항목도 사본으로 돌려준다 — 원본을 그대로
    내보내면 호출자가 반환값을 수정했을 때 입력이 오염된다.
    """
    return replace(
        risk,
        selected=selected,
        signals=list(risk.signals),
        reasons=list(risk.reasons),
    )


def _sorted_desc(risks: Sequence[SegmentRisk]) -> list[SegmentRisk]:
    """위험도 내림차순. 동점은 세그먼트 ID로 깨뜨린다.

    NFR-3(재현성) — 동점에서 순서가 흔들리면 벤치마크 숫자가
    실행마다 달라진다. hard fail 여부와 무관하게 **전체를 한 번에**
    정렬한다 — hard fail만 따로 골라 입력 순서 그대로 두면 그 구간의
    순서가 호출자가 넘긴 리스트 순서에 좌우된다.
    """
    return sorted(risks, key=lambda r: (-r.risk_score, r.segment_id))


def select_by_budget(risks: Sequence[SegmentRisk], budget_ratio: float) -> list[SegmentRisk]:
    """상위 `budget_ratio` 비율을 검수 큐에 담는다 (FR-6.3 ①).

    **전체 목록을 반환하고 선별된 것에만 `selected=True`를 붙인다.**
    선별된 것만 반환하면 `review_ratio`에 그대로 넘겼을 때 언제나 1.0이
    나오는데, 그 값이 스펙 §6.2의 "실제 검수 비율"이자 README 배수의
    분모라 조용히 틀리면 프로젝트의 핵심 주장이 무너진다.

    hard fail은 예산과 무관하게 항상 포함된다 (FR-6.2). 따라서 선별
    개수가 `len(risks) * budget_ratio`를 넘을 수 있다 — 이것이 §6.2가
    "요청 예산"과 "실제 검수 비율"을 구분하는 이유다.

    반환 순서는 위험도 내림차순이며 동점은 세그먼트 ID로 깨뜨린다 (NFR-3).
    """
    # `budget_ratio < 0`처럼 비교 연산의 방향에 기대 NaN을 걸러내면, 훗날
    # 리팩터링 한 번에 조용히 깨진다(Task 9에서 `nan < 0`이 False라 같은
    # 유형의 결함이 실제로 났다). `math.isnan`으로 명시적으로 막는다.
    if math.isnan(budget_ratio):
        raise ValueError(f"budget_ratio는 NaN일 수 없다 (받은 값: {budget_ratio})")
    if not 0.0 <= budget_ratio <= 1.0:
        raise ValueError(f"budget_ratio는 0.0~1.0이어야 한다 (받은 값: {budget_ratio})")
    if not risks:
        return []

    ordered = _sorted_desc(risks)
    hard_ids = {r.segment_id for r in ordered if r.hard_fail}
    rest = [r for r in ordered if not r.hard_fail]

    # 올림한다. 10건에 5% 예산이면 0.5건인데, 내림하면 0건이 되어
    # 트리아지가 아무것도 안 하고 통과한다.
    quota = math.ceil(len(risks) * budget_ratio)
    remaining = max(0, quota - len(hard_ids))
    selected_ids = hard_ids | {r.segment_id for r in rest[:remaining]}

    return [_copy(r, selected=r.segment_id in selected_ids) for r in ordered]


def select_by_threshold(risks: Sequence[SegmentRisk], threshold: float) -> list[SegmentRisk]:
    """위험도가 `threshold` 이상인 것을 담는다 (FR-6.3 ②).

    `select_by_budget`과 같은 계약이다 — **전체 목록을 반환하고** 임계값
    이상이거나 hard fail인 항목에만 `selected=True`를 붙인다. hard fail은
    임계값 미만이어도 우회한다 (FR-6.2).
    """
    # select_by_budget과 같은 방어다. NaN을 비교 연산의 우연에 맡기면
    # (`risk_score >= threshold`가 NaN에서 항상 False라 hard fail 외 전량이
    # 조용히 검수에서 빠진다) Task 9가 잡은 결함과 같은 부류가 재현된다.
    if math.isnan(threshold):
        raise ValueError(f"threshold는 NaN일 수 없다 (받은 값: {threshold})")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold는 0.0~1.0이어야 한다 (받은 값: {threshold})")

    ordered = _sorted_desc(risks)
    return [_copy(r, selected=r.hard_fail or r.risk_score >= threshold) for r in ordered]


def review_ratio(risks: Sequence[SegmentRisk]) -> float:
    """실제로 검수 큐에 들어간 비율 (스펙 §6.2).

    **요청 예산이 아니라 이 값으로 배수를 계산한다.** hard fail이 예산을
    우회하므로 둘은 다르고, 요청 예산으로 나누면 배수가 부풀려진다.

    `select_by_budget`/`select_by_threshold`가 반환한 전체 목록을 그대로
    넘기는 것이 정상 사용법이다(둘 다 `selected` 필드를 채운 전체 목록을
    돌려주므로).
    """
    if not risks:
        return 0.0
    return sum(1 for r in risks if r.selected) / len(risks)
