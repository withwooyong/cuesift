"""트리아지 선별 테스트 (요구사항정의서 FR-6.3, 스펙 §6.2)."""

import pytest

from cuesift.segment import SegmentRisk
from cuesift.triage import (
    review_ratio,
    select_by_budget,
    select_by_threshold,
    select_tier1_candidates,
)


def _risk(sid: str, score: float, hard: bool = False) -> SegmentRisk:
    return SegmentRisk(segment_id=sid, signals=[], risk_score=score, hard_fail=hard)


def _t1_risk(seg_id: str, score: float, *, hard_fail: bool = False, selected: bool = False):
    return SegmentRisk(
        segment_id=seg_id,
        signals=[],
        risk_score=score,
        hard_fail=hard_fail,
        selected=selected,
    )


@pytest.fixture
def ten():
    """위험도 0.0, 0.1, ..., 0.9인 세그먼트 10개."""
    return [_risk(f"s{i}", i / 10) for i in range(10)]


def test_budget_selects_the_top_slice(ten):
    result = select_by_budget(ten, 0.2)
    assert {r.segment_id for r in result if r.selected} == {"s9", "s8"}


def test_budget_marks_selected_flag(ten):
    """select_by_budget은 전체 목록을 반환한다 — 선별된 것은 selected=True,
    나머지는 selected=False다."""
    result = select_by_budget(ten, 0.2)
    selected_ids = {"s9", "s8"}
    for r in result:
        assert r.selected == (r.segment_id in selected_ids)


def test_budget_does_not_mutate_the_input(ten):
    """스펙 §6.1의 예산 스윕은 같은 목록에 여러 예산을 적용한다.
    입력을 변형하면 두 번째 예산부터 결과가 오염된다."""
    select_by_budget(ten, 0.5)
    assert all(r.selected is False for r in ten)


def test_budget_rounds_up_so_a_small_budget_is_not_empty(ten):
    """10건에 5% 예산이면 0.5건이다. 내림하면 0건이 되어 트리아지가
    아무것도 안 하고 통과한다."""
    result = select_by_budget(ten, 0.05)
    assert len(result) == 10  # 전체 목록이 반환된다
    assert sum(1 for r in result if r.selected) == 1


def test_hard_fail_bypasses_the_budget():
    """FR-6.2 — 예산 1%여도 hard fail은 전부 들어간다."""
    risks = [_risk(f"s{i}", 0.0) for i in range(10)]
    risks[3] = _risk("s3", 1.0, hard=True)
    risks[7] = _risk("s7", 1.0, hard=True)
    result = select_by_budget(risks, 0.01)
    assert {"s3", "s7"} <= {r.segment_id for r in result if r.selected}


def test_review_ratio_reports_what_was_actually_spent():
    """스펙 §6.2 — 요청 예산이 아니라 실제 비율로 배수를 계산해야 한다.
    hard fail이 예산을 우회하므로 둘은 다르다."""
    risks = [_risk(f"s{i}", 0.0) for i in range(10)]
    risks[3] = _risk("s3", 1.0, hard=True)
    risks[7] = _risk("s7", 1.0, hard=True)
    assert review_ratio(select_by_budget(risks, 0.01)) > 0.01


def test_budget_of_zero_still_includes_hard_fails():
    risks = [_risk("a", 0.0), _risk("b", 1.0, hard=True)]
    result = select_by_budget(risks, 0.0)
    assert {r.segment_id for r in result if r.selected} == {"b"}


def test_full_budget_selects_everything(ten):
    result = select_by_budget(ten, 1.0)
    assert len(result) == 10
    assert all(r.selected for r in result)


def test_budget_outside_zero_to_one_is_rejected(ten):
    with pytest.raises(ValueError, match="budget_ratio"):
        select_by_budget(ten, 1.5)


def test_empty_input_returns_empty(ten):
    assert select_by_budget([], 0.5) == []
    assert review_ratio([]) == 0.0


def test_ties_are_broken_deterministically():
    """NFR-3 재현성 — 같은 입력이 같은 결과를 내야 한다. 동점에서
    순서가 흔들리면 벤치마크 숫자가 실행마다 달라진다."""
    risks = [_risk("b", 0.5), _risk("a", 0.5), _risk("c", 0.5)]
    first = [r.segment_id for r in select_by_budget(risks, 0.34)]
    second = [r.segment_id for r in select_by_budget(list(reversed(risks)), 0.34)]
    assert first == second


def test_threshold_selects_at_or_above(ten):
    result = select_by_threshold(ten, 0.7)
    assert {r.segment_id for r in result if r.selected} == {"s7", "s8", "s9"}


def test_threshold_includes_hard_fail_below_threshold():
    """hard fail은 임계값 정책에서도 우회한다(FR-6.2)."""
    risks = [_risk("a", 0.1, hard=True), _risk("b", 0.2)]
    result = select_by_threshold(risks, 0.9)
    assert {r.segment_id for r in result if r.selected} == {"a"}


@pytest.mark.parametrize("bad", [float("nan"), -1.0, 5.0])
def test_threshold_outside_zero_to_one_is_rejected(bad):
    """형제 함수 select_by_budget과 같은 방어를 갖는다.

    threshold=nan은 hard fail 외 전량을 조용히 검수에서 뺀다 —
    Task 9가 잡은 `nan < 0 == False`와 같은 부류다.
    """
    with pytest.raises(ValueError, match="threshold"):
        select_by_threshold([_risk("a", 0.5)], bad)


def test_review_ratio_counts_selected_over_total():
    risks = [_risk("a", 0.0), _risk("b", 0.0), _risk("c", 0.0), _risk("d", 0.0)]
    risks[0].selected = True
    assert review_ratio(risks) == 0.25


def test_review_ratio_works_on_the_selection_result_directly():
    """이것이 이 API의 정상 사용법이다.

    선별된 것만 반환하던 이전 계약에서는 이 호출이 항상 1.0을 냈다 —
    그 값이 스펙 §6.2의 실제 검수 비율이자 README 배수의 분모다.
    """
    risks = [_risk(f"s{i}", i / 100) for i in range(100)]
    assert review_ratio(select_by_budget(risks, 0.10)) == 0.10


def test_hard_fail_entries_are_ordered_deterministically():
    """hard fail 구간도 입력 순서에 의존하면 안 된다 (NFR-3)."""
    risks = [_risk("z", 1.0, hard=True), _risk("a", 1.0, hard=True), _risk("m", 1.0, hard=True)]
    forward = [r.segment_id for r in select_by_budget(risks, 1.0)]
    backward = [r.segment_id for r in select_by_budget(list(reversed(risks)), 1.0)]
    assert forward == backward


def test_selection_does_not_alias_mutable_fields():
    """사본의 reasons에 append해도 원본이 바뀌면 안 된다.

    예산 스윕(§6.1)은 같은 원본에 여러 예산을 차례로 적용한다.
    """
    original = SegmentRisk(
        segment_id="x", signals=[], risk_score=0.9, hard_fail=False, reasons=["a"]
    )
    copy = select_by_budget([original], 1.0)[0]
    copy.reasons.append("침입")
    assert original.reasons == ["a"]
    assert copy.signals is not original.signals


def test_nan_budget_is_rejected_explicitly():
    """NaN 방어가 비교 연산의 우연이 아니라 명시적이어야 한다."""
    with pytest.raises(ValueError, match="budget_ratio"):
        select_by_budget([_risk("a", 0.5)], float("nan"))


def test_hard_fail은_후보에서_빠진다():
    """risk_score가 1.0으로 고정돼 신호를 더해도 순위가 안 바뀐다 -
    낭비가 아니라 **무의미**하다 (설계 §5)."""
    risks = [
        _t1_risk("a", 1.0, hard_fail=True),
        _t1_risk("b", 0.4),
        _t1_risk("c", 0.3),
        _t1_risk("d", 0.2),
    ]
    assert "a" not in select_tier1_candidates(risks, 1.0)


def test_이미_선별된_것은_후보에서_빠진다():
    """예산을 여기 쓰면 그만큼 회색지대를 못 본다 (설계 §5)."""
    risks = [_t1_risk("a", 0.9, selected=True), _t1_risk("b", 0.4), _t1_risk("c", 0.3)]
    assert select_tier1_candidates(risks, 1.0) == ["b", "c"]


def test_상한의_분모는_전체다():
    """FR-4.3이 '전체 세그먼트 중 최대 비율'이라고 적혀 있다. 후보 집합을
    분모로 삼으면 회색지대가 좁은 트랙에서 상한이 사실상 사라진다."""
    risks = [_t1_risk("a", 0.9, selected=True)] + [
        _t1_risk(str(i), 0.5 - i * 0.01) for i in range(9)
    ]
    # 전체 10건(selected 1건 포함) × 0.2 = 2건. 분모가 회색지대 9건이 아니다.
    assert len(select_tier1_candidates(risks, 0.2)) == 2


def test_회색지대가_상한보다_작으면_있는_만큼만():
    """상한이지 할당량이 아니다 (설계 §5)."""
    risks = [_t1_risk("a", 0.9, selected=True), _t1_risk("b", 0.4)] + [
        _t1_risk(f"h{i}", 1.0, hard_fail=True) for i in range(8)
    ]
    assert select_tier1_candidates(risks, 1.0) == ["b"]


def test_위험도_내림차순으로_고른다():
    risks = [_t1_risk("low", 0.1), _t1_risk("high", 0.8), _t1_risk("mid", 0.5)]
    assert select_tier1_candidates(risks, 0.7) == ["high", "mid"]


def test_동점은_세그먼트_ID로_깨뜨린다():
    """NFR-3 - 순서가 흔들리면 같은 입력에 같은 LLM 호출이 나가지 않는다."""
    risks = [_t1_risk("b", 0.5), _t1_risk("a", 0.5), _t1_risk("c", 0.5)]
    assert select_tier1_candidates(risks, 0.7) == ["a", "b"]


def test_빈_입력은_빈_목록():
    assert select_tier1_candidates([], 0.5) == []


def test_상한이_0이면_아무도_안_고른다():
    risks = [_t1_risk("a", 0.5), _t1_risk("b", 0.4)]
    assert select_tier1_candidates(risks, 0.0) == []


@pytest.mark.parametrize("bad", [-0.1, 1.1, float("nan")])
def test_잘못된_상한을_거부한다(bad):
    """select_by_budget과 같은 방어다. NaN을 비교 연산의 우연에 맡기면
    리팩터링 한 번에 조용히 깨진다."""
    with pytest.raises(ValueError, match="max_ratio"):
        select_tier1_candidates([_t1_risk("a", 0.5)], bad)


@pytest.mark.parametrize("n,ratio", [(1, 0.5), (3, 0.7), (10, 0.2), (7, 0.3), (4, 0.25)])
def test_상한은_절대_초과하지_않는다(n, ratio):
    """FR-4.3 보증: Tier 1 후보 비율이 max_ratio를 절대 초과하지 않는다.

    이 테스트가 없으면 ceil로 되돌릴 때 정렬 버그로 오독할 수 있다."""
    risks = [_t1_risk(str(i), 0.9 - i * 0.01) for i in range(n)]
    result = select_tier1_candidates(risks, ratio)
    # selected 필드가 없으므로 비율은 단순히 len(result) / len(risks)
    actual_ratio = len(result) / len(risks) if risks else 0.0
    assert actual_ratio <= ratio + 1e-9  # 부동소수 오차 허용
