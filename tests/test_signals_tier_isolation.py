"""Tier 0/1 실행 경로 분리 (설계 §4.1 · 요구사항정의서 §4)."""

from __future__ import annotations

import pytest
from tests.fakes.provider import ScriptedProvider

from cuesift.segment import Segment, Signal
from cuesift.signals.base import (
    SignalContext,
    Tier1Context,
    collect_all,
    collect_tier1,
    register,
    registry,
)
from cuesift.spec import load_builtin


class _SpyTier1:
    """어느 경로로 불렸는지 세는 tier 1 수집기."""

    name = "test.tier1_spy"
    tier = 1

    def __init__(self) -> None:
        self.tier0_calls = 0
        self.tier1_calls = 0

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        """**일부러 구현해 둔다.**

        없으면 `collect_all`이 tier를 안 볼 때 `AttributeError`로 죽는데,
        그러면 "누가 불렀는가"가 "왜 죽었는가"에 가려진다. 세는 편이
        변이의 정체를 정확히 드러낸다.
        """
        self.tier0_calls += 1
        return None

    def collect_tier1(self, seg: Segment, ctx: Tier1Context) -> Signal | None:
        self.tier1_calls += 1
        # 실제 수집기와 같은 자리에서 프로바이더를 만진다.
        ctx.provider_for(0).complete([], temperature=1.0, max_tokens=None)
        return Signal(name=self.name, tier=1, score=0.5)


@pytest.fixture
def spy_registered():
    """레지스트리를 저장·복원한다. 전역이라 오염되면 다른 테스트가 깨진다."""
    saved = dict(registry())
    collector = _SpyTier1()
    register(collector)
    yield collector
    registry().clear()
    registry().update(saved)


@pytest.fixture
def signal_ctx() -> SignalContext:
    # 기존 신호 테스트와 같은 방식이다 (tests/test_signals_derived.py).
    # conftest.py에는 SpecProfile fixture가 없다 - 각 파일이 load_builtin을
    # 직접 부른다.
    return SignalContext(
        profile=load_builtin("en"), glossary=None, source_lang="ko", target_lang="en"
    )


def _segments() -> list[Segment]:
    return [
        Segment(id="1", index=0, start_ms=0, end_ms=1000, source_text="안녕", target_text="Hi"),
        Segment(id="2", index=1, start_ms=1000, end_ms=2000, source_text="잘가", target_text="Bye"),
    ]


def test_collect_all은_tier1을_부르지_않는다(spy_registered, signal_ctx):
    """**이 작업의 최우선 게이트다.**

    Tier 1이 collect_all에서 실행되면 전량 LLM 호출이 일어난다 -
    요구사항정의서 §4가 "16부작 × 20개 언어에서 3배는 감당 불가"라고
    적은 바로 그 사고다.
    """
    collect_all(_segments(), signal_ctx)
    assert spy_registered.tier0_calls == 0
    assert spy_registered.tier1_calls == 0


def test_collect_all은_enabled에_tier1을_넣으면_거부한다(spy_registered, signal_ctx):
    """조용히 건너뛰지 않는다. 말없이 빠지면 ablation에서 '기여도 0'으로 읽힌다."""
    with pytest.raises(ValueError, match="tier 0만"):
        collect_all(_segments(), signal_ctx, enabled=["test.tier1_spy"])


def test_collect_tier1은_tier1만_부른다(spy_registered, signal_ctx):
    provider = ScriptedProvider(["a", "b"])
    t1 = Tier1Context(
        signal=signal_ctx,
        provider_for=lambda attempt: provider,
        samples=3,
        temperature=1.0,
    )
    result = collect_tier1(_segments()[:1], t1)
    assert len(provider.calls) == 1
    assert [s.name for s in result["1"]] == ["test.tier1_spy"]


def test_collect_tier1은_넘긴_세그먼트에만_돈다(spy_registered, signal_ctx):
    """상한은 select_tier1_candidates의 일이다. 여기서 또 자르지 않는다."""
    provider = ScriptedProvider(["a"])
    t1 = Tier1Context(
        signal=signal_ctx, provider_for=lambda attempt: provider, samples=3, temperature=1.0
    )
    result = collect_tier1(_segments()[:1], t1)
    assert set(result) == {"1"}


def test_temperature가_0이면_거부한다(signal_ctx):
    """0이면 재번역이 전부 동일해 점수가 항상 0.0이 된다 - 신호가 죽었는데
    '안전'으로 보고된다(Q3 무음 열화 금지)."""
    with pytest.raises(ValueError, match="temperature"):
        Tier1Context(
            signal=signal_ctx, provider_for=lambda attempt: None, samples=3, temperature=0.0
        )


def test_samples가_2_미만이면_거부한다(signal_ctx):
    with pytest.raises(ValueError, match="samples"):
        Tier1Context(
            signal=signal_ctx, provider_for=lambda attempt: None, samples=1, temperature=1.0
        )
