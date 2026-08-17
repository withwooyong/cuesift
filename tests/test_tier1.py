"""2라운드 트리아지 (설계 §7)."""

from __future__ import annotations

import pytest
from tests.fakes.provider import EchoProvider

from cuesift.segment import Segment, SegmentRisk
from cuesift.signals.base import SignalContext
from cuesift.spec import load_builtin
from cuesift.tier1 import _diagnose_empty_candidates, triage_with_tier1


@pytest.fixture
def signal_ctx() -> SignalContext:
    return SignalContext(
        profile=load_builtin("en"), glossary=None, source_lang="ko", target_lang="en"
    )


def test_tier1은_후보에만_불린다(signal_ctx):
    """**비용 통제의 핵심 게이트다** (FR-4.3).

    전량에 불리면 요구사항정의서 §4가 '감당 불가'라고 적은 비용이 난다.
    """
    segments = [
        Segment(
            id=str(i),
            index=i,
            start_ms=i * 1000,
            end_ms=(i + 1) * 1000,
            source_text=f"원문{i}",
            target_text=f"Target {i}",
        )
        for i in range(10)
    ]
    # EchoProvider는 요청받은 id를 그대로 채워 정상 JSON을 낸다 - 파싱
    # 실패가 없으므로 재시도가 끼지 않고 호출 횟수를 정확히 셀 수 있다.
    provider = EchoProvider()

    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.2,
        samples=3,
    )

    # 후보 2건(10 × 0.2) × 3회 재번역 = 6회. 전량이면 30회다.
    assert len(provider.calls) == 6


def test_번역_실패분은_후보에서_빠진다(signal_ctx):
    """target_text가 None이면 재번역할 대상이 없다 (설계 §5).

    **실측(2026-08-17): 호출 2회다.** id=1은 `struct.empty`가 빈
    target_text를 hard_fail로 잡아 `select_tier1_candidates`의 hard_fail
    제외에서 애초에 빠진다. `budget_ratio=0.5`에서 quota=ceil(2×0.5)=1을
    id=1의 hard_fail이 전부 소진해(remaining=max(0, 1-1)=0) id=2는 예산에
    들지 못하지만, **선별되지 않은 것과 회색지대 후보 자격은 별개다** -
    id=2는 hard_fail도 아니고 selected도 아니므로 여전히 회색지대다.
    남는 후보는 id=2 하나 = `samples=2`회 호출. 이 시나리오에서
    `triage_with_tier1`의 target_text 필터(설계 §5)는 실제로 아무것도
    더 거르지 않는다 - id=1이 hard_fail 제외에서 이미 빠졌기 때문이다.
    그 필터가 실제로 무언가를 거르는 경로는 현재 등록된 신호로는
    도달하지 않는다(`_diagnose_empty_candidates`의 넷째 분기 참고).
    """
    segments = [
        Segment(id="1", index=0, start_ms=0, end_ms=1000, source_text="원문", target_text=None),
        Segment(
            id="2", index=1, start_ms=1000, end_ms=2000, source_text="원문", target_text="Target"
        ),
    ]
    # EchoProvider는 요청받은 id를 그대로 채워 정상 JSON을 낸다 - 파싱
    # 실패가 없으므로 재시도가 끼지 않고 호출 횟수를 정확히 셀 수 있다.
    provider = EchoProvider()

    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.5,
        provider=provider,
        max_ratio=1.0,
        samples=2,
    )

    assert len(provider.calls) == 2


def test_max_ratio가_0이면_LLM을_안_부른다(signal_ctx):
    """비용을 완전히 끄는 경로가 있어야 한다."""
    segments = [
        Segment(
            id=str(i),
            index=i,
            start_ms=i * 1000,
            end_ms=(i + 1) * 1000,
            source_text=f"원문{i}",
            target_text=f"Target {i}",
        )
        for i in range(10)
    ]
    # EchoProvider는 요청받은 id를 그대로 채워 정상 JSON을 낸다 - 파싱
    # 실패가 없으므로 재시도가 끼지 않고 호출 횟수를 정확히 셀 수 있다.
    provider = EchoProvider()

    risks = triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.0,
        samples=3,
    )

    assert provider.calls == []
    assert len(risks) == 10


def test_전체_목록을_반환한다(signal_ctx):
    """select_by_budget과 같은 계약이다 - 선별된 것만 반환하면
    review_ratio가 언제나 1.0이 되어 §9.1 배수의 분모가 무너진다."""
    segments = [
        Segment(
            id=str(i),
            index=i,
            start_ms=i * 1000,
            end_ms=(i + 1) * 1000,
            source_text=f"원문{i}",
            target_text=f"Target {i}",
        )
        for i in range(10)
    ]
    # EchoProvider는 요청받은 id를 그대로 채워 정상 JSON을 낸다 - 파싱
    # 실패가 없으므로 재시도가 끼지 않고 호출 횟수를 정확히 셀 수 있다.
    provider = EchoProvider()

    risks = triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.2,
        samples=3,
    )

    assert len(risks) == 10
    assert any(r.selected for r in risks)


# --- 후보 0건의 세 가지(+1) 원인 관측 가능성 (컨트롤러 요구 ①) ---
#
# Task 4 리뷰어가 "select_tier1_candidates의 내림(floor) 상한이 조용히
# 0건을 낼 수 있다"를 지적하며 "오케스트레이션이 0건을 관측 가능하게
# 내는 것"을 조건으로 달았다. 아래 세 통합 테스트가 그 조건이 요구하는
# 세 원인을 각각 재현하고, 넷째(불가능하지만 논리적으로 존재하는) 원인은
# `_diagnose_empty_candidates`를 직접 단위 테스트해 별도로 확인한다.


def test_max_ratio가_0이면_사유를_warn한다(signal_ctx):
    """세 원인 중 첫째 - 사용자가 껐다 (정상)."""
    segments = [
        Segment(
            id=str(i),
            index=i,
            start_ms=i * 1000,
            end_ms=(i + 1) * 1000,
            source_text=f"원문{i}",
            target_text=f"Target {i}",
        )
        for i in range(10)
    ]
    provider = EchoProvider()
    messages: list[str] = []

    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.0,
        samples=3,
        warn=messages.append,
    )

    assert len(messages) == 1
    assert "껐다" in messages[0]


def test_세그먼트_수가_적어_상한이_0이면_사유를_warn한다(signal_ctx):
    """세 원인 중 둘째 - `select_tier1_candidates`의 내림 상한이 0이 됐다.

    n=3, max_ratio=0.2 -> cap=floor(0.6)=0. 회색지대 자체는 비지 않는다
    (budget_ratio=0.1이 1건만 선별하므로 나머지 2건이 회색지대에 남는다) -
    "회색지대가 빔"과 구분되는 것을 확인하는 것이 이 테스트의 요점이다.
    """
    segments = [
        Segment(
            id=str(i),
            index=i,
            start_ms=i * 1000,
            end_ms=(i + 1) * 1000,
            source_text=f"원문{i}",
            target_text=f"Target {i}",
        )
        for i in range(3)
    ]
    provider = EchoProvider()
    messages: list[str] = []

    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=0.1,
        provider=provider,
        max_ratio=0.2,
        samples=3,
        warn=messages.append,
    )

    assert provider.calls == []
    assert len(messages) == 1
    assert "상한" in messages[0]


def test_회색지대가_비면_사유를_warn한다(signal_ctx):
    """세 원인 중 셋째 - 전부 hard_fail이거나 이미 선별돼 회색지대가 빈다.

    id=1은 target_text가 없어 hard_fail이고, budget_ratio=1.0(전량 예산)이
    id=2까지 선별한다 - 남는 회색지대가 없다.
    """
    segments = [
        Segment(id="1", index=0, start_ms=0, end_ms=1000, source_text="원문", target_text=None),
        Segment(
            id="2", index=1, start_ms=1000, end_ms=2000, source_text="원문", target_text="Target"
        ),
    ]
    provider = EchoProvider()
    messages: list[str] = []

    triage_with_tier1(
        segments,
        signal_ctx,
        budget_ratio=1.0,
        provider=provider,
        max_ratio=1.0,
        samples=2,
        warn=messages.append,
    )

    assert provider.calls == []
    assert len(messages) == 1
    assert "회색지대" in messages[0]


def test_diagnose_empty_candidates가_네_사유를_구분한다():
    """`_diagnose_empty_candidates`를 직접 단위 테스트한다.

    넷째 원인(후보로 뽑혔지만 전부 번역 실패분)은 현재 등록된 신호로는
    통합 테스트로 재현할 수 없다 - `struct.empty`가 빈 target_text를 항상
    hard_fail로 잡아 회색지대에 들어오기 전에 걸러지기 때문이다(함수
    독스트링 참고). 순수 함수라 합성 `SegmentRisk`로 각 분기를 직접
    겨냥할 수 있다.
    """
    hard = SegmentRisk(segment_id="h", signals=[], risk_score=1.0, hard_fail=True, selected=True)
    picked = SegmentRisk(segment_id="p", signals=[], risk_score=0.9, hard_fail=False, selected=True)
    gray = SegmentRisk(segment_id="g", signals=[], risk_score=0.1, hard_fail=False, selected=False)

    # ① max_ratio=0.0 - candidate_ids·scored 내용과 무관하게 최우선이다.
    assert "껐다" in _diagnose_empty_candidates([gray], set(), 0.0)

    # ② candidate_ids가 비지 않았는데 후보가 0건 -> target_text 필터가
    # 전부 걸렀다.
    assert "번역 실패분" in _diagnose_empty_candidates([gray], {"g"}, 0.2)

    # ③ candidate_ids가 비었고 회색지대(비-hard_fail·비-selected)가 남아
    # 있다 -> 상한이 내림으로 0이 됐다.
    assert "상한" in _diagnose_empty_candidates([hard, gray], set(), 0.01)

    # ④ candidate_ids가 비었고 회색지대도 비었다(전부 hard_fail 또는 selected).
    assert "회색지대" in _diagnose_empty_candidates([hard, picked], set(), 0.5)
