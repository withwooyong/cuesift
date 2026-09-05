"""역번역 유사도 신호 (FR-4.2 · 설계 §5).

**가짜 임베더가 내는 벡터로 점수를 결정론적으로 만든다.** 실제 모델을
쓰면 값이 흔들려 경계 조건을 못 박을 수 없다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from tests.fakes.provider import EchoProvider

from cuesift.segment import Segment
from cuesift.signals.backtranslation import BackTranslation
from cuesift.signals.base import SignalContext, Tier1Context
from cuesift.spec.profile import load_builtin


class FakeEmbedder:
    """텍스트를 미리 정한 벡터로 바꾼다. 모르는 텍스트는 예외를 낸다."""

    name = "fake-embed"

    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._table[t] for t in texts]

    def close(self) -> None:
        return None


def _segment(source: str, target: str | None) -> Segment:
    return Segment(
        id="00007", index=7, start_ms=0, end_ms=2000, source_text=source, target_text=target
    )


def _ctx(embedder, provider) -> Tier1Context:
    signal = SignalContext(
        profile=load_builtin("ted-en"), glossary=None, source_lang="ko", target_lang="en"
    )
    return Tier1Context(
        signal=signal,
        provider_for=lambda attempt: provider,
        samples=2,
        temperature=1.0,
        embedder=embedder,
    )


def test_원문과_역번역이_같으면_점수가_0이다():
    seg = _segment("비가 온다", "It rains")
    # 실제 EchoProvider 기본 transform은 "EN:" 접두를 붙인다(문서 확인 완료,
    # tests/fakes/provider.py). 그대로 두면 역번역문이 "EN:It rains"가 되어
    # FakeEmbedder 조회표에 없는 키를 찾게 되므로, 라운드트립 검증이라는
    # 이 테스트의 취지에 맞춰 항등 transform을 명시한다.
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    provider = EchoProvider(transform=lambda s: s)
    signal = BackTranslation().collect_tier1(seg, _ctx(embedder, provider))
    assert signal is not None
    assert signal.score == pytest.approx(0.0)


def test_방향이_반대면_clamp가_1에서_자른다():
    # **코사인의 치역이 [-1, 1]이라 1 - cos가 2.0까지 간다.**
    # `signals/llm.py`의 clamp 주석이 예견한 자리이며, 여기서 처음 작동한다.
    seg = _segment("비가 온다", "It rains")
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [-1.0, 0.0]})
    provider = EchoProvider(transform=lambda s: s)
    signal = BackTranslation().collect_tier1(seg, _ctx(embedder, provider))
    assert signal is not None
    assert signal.score == 1.0


def test_번역이_없으면_None이다():
    seg = _segment("비가 온다", None)
    embedder = FakeEmbedder({})
    assert BackTranslation().collect_tier1(seg, _ctx(embedder, EchoProvider())) is None


def test_embedder가_없으면_예외다():
    # **None을 내면 신호가 전 구간 0건으로 끝나고 그것이 "안전"으로 읽힌다.**
    # 배선 누락은 조용히 넘어갈 사고가 아니다 (설계 D6).
    seg = _segment("비가 온다", "It rains")
    ctx = _ctx(None, EchoProvider())
    with pytest.raises(ValueError, match="embedder"):
        BackTranslation().collect_tier1(seg, ctx)


def test_역번역은_방향을_뒤집는다():
    # 프로바이더가 받은 프롬프트의 번역 방향을 확인한다. 뒤집지 않으면
    # 원본 번역 캐시에 히트해 역번역문이 번역문과 같아지고, 코사인이
    # 1.0에 붙어 신호가 전 구간 0점이 된다 (설계 §6).
    #
    # **`"en" in sent and "ko" in sent`만으로는 방향을 검증하지 못한다.**
    # 실제로 되돌려 확인했다: `prompt.py`의 `_SYSTEM_BASE`가
    # "{source_lang} 자막을 {target_lang}로 번역한다"이므로 정방향과
    # 똑같이 (source=ko, target=en)으로 호출해도 "en"·"ko" 두 글자는
    # 여전히 프롬프트에 나타난다 - 이 단언은 방향이 뒤집혀도 통과한다.
    # 문구 전체(부분 문자열)를 대조해야 순서(=방향)가 드러난다.
    seg = _segment("비가 온다", "It rains")
    provider = EchoProvider(transform=lambda s: s)
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    BackTranslation().collect_tier1(seg, _ctx(embedder, provider))
    sent = "\n".join(m.content for m in provider.last_messages)
    assert "en 자막을 ko로 번역한다" in sent
    assert "ko 자막을 en로 번역한다" not in sent


def test_용어집을_넘기지_않는다():
    # 용어집이 원문 어휘를 강제하면 오류 문장의 역번역도 원문에 가까워져
    # 유사도 격차가 줄고 신호가 둔해진다 (설계 D2).
    seg = _segment("비가 온다", "It rains")
    provider = EchoProvider(transform=lambda s: s)
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    BackTranslation().collect_tier1(seg, _ctx(embedder, provider))
    sent = "\n".join(m.content for m in provider.last_messages)
    assert "용어집" not in sent


def test_임베딩은_한_요청에_두_텍스트를_담는다():
    seg = _segment("비가 온다", "It rains")
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    provider = EchoProvider(transform=lambda s: s)
    BackTranslation().collect_tier1(seg, _ctx(embedder, provider))
    assert embedder.calls == [["비가 온다", "It rains"]]


def test_detail에_역번역문과_코사인이_실린다():
    # FR-6.4 - review.json이 "왜 선별되었는지"를 이것으로 쓴다.
    seg = _segment("비가 온다", "It rains")
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    provider = EchoProvider(transform=lambda s: s)
    signal = BackTranslation().collect_tier1(seg, _ctx(embedder, provider))
    assert signal is not None
    assert signal.detail["back_translation"] == "It rains"
    assert signal.detail["cosine"] == pytest.approx(1.0)


def test_hard_fail이_아니다():
    # 의미 판단은 결정론적이지 않고, hard fail 오탐은 실제 검수 비율을
    # 부풀려 Recall@Budget 지표 자체를 파괴한다 (FR-6.2).
    seg = _segment("비가 온다", "It rains")
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    provider = EchoProvider(transform=lambda s: s)
    signal = BackTranslation().collect_tier1(seg, _ctx(embedder, provider))
    assert signal is not None
    assert signal.hard_fail is False


def build_messages_for(source_lang: str, target_lang: str, text: str):
    """`build_messages`를 세그먼트 하나짜리 배치로 호출하는 얇은 헬퍼.

    프롬프트를 테스트가 직접 지어 넘기면 실제 조립 로직과 갈라질 수 있으므로
    (기존 caveat 사고, CLAUDE.md 참고) 실제 함수를 그대로 호출한다.
    """
    from cuesift.translate.prompt import build_messages

    seg = Segment(id="0", index=0, start_ms=0, end_ms=1000, source_text=text, target_text=None)
    return build_messages(
        [seg],
        source_lang=source_lang,
        target_lang=target_lang,
    )


def test_역번역과_정방향_번역이_같은_캐시를_쓰지_않는다():
    """온도가 둘 다 0.0인데도 캐시가 섞이지 않는 것을 못 박는다 (설계 §6).

    `store/cache.py`의 키 주석은 "Tier 1이 temperature=0.0으로 불리면
    성질이 깨진다"고 경고한다. 역번역이 그 조건에 정확히 해당하는데도
    안전한 이유는 **번역 방향이 반대라 messages_sha가 다르기** 때문이다.

    **누군가 역번역을 같은 방향으로 바꾸면 이 테스트가 실패해야 한다.**
    바뀌면 정방향 번역 캐시에 히트해 역번역문이 번역문과 같아지고,
    코사인이 1.0에 붙어 신호가 전 구간 0점이 된다.
    """
    from cuesift.store.cache import CacheRequest

    forward = CacheRequest(
        identity="test|model",
        temperature=0.0,
        max_tokens=None,
        messages=tuple(build_messages_for("ko", "en", "비가 온다")),
    )
    backward = CacheRequest(
        identity="test|model",
        temperature=0.0,
        max_tokens=None,
        messages=tuple(build_messages_for("en", "ko", "It rains")),
    )
    assert forward.key != backward.key
