"""트리아지 선별 테스트 (요구사항정의서 FR-6.3, 스펙 §6.2)."""

import pytest

from cuesift.segment import SegmentRisk
from cuesift.triage import review_ratio, select_by_budget, select_by_threshold


def _risk(sid: str, score: float, hard: bool = False) -> SegmentRisk:
    return SegmentRisk(segment_id=sid, signals=[], risk_score=score, hard_fail=hard)


@pytest.fixture
def ten():
    """위험도 0.0, 0.1, ..., 0.9인 세그먼트 10개."""
    return [_risk(f"s{i}", i / 10) for i in range(10)]


def test_budget_selects_the_top_slice(ten):
    selected = select_by_budget(ten, 0.2)
    assert {r.segment_id for r in selected} == {"s9", "s8"}


def test_budget_marks_selected_flag(ten):
    selected = select_by_budget(ten, 0.2)
    assert all(r.selected for r in selected)


def test_budget_does_not_mutate_the_input(ten):
    """스펙 §6.1의 예산 스윕은 같은 목록에 여러 예산을 적용한다.
    입력을 변형하면 두 번째 예산부터 결과가 오염된다."""
    select_by_budget(ten, 0.5)
    assert all(r.selected is False for r in ten)


def test_budget_rounds_up_so_a_small_budget_is_not_empty(ten):
    """10건에 5% 예산이면 0.5건이다. 내림하면 0건이 되어 트리아지가
    아무것도 안 하고 통과한다."""
    assert len(select_by_budget(ten, 0.05)) == 1


def test_hard_fail_bypasses_the_budget():
    """FR-6.2 — 예산 1%여도 hard fail은 전부 들어간다."""
    risks = [_risk(f"s{i}", 0.0) for i in range(10)]
    risks[3] = _risk("s3", 1.0, hard=True)
    risks[7] = _risk("s7", 1.0, hard=True)
    selected = select_by_budget(risks, 0.01)
    assert {"s3", "s7"} <= {r.segment_id for r in selected}


def test_review_ratio_reports_what_was_actually_spent():
    """스펙 §6.2 — 요청 예산이 아니라 실제 비율로 배수를 계산해야 한다.
    hard fail이 예산을 우회하므로 둘은 다르다."""
    risks = [_risk(f"s{i}", 0.0) for i in range(10)]
    risks[3] = _risk("s3", 1.0, hard=True)
    risks[7] = _risk("s7", 1.0, hard=True)
    selected = select_by_budget(risks, 0.01)
    assert review_ratio(selected + [r for r in risks if r.segment_id not in {"s3", "s7"}]) > 0.01


def test_budget_of_zero_still_includes_hard_fails():
    risks = [_risk("a", 0.0), _risk("b", 1.0, hard=True)]
    assert {r.segment_id for r in select_by_budget(risks, 0.0)} == {"b"}


def test_full_budget_selects_everything(ten):
    assert len(select_by_budget(ten, 1.0)) == 10


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
    selected = select_by_threshold(ten, 0.7)
    assert {r.segment_id for r in selected} == {"s7", "s8", "s9"}


def test_threshold_includes_hard_fail_below_threshold():
    """hard fail은 임계값 정책에서도 우회한다(FR-6.2)."""
    risks = [_risk("a", 0.1, hard=True), _risk("b", 0.2)]
    assert {r.segment_id for r in select_by_threshold(risks, 0.9)} == {"a"}


def test_review_ratio_counts_selected_over_total():
    risks = [_risk("a", 0.0), _risk("b", 0.0), _risk("c", 0.0), _risk("d", 0.0)]
    risks[0].selected = True
    assert review_ratio(risks) == 0.25
