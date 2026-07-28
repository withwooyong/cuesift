"""신호 수집기 인터페이스와 레지스트리 테스트 (요구사항정의서 FR-6.5)."""

import pytest

from cuesift.segment import Segment, Signal
from cuesift.signals import SignalContext, collect_all, register, registry
from cuesift.spec import load_builtin


@pytest.fixture
def ctx():
    return SignalContext(
        profile=load_builtin("en"), glossary=None, source_lang="ko", target_lang="en"
    )


@pytest.fixture
def segments():
    return [
        Segment(id="s1", index=0, start_ms=0, end_ms=2000, source_text="가", target_text="a"),
        Segment(id="s2", index=1, start_ms=2000, end_ms=4000, source_text="나", target_text="b"),
    ]


@pytest.fixture(autouse=True)
def clean_registry():
    """레지스트리는 전역 상태다. **비운 뒤** 테스트를 돌리고 복원한다.

    비우지 않으면 Task 7·8이 등록한 실제 신호 8종이 함께 돌아가고,
    이 파일의 단언(`== ["test.always"]`)이 실제 신호의 발화 여부에
    의존하게 된다. 인터페이스 테스트가 신호 구현에 묶이면 안 된다.
    """
    saved = dict(registry())
    registry().clear()
    yield
    registry().clear()
    registry().update(saved)


class _AlwaysFires:
    name = "test.always"
    tier = 0

    def collect(self, seg, ctx):
        return Signal(name=self.name, tier=0, score=1.0)


class _NeverFires:
    name = "test.never"
    tier = 0

    def collect(self, seg, ctx):
        return None


class _Batch:
    name = "test.batch"
    tier = 0

    def collect_batch(self, segments, ctx):
        return {segments[0].id: Signal(name=self.name, tier=0, score=0.5)}


def test_register_makes_collector_discoverable():
    register(_AlwaysFires())
    assert "test.always" in registry()


def test_duplicate_name_is_rejected():
    """이름이 겹치면 나중 것이 앞선 것을 조용히 덮어써 신호가 사라진다."""
    register(_AlwaysFires())
    with pytest.raises(ValueError, match="test.always"):
        register(_AlwaysFires())


def test_collect_all_returns_signals_per_segment(ctx, segments):
    register(_AlwaysFires())
    result = collect_all(segments, ctx)
    assert [s.name for s in result["s1"]] == ["test.always"]
    assert [s.name for s in result["s2"]] == ["test.always"]


def test_none_result_means_no_signal(ctx, segments):
    """수집기가 None을 내면 '점수 0'이 아니라 '해당 없음'이다.
    0점 신호를 넣으면 §8.4 review.json이 무의미한 항목으로 채워진다."""
    register(_NeverFires())
    result = collect_all(segments, ctx)
    assert result["s1"] == []


def test_every_segment_appears_even_with_no_signals(ctx, segments):
    """빠진 키는 KeyError를 부른다. 신호가 없어도 빈 리스트를 준다."""
    result = collect_all(segments, ctx)
    assert set(result) == {"s1", "s2"}


def test_batch_collector_runs_once_over_the_track(ctx, segments):
    register(_Batch())
    result = collect_all(segments, ctx)
    assert [s.name for s in result["s1"]] == ["test.batch"]
    assert result["s2"] == []


def test_enabled_filter_selects_a_subset(ctx, segments):
    """ablation 측정(스펙 §6.1 신호별 기여도)이 이 인자를 쓴다."""
    register(_AlwaysFires())
    register(_Batch())
    result = collect_all(segments, ctx, enabled={"test.batch"})
    assert [s.name for s in result["s1"]] == ["test.batch"]


def test_enabled_with_unknown_name_raises():
    """오타로 신호를 껐는데 '기여도 0'으로 읽히면 잘못된 결론이 나온다."""
    with pytest.raises(ValueError, match="test.nope"):
        collect_all([], SignalContext(load_builtin("en"), None, "ko", "en"), enabled={"test.nope"})
