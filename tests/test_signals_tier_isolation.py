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


class _SpyTier2:
    """v0.2 QE 모델(tier 2, 설계 §4.1 D6)의 자리를 흉내 낸다.

    tier 1 스파이 하나만으로는 `collect_all`의 필터가 `c.tier == 0`에서
    `c.tier != 1`로, `collect_tier1`의 필터가 `c.tier == 1`에서
    `c.tier != 0`으로 느슨해지는 변이를 잡지 못한다 - tier 1 객체는 두
    변이 모두에서 여전히 올바르게 걸러지기 때문이다(`!= 1`은 tier=1을
    배제하고, `!= 0`은 tier=1을 포함하도록 놔둔다). tier 2 값이 있어야
    "== 대신 != 조건이 새어 들어간다"는 실수가 보인다.
    """

    name = "test.tier2_spy"
    tier = 2

    def __init__(self) -> None:
        self.tier0_calls = 0
        self.tier1_calls = 0

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        self.tier0_calls += 1
        return None

    def collect_tier1(self, seg: Segment, ctx: Tier1Context) -> Signal | None:
        self.tier1_calls += 1
        return None


@pytest.fixture
def spy_registered():
    """레지스트리를 저장·복원한다. 전역이라 오염되면 다른 테스트가 깨진다.

    **등록 전에 기존 tier 1 수집기를 걷어낸다.** `llm.self_consistency`
    (Task 5)가 전역·영구 등록되면서 `collect_tier1(enabled=None)`이
    이 스파이만이 아니라 그것까지 함께 돌리게 됐다 - 이 파일의 세그먼트가
    실제 `target_text`를 갖고 있어 `_retranslate`까지 들어가 버려,
    `ScriptedProvider`의 짧은 대본이 소진되거나 `result`에 이름이
    섞여 단언이 깨진다. tier 0은 남긴다 - `collect_all`을 부르는 테스트와
    `struct.untranslated` 같은 실제 tier 0 이름을 쓰는 테스트가 있다.
    """
    saved = dict(registry())
    for name, existing in list(registry().items()):
        if existing.tier == 1:
            del registry()[name]
    collector = _SpyTier1()
    register(collector)
    yield collector
    registry().clear()
    registry().update(saved)


@pytest.fixture
def tier2_spy_registered():
    """tier 2 스파이 하나만 등록한다.

    `spy_registered`(tier 1)와 합치지 않는 이유는, 두 스파이가 같은
    테스트에 섞이면 실패 시 "어느 tier가 새었는가"가 바로 드러나지
    않기 때문이다.

    `spy_registered`와 같은 이유로 등록 전에 기존 tier 1 수집기를
    걷어낸다 - `test_collect_all과_collect_tier1_모두_tier2를_부르지_않는다`가
    `collect_tier1(enabled=None)`을 불러 `llm.self_consistency`까지
    함께 돌 뻔했다.
    """
    saved = dict(registry())
    for name, existing in list(registry().items()):
        if existing.tier == 1:
            del registry()[name]
    collector = _SpyTier2()
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
    """상한은 select_tier1_candidates의 일이다. 여기서 또 자르지 않는다.

    **세그먼트 1건으로는 이 계약을 단언할 수 없다.** "전부 돈다"와 "첫
    1건만 돈다"가 구분되지 않는다 - `result`의 키는 딕셔너리 컴프리헨션이
    `segments`에서 직접 만들므로 수집기 호출 횟수와 무관하게 항상 참이
    된다(리뷰 실측: `for seg in segments:`를 `for seg in segments[:1]:`로
    바꿔도 이 단언만으로는 6 passed가 유지됐다). 그래서 세그먼트 2건을
    넘기고 호출 횟수까지 함께 잰다.
    """
    provider = ScriptedProvider(["a", "b"])
    t1 = Tier1Context(
        signal=signal_ctx, provider_for=lambda attempt: provider, samples=3, temperature=1.0
    )
    segs = _segments()
    result = collect_tier1(segs, t1)
    assert set(result) == {"1", "2"}
    assert spy_registered.tier1_calls == len(segs)
    assert len(provider.calls) == len(segs)


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


def test_samples가_float이면_거부한다(signal_ctx):
    """`samples=2.7`은 하한(`< 2`) 검사를 그냥 통과한다 - `2.7 < 2`가
    거짓이기 때문이다. 여기서 막지 않으면 Task 5의 `range(ctx.samples)`가
    `TypeError`로 터진다. 생성 시점 검증의 취지가 "나중에 안 터지게"인데
    이 경로만 비켜 가는 것을 막는다."""
    with pytest.raises(ValueError, match="samples"):
        Tier1Context(
            signal=signal_ctx, provider_for=lambda attempt: None, samples=2.7, temperature=1.0
        )


def test_collect_all과_collect_tier1_모두_tier2를_부르지_않는다(tier2_spy_registered, signal_ctx):
    """향후 QE 모델(tier 2)이 등록돼도 두 함수 중 어느 쪽도 그것을 부르면
    안 된다. `_SpyTier2` 독스트링이 설명하듯, tier 1 스파이만으로는 필터
    조건이 `==`에서 `!=`로 느슨해지는 변이를 잡지 못한다."""
    collect_all(_segments(), signal_ctx)
    assert tier2_spy_registered.tier0_calls == 0

    provider = ScriptedProvider(["a", "b"])
    t1 = Tier1Context(
        signal=signal_ctx, provider_for=lambda attempt: provider, samples=3, temperature=1.0
    )
    collect_tier1(_segments(), t1)
    assert tier2_spy_registered.tier1_calls == 0


def test_collect_tier1의_enabled은_지정한_이름만_돌린다(spy_registered, signal_ctx):
    """`collect_all`의 `enabled` 경로는 `tests/test_signals_base.py`가 이미
    검증한다. `collect_tier1`의 `enabled` 경로는 이 태스크가 새로 연
    것이라 아무도 밟지 않은 채로 들어올 뻔했다(리뷰 지적) - 여기서 직접
    검증한다."""
    provider = ScriptedProvider(["a", "b"])
    t1 = Tier1Context(
        signal=signal_ctx, provider_for=lambda attempt: provider, samples=3, temperature=1.0
    )
    segs = _segments()
    result = collect_tier1(segs, t1, enabled=["test.tier1_spy"])
    assert [s.name for s in result["1"]] == ["test.tier1_spy"]
    assert spy_registered.tier1_calls == len(segs)


def test_collect_tier1은_tier0_이름을_enabled에_넣으면_거부한다(signal_ctx):
    """`collect_all`의 대칭 테스트(enabled에 tier1을 넣으면 거부)와 반대
    방향이다. `struct.untranslated`는 `cuesift.signals.structural`이 패키지
    import 시점에 전역 등록하는 실제 tier 0 신호다."""
    t1 = Tier1Context(
        signal=signal_ctx, provider_for=lambda attempt: None, samples=3, temperature=1.0
    )
    with pytest.raises(ValueError, match="tier 1만"):
        collect_tier1([], t1, enabled=["struct.untranslated"])


def test_collect_tier1은_등록되지_않은_이름을_거부한다(signal_ctx):
    """2라운드 리뷰 지적(B2) — `non_tier1` 거부는 6개 테스트로 이미
    덮였지만 `unknown` 거부(base.py의 `if unknown: raise ValueError(...)`)는
    무방비였다. 실측: 그 블록을 통째로 지워도 지정 6파일이 전부 통과했고,
    대신 `collect_tier1([], t1, enabled=["typo.does_not_exist"])`가 자기설명적인
    `ValueError`가 아니라 raw `KeyError: 'typo.does_not_exist'`를 던지는
    회귀가 생겼다 - `collect_all` 쪽은 `tests/test_signals_base.py::
    test_enabled_with_unknown_name_raises`가 이미 덮고 있어 비대칭이었다."""
    t1 = Tier1Context(
        signal=signal_ctx, provider_for=lambda attempt: None, samples=3, temperature=1.0
    )
    with pytest.raises(ValueError, match="등록되지 않은 신호"):
        collect_tier1([], t1, enabled=["typo.does_not_exist"])


def test_register는_tier_속성이_없으면_거부한다():
    """2라운드 리뷰 지적(C1) — `register()`의 `tier` 검사(base.py:137-138)는
    동작하지만 이 파일이 그것을 단언한 적이 없었다. 실측: 그 검사를
    통째로 지워도 지정 6파일이 전부 통과했다 - "게이트를 만들면 반드시
    실패시켜 봐야 한다"는 이 저장소의 규율에 미달했다.

    등록에 **실패**하는 경로라 `_REGISTRY`가 오염되지 않지만, 실패 도중
    잠깐이라도 들어갔다가 예외로 되돌아가는 경로가 아님을 신뢰하지 않고
    `spy_registered`와 같은 저장·복원 패턴을 그대로 따른다.
    """

    class _NoTier:
        name = "test.no_tier"

    saved = dict(registry())
    try:
        with pytest.raises(ValueError, match="tier"):
            register(_NoTier())
        assert "test.no_tier" not in registry()
    finally:
        registry().clear()
        registry().update(saved)
