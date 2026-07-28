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


def test_segment_risk_holds_signals_and_reasons():
    sig = Signal(name="struct.empty", tier=0, score=1.0, hard_fail=True)
    risk = SegmentRisk(segment_id="s1", signals=[sig], risk_score=1.0, hard_fail=True)
    assert risk.selected is False
    assert risk.reasons == []
