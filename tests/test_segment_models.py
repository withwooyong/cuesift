"""세그먼트 데이터 모델 테스트 (요구사항정의서 §7.3)."""

import pytest

from cuesift.segment import Segment, SegmentRisk, Signal, Span


def test_segment_duration_is_derived_from_timecodes():
    seg = Segment(id="s1", index=0, start_ms=1000, end_ms=3500, source_text="안녕하세요")
    assert seg.duration_ms == 2500


def test_segment_rejects_reversed_timecodes():
    """end < start는 파싱 버그의 신호다. 조용히 음수 duration을 만들면
    CPS가 음수가 되어 규격 검사가 무의미해진다."""
    with pytest.raises(ValueError, match="end_ms"):
        Segment(id="s1", index=0, start_ms=3000, end_ms=1000, source_text="x")


def test_segment_target_text_defaults_to_none():
    seg = Segment(id="s1", index=0, start_ms=0, end_ms=1000, source_text="원문")
    assert seg.target_text is None
    assert seg.speaker is None
    assert seg.meta == {}


def test_signal_score_must_be_normalized():
    """FR-6.1은 신호를 0~1로 정규화한다고 규정한다. 범위를 벗어난 값이
    들어오면 가중합이 조용히 왜곡되므로 생성 시점에 막는다."""
    with pytest.raises(ValueError, match="score"):
        Signal(name="spec.cps", tier=0, score=1.5)


def test_signal_defaults():
    sig = Signal(name="spec.cps", tier=0, score=0.4)
    assert sig.hard_fail is False
    assert sig.spans == ()
    assert sig.detail == {}


def test_span_rejects_reversed_range():
    with pytest.raises(ValueError, match="end"):
        Span(start=5, end=2)


@pytest.mark.parametrize("bad", ["both", "TARGET", "", None, 0])
def test_span_rejects_unknown_side(bad):
    """`Literal`은 타입 힌트일 뿐 런타임에 아무것도 막지 않는다.

    `side`가 오타나 `None`이면 FR-7.3 리포트가 하이라이트할 쪽을 잃는데,
    검증이 없으면 그 사실이 리포트 생성 시점에야 드러난다 — 신호를 만든
    코드에서 멀리 떨어진 곳이다. 같은 클래스의 `start`/`end`는 이미
    `__post_init__`에서 막고 있으므로 여기만 무방비로 두면 방어가 반쪽이다.
    """
    with pytest.raises(ValueError, match="side"):
        Span(start=0, end=3, side=bad)


def test_span_defaults_to_target_side():
    """대부분의 신호가 번역문을 가리키므로 그것이 기본값이다."""
    assert Span(start=0, end=3).side == "target"


def test_span_can_point_at_the_source():
    """용어 누락·숫자 누락은 번역문에 없으므로 원문을 가리킨다."""
    assert Span(start=0, end=3, side="source").side == "source"


def test_segment_risk_holds_signals_and_reasons():
    sig = Signal(name="struct.empty", tier=0, score=1.0, hard_fail=True)
    risk = SegmentRisk(segment_id="s1", signals=[sig], risk_score=1.0, hard_fail=True)
    assert risk.selected is False
    assert risk.reasons == []


@pytest.mark.parametrize("bad", [-5.0, 1.5, float("nan")])
def test_segment_risk_rejects_out_of_range_score(bad):
    """형제 세 모델과 같은 방어다. 범위 밖 값은 triage의 정렬을 깨뜨린다."""
    with pytest.raises(ValueError, match="risk_score"):
        SegmentRisk(segment_id="s1", signals=[], risk_score=bad, hard_fail=False)


def test_segment_risk_allows_hard_fail_with_any_score():
    """FR-6.2는 hard fail이 가중합을 '우회'한다는 의미 계약이지
    '항상 1.0'이 아니다. triage의 우회 보장을 risk의 구현 선택에 묶지 않는다."""
    r = SegmentRisk(segment_id="s1", signals=[], risk_score=0.1, hard_fail=True)
    assert r.hard_fail is True
