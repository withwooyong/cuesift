"""자가일관성 신호 (FR-4.1 · 설계 §6)."""

from __future__ import annotations

import pytest
from tests.fakes.provider import EchoProvider

from cuesift.segment import Segment
from cuesift.signals.base import SignalContext, Tier1Context
from cuesift.signals.llm import SelfConsistency
from cuesift.spec import load_builtin


@pytest.fixture
def signal_ctx() -> SignalContext:
    # 기존 신호 테스트와 같은 방식이다 (tests/test_signals_derived.py).
    # conftest.py에는 SpecProfile fixture가 없다 - 각 파일이 load_builtin을
    # 직접 부른다.
    return SignalContext(
        profile=load_builtin("en"), glossary=None, source_lang="ko", target_lang="en"
    )


def _seg() -> Segment:
    return Segment(
        id="1",
        index=0,
        start_ms=0,
        end_ms=2000,
        source_text="그는 오지 않았다",
        target_text="He did not come",
    )


def _ctx(signal_ctx: SignalContext, texts: list[str]) -> Tier1Context:
    """시도마다 정해진 번역문을 내는 컨텍스트.

    **`EchoProvider`를 쓰는 이유는 재시도를 없애기 위해서다.** 이 가짜는
    요청받은 id를 그대로 채워 정상 JSON을 내므로 파싱이 실패하지 않고,
    호출 횟수가 정확히 `samples`와 같아진다. 응답 문자열을 손으로 조립하면
    `parse_translations`의 정수 id 계약(커밋 `817ed64`)을 다시 구현하는
    셈이고, 그 계약이 바뀌면 이 테스트가 조용히 재시도 경로를 타게 된다.

    시도마다 다른 프로바이더를 주는 것이 실제 배선과 같다(설계 §8) -
    캐시가 attempt로 갈리므로 각 시도가 자기 응답을 받는다.
    """
    # 기본 인자로 캡처한다. 루프 변수를 클로저로 잡으면 전부 마지막 값이
    # 되고, ruff의 B023이 그것을 잡는다.
    providers = [EchoProvider(transform=lambda _src, t=t: t) for t in texts]
    return Tier1Context(
        signal=signal_ctx,
        provider_for=lambda attempt: providers[attempt],
        samples=len(texts),
        temperature=1.0,
    )


def test_재번역이_모두_같으면_점수가_0이다(signal_ctx):
    """흔들리지 않았다 = 이 구간은 번역하기 쉽다."""
    same = "He did not come"
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, [same, same, same]))
    assert signal is not None
    assert signal.score == 0.0


def test_재번역이_흩어지면_점수가_높다(signal_ctx):
    scattered = [
        "He did not come",
        "완전히 다른 문장이 나왔다",
        "Something else entirely here",
    ]
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, scattered))
    assert signal is not None
    assert signal.score > 0.5


def test_신호에_근거가_담긴다(signal_ctx):
    """FR-6.4 - review.json이 '왜 선별되었는지'를 이것으로 쓴다."""
    same = "He did not come"
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, [same, same, same]))
    assert signal is not None
    assert len(signal.detail["samples"]) == 3
    assert len(signal.detail["pairwise"]) == 3  # 3개에서 나오는 쌍의 수


def test_hard_fail이_아니다(signal_ctx):
    """의미 판단은 결정론적이지 않다. hard fail은 오탐이 곧 지표 파괴다
    (FR-6.2 · 이 저장소의 '미탐이 오탐보다 낫다')."""
    same = "He did not come"
    signal = SelfConsistency().collect_tier1(_seg(), _ctx(signal_ctx, [same, same, same]))
    assert signal is not None
    assert signal.hard_fail is False
    assert signal.tier == 1


def test_성공분이_2개_미만이면_None이다(signal_ctx):
    """**score=0.0이 아니다.** 0.0은 '판정했고 안전하다'이고 None은
    '판정 대상이 아니다'다 - 0점 신호를 내면 review.json이 무의미한
    항목으로 채워진다(signals/base.py의 계약)."""
    # garbage=True면 파싱이 실패해 재시도 뒤에도 target_text가 안 채워진다.
    # _ctx를 쓰지 않는 이유는 그쪽이 transform으로 **정상** 응답을 내기
    # 때문이다 - "망가진 문자열"을 transform에 넣어도 그냥 번역문이 된다.
    providers = [EchoProvider(garbage=True) for _ in range(3)]
    ctx = Tier1Context(
        signal=signal_ctx,
        provider_for=lambda attempt: providers[attempt],
        samples=3,
        temperature=1.0,
    )
    assert SelfConsistency().collect_tier1(_seg(), ctx) is None


def test_번역문이_없으면_None이다(signal_ctx):
    """번역 실패분은 검수 대상이 아니라 재실행 대상이다
    (TranslationResult 독스트링)."""
    seg = Segment(
        id="1",
        index=0,
        start_ms=0,
        end_ms=2000,
        source_text="그는 오지 않았다",
        target_text=None,
    )
    same = "He did not come"
    assert SelfConsistency().collect_tier1(seg, _ctx(signal_ctx, [same, same, same])) is None


def test_시도마다_다른_프로바이더를_받는다(signal_ctx):
    """설계 §8 - attempt별로 캐시가 갈려야 분산이 관측된다."""
    seen: list[int] = []
    providers = [EchoProvider() for _ in range(3)]

    def provider_for(attempt: int):
        seen.append(attempt)
        return providers[attempt]

    ctx = Tier1Context(signal=signal_ctx, provider_for=provider_for, samples=3, temperature=1.0)
    SelfConsistency().collect_tier1(_seg(), ctx)
    assert seen == [0, 1, 2]
