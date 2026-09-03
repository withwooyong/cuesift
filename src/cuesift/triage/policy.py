"""검수 큐 선별 정책 (요구사항정의서 FR-6.3, 스펙 §6.2).

**정책 함수들의 계약:**
- `select_by_budget`·`select_by_threshold`: 전체 목록을 반환하고 입력을 변형하지 않는다
- `select_tier1_candidates`: Tier 1 후보 세그먼트 ID의 **목록을 반환한다** (`list[str]`)

선별된 것만 반환하면 그 결과를 그대로 `review_ratio`에 넘겼을 때 언제나 1.0이 나온다 —
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


def _select_top(risks: Sequence[SegmentRisk], quota: int) -> list[SegmentRisk]:
    """위험도 상위 `quota`개를 선별한 **전체 목록**을 낸다 (FR-6.3 ① · FR-6.2).

    **비율 축과 개수 축이 이 함수 하나를 공유한다.** 두 축이 각자 이 로직을 갖고
    있으면 hard fail 소진 규칙이나 동점 처리가 한쪽에서만 바뀌어 갈라지고,
    그때 `review_ratio()`의 의미가 축마다 달라진다 - 그 값이 README 최상단
    배수의 분모다.

    **hard fail이 quota를 소진한다.** 따라서 위험도가 낮은 hard fail이 그보다
    높은 비-hard 세그먼트를 큐에서 밀어내고, hard fail 개수가 quota를 넘으면
    선별 개수가 quota를 **넘는다**(FR-6.2 - hard fail은 검수 예산을 우회한다).
    가산으로 바꾸면 반대로 `review_ratio`가 요청 예산을 크게 넘어 §9.1 배수의
    분모가 부풀고, hard fail 오탐이 지표를 직접 파괴한다 - 그쪽이 더 나쁘다.
    """
    ordered = _sorted_desc(risks)
    hard_ids = {r.segment_id for r in ordered if r.hard_fail}
    rest = [r for r in ordered if not r.hard_fail]
    remaining = max(0, quota - len(hard_ids))
    selected_ids = hard_ids | {r.segment_id for r in rest[:remaining]}
    return [_copy(r, selected=r.segment_id in selected_ids) for r in ordered]


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

    # 올림한다. 10건에 5% 예산이면 0.5건인데, 내림하면 0건이 되어
    # 트리아지가 아무것도 안 하고 통과한다.
    quota = math.ceil(len(risks) * budget_ratio)
    return _select_top(risks, quota)


def select_by_count(risks: Sequence[SegmentRisk], k: int) -> list[SegmentRisk]:
    """위험도 상위 `k`개를 검수 큐에 담는다 (FR-6.3 ① · 설계 D4·D5·D6·D8).

    `select_by_budget`과 **계약이 같다** - 전체 목록을 반환하고 선별된 것에만
    `selected=True`를 붙이며, 입력을 변형하지 않고, 동점은 세그먼트 ID로
    깨뜨린다(NFR-3). 다른 것은 quota를 환산 없이 `k`로 쓰는 것 하나뿐이다.

    **`k`가 상한이 아니다.** hard fail이 `k`를 넘으면 선별 개수가 `k`를 넘는다
    (FR-6.2 - hard fail은 검수 예산을 우회한다). 자르면 요구사항을 정면으로
    어기고, 실제 개수는 `review_ratio()`와 화면의 "검수 대상 N개"가 말한다.

    **`k = 0`은 "hard fail만 보기"다**(D4). `--review-budget 0`이 이미 그
    뜻이므로 개수 축에서만 0을 거부하면 두 축이 비대칭이 된다.

    **`k`가 세그먼트 수보다 크면 전량이다**(D5). 오류로 만들면 세그먼트 수를
    미리 아는 사람만 이 함수를 쓸 수 있다.
    """
    # **`bool`을 먼저 막는다**(D8). `bool`은 `int`의 서브클래스라
    # `select_by_count(risks, True)`가 아래 `k < 0`을 통과해 조용히 K=1로
    # 동작한다. 이 모듈이 NaN을 비교 연산의 우연에 맡기지 않는 것과 같은
    # 이유다 - 조용히 도는 잘못된 값은 게이트에 걸리지 않는다.
    if isinstance(k, bool):
        raise ValueError(f"k는 bool일 수 없다 (받은 값: {k})")
    if k < 0:
        raise ValueError(f"k는 0 이상이어야 한다 (받은 값: {k})")
    if not risks:
        return []
    return _select_top(risks, k)


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


def gray_zone(risks: Sequence[SegmentRisk]) -> list[SegmentRisk]:
    """컷라인 아래 회색지대 - hard_fail도 아니고 이미 선별되지도 않은 것 (설계 §5).

    **`select_tier1_candidates`와 `tier1.py`의 진단 헬퍼가 이 술어를 따로
    복제해 갖고 있었다** (2라운드 리뷰 C3) - `select_tier1_candidates`가
    제외 조건을 하나 더 넣으면 `tier1.py`의 "회색지대가 비었다" 진단이
    조용히 틀린 원인을 말하게 된다. 정렬은 `_sorted_desc`를 그대로 쓴다 -
    동점을 세그먼트 ID로 깨뜨리는 규칙이 검수 큐와 같아야 NFR-3(재현성)이
    성립한다.
    """
    return [r for r in _sorted_desc(risks) if not r.hard_fail and not r.selected]


def select_tier1_candidates(
    risks: Sequence[SegmentRisk],
    max_ratio: float,
) -> list[str]:
    """Tier 1을 적용할 세그먼트 ID (FR-4.3 · 설계 §5).

    `select_by_budget`이 `selected`를 채운 **전체 목록**을 받는다. 선별분만
    받으면 "컷라인 아래"라는 개념 자체가 성립하지 않는다.

    ## 왜 컷라인 위가 아니라 아래인가

    요구사항정의서 §4의 도식은 "Tier 0 -> 의심 후보 -> Tier 1"이라고 적혀
    있으나, 그 도식은 벤치마크(2026-07-29)보다 먼저 쓰였다. 실측은 Tier 0가
    의미 반전을 큐에서 **밀어낸다**고 말한다 - 예산 10%에서 `negation`
    Recall이 1.41%로 무작위 기준선 9.61%보다 낮다. 위험도 상위를 후보로
    삼으면 Tier 1은 **이미 잡힌 것만 다시 본다.**

    ## 제외 대상

    - `hard_fail`: `fuse()`가 risk_score를 1.0으로 고정하므로 신호를 더해도
      순위가 바뀌지 않는다. 낭비가 아니라 무의미하다
    - `selected`: 이미 검수 큐행이다. 상한을 여기 쓰면 그만큼 회색지대를
      못 본다

    `target_text is None`(번역 실패분) 제외는 **호출자의 일이다** -
    `SegmentRisk`가 텍스트를 갖지 않으므로 여기서 판정할 수 없고, 끌어들이면
    `triage/`가 `segment/` 본문에 결합된다.

    상한은 **할당량이 아니다.** 회색지대가 상한보다 작으면 있는 만큼만 낸다.
    """
    # select_by_budget과 같은 방어다. NaN을 비교 연산의 방향에 맡기면
    # 훗날 리팩터링 한 번에 조용히 깨진다.
    if math.isnan(max_ratio):
        raise ValueError(f"max_ratio는 NaN일 수 없다 (받은 값: {max_ratio})")
    if not 0.0 <= max_ratio <= 1.0:
        raise ValueError(f"max_ratio는 0.0~1.0이어야 한다 (받은 값: {max_ratio})")
    if not risks:
        return []

    # **분모가 후보 집합이 아니라 전체다.** FR-4.3이 "전체 세그먼트 중
    # Tier 1을 적용할 최대 비율"이라고 적혀 있고, 후보 집합을 분모로 삼으면
    # 회색지대가 좁은 트랙에서 상한이 사실상 사라진다.
    #
    # **내림한다** — 올림이면 상한이 상한을 넘는다 (3×0.7 → 실제 100%).
    # 내림이면 n < 1/max_ratio일 때 cap이 0이 되어 Tier 1이 통째로 꺼진다
    # (0.25 비율은 n<4, 0.10은 n<10에서 빈 목록). 이것은 **명시되면 설계**이고,
    # 조용하면 사고다 — 주석으로 비용을 기록해 다음 사람이 설계를 읽을 수 있게.
    cap = math.floor(len(risks) * max_ratio)
    if cap <= 0:
        return []

    return [r.segment_id for r in gray_zone(risks)[:cap]]


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
