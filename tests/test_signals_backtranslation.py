"""역번역 유사도 신호 (FR-4.2 · 설계 §5).

**가짜 임베더가 내는 벡터로 점수를 결정론적으로 만든다.** 실제 모델을
쓰면 값이 흔들려 경계 조건을 못 박을 수 없다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from tests.fakes.provider import EchoProvider

from cuesift.glossary import Glossary, GlossaryEntry
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


def _ctx(embedder, provider, glossary: Glossary | None = None) -> Tier1Context:
    signal = SignalContext(
        profile=load_builtin("ted-en"), glossary=glossary, source_lang="ko", target_lang="en"
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
    #
    # **방향 라벨만 되돌리는 회귀는 이 테스트 하나가 전담한다.**
    # `test_역번역과_정방향_번역이_같은_캐시를_쓰지_않는다`는 콘텐츠(`local_seg`의
    # `source_text` 스왑)까지 함께 정방향과 같아져야 실패한다(실측 - 방향
    # 라벨만 되돌리는 파괴 실험으로는 그 테스트가 통과했다). 즉 "방향 라벨만"
    # 회귀에서 우는 게이트는 이 테스트뿐이다 - 이 테스트를 약화시키면
    # 그 회귀는 아무 데서도 걸리지 않는다.
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
    #
    # **`glossary=None`인 픽스처만으로는 이 테스트가 공허하다** (리뷰 Important 1).
    # `ctx.signal.glossary`가 애초에 `None`이면 `_backtranslate`가
    # `glossary=None` 대신 `glossary=ctx.signal.glossary`를 넘기도록 바뀌어도
    # `prompt.py`의 `if glossary is not None: ... if entries:`가 여전히 빈
    # 블록만 만들어 단언이 그대로 통과한다. **항목이 실제로 있고, 그 항목이
    # 역번역 대상 문자열("It rains")에 실제로 등장하도록** 픽스처를 채워야
    # 버그가 들어오면 "용어집 (반드시 이 대응어를 쓴다):" 블록이 실제로
    # 프롬프트에 나타나 이 단언이 깨진다.
    seg = _segment("비가 온다", "It rains")
    provider = EchoProvider(transform=lambda s: s)
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    glossary = Glossary(entries=(GlossaryEntry(source="rains", targets=("비",)),))
    BackTranslation().collect_tier1(seg, _ctx(embedder, provider, glossary=glossary))
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


def test_역번역이_실패하면_None이다():
    # 재시도·개별 폴백이 전부 파싱에 실패해 target_text가 None으로 남는
    # 경로다 (리뷰 Important 4). `garbage=True`는 `RetryableProviderError`를
    # 던지지 않고 파싱 불가능한 텍스트를 반환하므로 `InvalidResponseError`
    # 경로(배치 실패 -> 개별 폴백도 실패)를 타고, 실제 대기(sleep) 없이
    # 빠르게 끝난다.
    seg = _segment("비가 온다", "It rains")
    embedder = FakeEmbedder({})
    provider = EchoProvider(garbage=True)
    assert BackTranslation().collect_tier1(seg, _ctx(embedder, provider)) is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_역번역이_빈_문자열이거나_공백뿐이면_None이다(blank: str):
    # 빈 문자열과 공백뿐인 문자열을 모두 덮는다 (리뷰 Important 4 · Minor 6).
    # 공백뿐인 역번역이 그대로 임베더에 가면 영벡터가 나올 수 있고 `cosine`이
    # 거기서 `ValueError`를 던져 벤치 실행이 죽는다 - `backtranslation.py`의
    # `if not back or not back.strip()`가 이 값을 걸러야 embedder.embed가
    # 아예 불리지 않는다. `FakeEmbedder({})`(빈 조회표)로 두어, 걸러지지
    # 않으면 `KeyError`로 이 사실이 드러나게 했다.
    seg = _segment("비가 온다", "It rains")
    embedder = FakeEmbedder({})
    provider = EchoProvider(transform=lambda s: blank)
    assert BackTranslation().collect_tier1(seg, _ctx(embedder, provider)) is None


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

    **`CacheRequest` 두 개를 손으로 지어 키만 비교하면 이 신호의 실제
    코드를 한 줄도 거치지 않는다** (리뷰 Important 2). 그러면
    `_backtranslate`가 방향을 뒤집지 않도록 바뀌어도 이 테스트는 여전히
    `build_messages_for`가 각기 다른 인자로 만든 두 프롬프트를 비교할
    뿐이라 통과해 버린다 - 실제 방향 게이트는 `test_역번역은_방향을_뒤집는다`
    하나뿐이었다. **`BackTranslation.collect_tier1`을 실제로 돌려 프로바이더가
    받은 메시지(`provider.last_messages`)로 역방향 키를 만든다.**

    ## 이 테스트가 잡지 못하는 범위 - 방향 라벨만의 회귀

    **파괴 실험으로 확인했다: `_backtranslate`의 방향 라벨만 정방향과 같게
    되돌려도(`source_text` 스왑은 그대로 두고) 이 테스트는 통과한다.**
    역방향 요청 본문은 여전히 `local_seg.source_text`(= `seg.target_text`,
    "It rains")이고 정방향 참조 본문은 `seg.source_text`("비가 온다")라
    콘텐츠 자체가 이미 달라서 `messages_sha`가 갈리기 때문이다. 이 테스트가
    실제로 실패하려면 콘텐츠(`source_text` 스왑)까지 함께 정방향과 같아져야
    한다("역번역을 같은 방향으로 한 번 더" - 콘텐츠+방향이 모두 정방향과
    같아지는 완전 붕괴에서 SHA 완전 일치를 실측했다).

    **그래서 "방향 라벨만" 되돌리는 부분 회귀는 `test_역번역은_방향을_뒤집는다`가
    전담한다.** 이 테스트 하나만 보고 "방향 회귀는 여기서 다 잡힌다"고 오인해
    그쪽을 약화시키면, 방향 라벨만의 회귀는 이 테스트도 그쪽도 잡지 못한 채
    아무 게이트도 울리지 않고 통과한다.
    """
    from cuesift.store.cache import CacheRequest

    seg = _segment("비가 온다", "It rains")
    provider = EchoProvider(transform=lambda s: s)
    embedder = FakeEmbedder({"비가 온다": [1.0, 0.0], "It rains": [1.0, 0.0]})
    BackTranslation().collect_tier1(seg, _ctx(embedder, provider))

    backward = CacheRequest(
        identity="test|model",
        temperature=0.0,
        max_tokens=None,
        messages=tuple(provider.last_messages),
    )
    forward = CacheRequest(
        identity="test|model",
        temperature=0.0,
        max_tokens=None,
        messages=tuple(build_messages_for("ko", "en", "비가 온다")),
    )
    assert forward.key != backward.key
