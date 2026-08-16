"""실행 엔진 - 배치, 폴백, 재시도 (FR-2.6)."""

from __future__ import annotations

import inspect
import json

import pytest
from tests.fakes.provider import EchoProvider, ScriptedProvider

from cuesift.glossary import Glossary, GlossaryEntry
from cuesift.segment.models import Segment
from cuesift.translate.engine import _MAX_BACKOFF_S, translate_segments
from cuesift.translate.provider import (
    FatalProviderError,
    Provider,
    RetryableProviderError,
)


def _segs(n: int) -> list[Segment]:
    return [
        Segment(
            id=f"s{i}", index=i, start_ms=i * 1000, end_ms=i * 1000 + 900, source_text=f"문장{i}"
        )
        for i in range(n)
    ]


def _ok(ids: list[int]) -> str:
    return json.dumps({"translations": [{"id": i, "text": f"EN{i}"} for i in ids]})


def _user_content(provider: EchoProvider | ScriptedProvider, call_index: int = 0) -> str:
    """프로바이더가 받은 유저 메시지. 프롬프트 조립 인자가 실제로 흘렀는지 본다."""
    return provider.calls[call_index][-1].content


# --------------------------------------------------------------------------
# 정상 경로
# --------------------------------------------------------------------------


def test_정상_경로에서_전부_번역된다() -> None:
    result = translate_segments(
        _segs(3), provider=EchoProvider(), source_lang="ko", target_lang="en"
    )
    assert [s.target_text for s in result.segments] == ["EN:문장0", "EN:문장1", "EN:문장2"]
    assert result.failures == ()
    assert result.target_lang == "en"


def test_원본_세그먼트를_변형하지_않는다() -> None:
    # en이 채운 target_text를 ja가 덮어쓰면 두 언어를 동시에 들 수 없다
    # (설계 §3.2).
    original = _segs(2)
    translate_segments(original, provider=EchoProvider(), source_lang="ko", target_lang="en")
    assert all(s.target_text is None for s in original)


def test_배치_크기대로_호출한다() -> None:
    provider = EchoProvider()
    translate_segments(
        _segs(25), provider=provider, source_lang="ko", target_lang="en", batch_size=10
    )
    assert len(provider.calls) == 3


def test_사용량을_누적한다() -> None:
    provider = EchoProvider()
    result = translate_segments(
        _segs(25), provider=provider, source_lang="ko", target_lang="en", batch_size=10
    )
    assert result.usage.calls == 3


def test_빈_입력은_호출하지_않는다() -> None:
    provider = EchoProvider()
    result = translate_segments([], provider=provider, source_lang="ko", target_lang="en")
    assert provider.calls == []
    assert result.segments == ()
    assert result.usage.calls == 0


def test_temperature를_그대로_넘긴다() -> None:
    # WP8 자가일관성이 의도적으로 올려 쓴다 (설계 §8.2).
    seen: list[float] = []

    class Recording(EchoProvider):
        def complete(self, messages, *, temperature, max_tokens):  # type: ignore[no-untyped-def]
            seen.append(temperature)
            return super().complete(messages, temperature=temperature, max_tokens=max_tokens)

    translate_segments(
        _segs(1), provider=Recording(), source_lang="ko", target_lang="en", temperature=0.9
    )
    assert seen == [0.9]


# --------------------------------------------------------------------------
# 개별 폴백 (InvalidResponseError에서만 돈다)
# --------------------------------------------------------------------------


def test_개수_불일치는_개별_폴백을_탄다() -> None:
    # 배치(2개 이상)는 깨뜨리고 개별 호출(1개)은 성공시킨다.
    provider = EchoProvider(fail_batches_of_size=2)
    result = translate_segments(
        _segs(3), provider=provider, source_lang="ko", target_lang="en", batch_size=3
    )
    # 배치 1회 실패 + 개별 3회 = 4회
    assert len(provider.calls) == 4
    assert result.failures == ()
    assert [s.target_text for s in result.segments] == ["EN:문장0", "EN:문장1", "EN:문장2"]


def test_파싱_실패도_개별_폴백을_탄다() -> None:
    provider = ScriptedProvider(["산문 응답입니다", _ok([0]), _ok([1])])
    result = translate_segments(
        _segs(2), provider=provider, source_lang="ko", target_lang="en", batch_size=2
    )
    assert len(provider.calls) == 3
    assert result.failures == ()


def test_개별_폴백도_실패하면_그_세그먼트만_실패한다() -> None:
    # 배치 깨짐 -> 개별 3회 중 가운데만 산문 응답.
    provider = ScriptedProvider(["산문", _ok([0]), "산문", _ok([2])])
    result = translate_segments(
        _segs(3), provider=provider, source_lang="ko", target_lang="en", batch_size=3
    )
    assert [f.segment_id for f in result.failures] == ["s1"]
    assert result.failures[0].reason == "invalid_response"
    # 나머지는 진행한다 (FR-2.6).
    assert result.segments[0].target_text == "EN0"
    assert result.segments[1].target_text is None
    assert result.segments[2].target_text == "EN2"


def test_폴백_사용량에_실패한_배치_호출이_포함된다() -> None:
    # 배치 호출은 응답을 받았으므로 토큰을 썼다. 빼면 NFR-2 비용 리포트가
    # 폴백이 잦을수록 더 크게 과소 계상된다.
    provider = EchoProvider(fail_batches_of_size=2)
    result = translate_segments(
        _segs(3), provider=provider, source_lang="ko", target_lang="en", batch_size=3
    )
    # 배치 1회 + 개별 3회. 가짜는 호출당 calls=1을 낸다.
    assert result.usage.calls == 4


def test_폴백은_배치의_전역_번호를_그대로_기대한다() -> None:
    # 두 번째 배치를 폴백시킨다. 개별 호출의 기대 id를 0부터 다시 세면
    # 여기서만 깨진다 - 첫 배치로 시험하면 0-기반과 구별되지 않는다.
    provider = ScriptedProvider([_ok([0, 1]), "산문", _ok([2]), _ok([3])])
    result = translate_segments(
        _segs(4),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        batch_size=2,
        context_window=0,
    )
    assert len(provider.calls) == 4
    assert result.failures == ()
    assert [s.target_text for s in result.segments] == ["EN0", "EN1", "EN2", "EN3"]


def test_맥락이_있는_배치도_폴백할_수_있다() -> None:
    # 폴백은 1개짜리 배치에 원래 배치의 앞뒤 맥락을 그대로 붙인다. 맥락과
    # 번역 대상의 번호가 겹치면 `build_messages`가 ValueError를 던지고 그것은
    # 폴백이 잡는 예외가 아니라 실행 전체를 끝낸다. 맥락이 빈 배치로만
    # 시험하면 이 자리가 통째로 검사되지 않는다.
    provider = ScriptedProvider([_ok([0, 1]), "산문", _ok([2]), _ok([3]), _ok([4, 5])])
    result = translate_segments(
        _segs(6),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        batch_size=2,
        context_window=2,
    )
    # 배치 3회 + 가운데 배치의 개별 2회 = 5회
    assert len(provider.calls) == 5
    assert result.failures == ()
    assert [s.target_text for s in result.segments] == [f"EN{i}" for i in range(6)]
    # 폴백 호출에 맥락이 실제로 실렸다. 없으면 위 단언들은 맥락을 통째로
    # 버리는 구현에서도 통과한다.
    fallback_body = _user_content(provider, 2)
    assert "## 앞 맥락" in fallback_body
    assert "## 뒤 맥락" in fallback_body
    assert "[2]" in fallback_body


def test_치명적_실패는_폴백_도중에도_전파된다() -> None:
    # 폴백은 개별 호출을 배치 크기만큼 만든다. 여기서 401을 삼키면
    # 잘못된 키 하나가 배치마다 N회씩 실패 호출을 낸다.
    provider = ScriptedProvider(["산문", FatalProviderError("401 Unauthorized")])
    with pytest.raises(FatalProviderError):
        translate_segments(
            _segs(3),
            provider=provider,
            source_lang="ko",
            target_lang="en",
            batch_size=3,
            sleep=lambda _s: None,
        )
    # 배치 1회 + 개별 1회에서 멈춘다. 나머지 2개는 부르지 않았다.
    assert len(provider.calls) == 2


def test_폴백_도중_재시도가_소진되면_그_세그먼트만_실패한다() -> None:
    # 모델이 형식을 어겨 폴백으로 내려간 뒤 서버가 죽는 경로다. 두 실패
    # 양식이 겹치는 자리라, 여기서 배치 전체를 실패로 접으면 이미 성공한
    # 개별 번역까지 잃는다.
    provider = ScriptedProvider(
        ["산문", _ok([0]), RetryableProviderError("503"), RetryableProviderError("503")]
    )
    result = translate_segments(
        _segs(2),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        batch_size=2,
        max_retries=1,
        sleep=lambda _s: None,
    )
    # 배치 1회 + s0 개별 1회 + s1 개별 2회(최초 1 + 재시도 1) = 4회
    assert len(provider.calls) == 4
    assert result.segments[0].target_text == "EN0"
    assert result.segments[1].target_text is None
    # 서버가 죽은 것이지 모델이 형식을 어긴 것이 아니다.
    assert [(f.segment_id, f.reason, f.attempts) for f in result.failures] == [
        ("s1", "provider_error", 2)
    ]


# --------------------------------------------------------------------------
# 빈 번역 (계약은 지켜졌고 그 세그먼트만 쓸모없다)
# --------------------------------------------------------------------------


def test_빈_번역은_그_세그먼트만_실패한다() -> None:
    raw = json.dumps({"translations": [{"id": 0, "text": "EN0"}, {"id": 1, "text": "   "}]})
    provider = ScriptedProvider([raw])
    result = translate_segments(
        _segs(2), provider=provider, source_lang="ko", target_lang="en", batch_size=2
    )
    assert [f.reason for f in result.failures] == ["empty_translation"]
    assert result.segments[1].target_text is None


def test_배치_일부만_비어도_나머지는_전부_성공한다() -> None:
    # 10개 중 3개가 공백이다. 배치를 통째로 버리면 7개까지 잃는다.
    empty_ids = {2, 5, 9}
    raw = json.dumps(
        {
            "translations": [
                {"id": i, "text": "  " if i in empty_ids else f"EN{i}"} for i in range(10)
            ]
        }
    )
    provider = ScriptedProvider([raw])
    result = translate_segments(
        _segs(10), provider=provider, source_lang="ko", target_lang="en", batch_size=10
    )
    # 폴백을 타지 않았다 - 계약은 지켜졌기 때문이다.
    assert len(provider.calls) == 1
    assert [f.segment_id for f in result.failures] == ["s2", "s5", "s9"]
    assert [s.target_text for s in result.segments] == [
        None if i in empty_ids else f"EN{i}" for i in range(10)
    ]


def test_실패한_세그먼트는_들어온_target_text를_남기지_않는다() -> None:
    # 같은 세그먼트를 en으로 채운 뒤 ja로 다시 넣는 사용법이 있다. 실패분에
    # 이전 언어의 번역문이 남으면 그것이 ja 결과로 보고되고, failures와
    # target_text가 서로 다른 말을 한다.
    segments = _segs(2)
    segments[1].target_text = "이전 언어의 번역문"
    raw = json.dumps({"translations": [{"id": 0, "text": "JA0"}, {"id": 1, "text": ""}]})
    provider = ScriptedProvider([raw])
    result = translate_segments(
        segments, provider=provider, source_lang="ko", target_lang="ja", batch_size=2
    )
    assert result.segments[1].target_text is None
    assert [f.segment_id for f in result.failures] == ["s1"]
    # 성공한 쪽은 새 언어의 번역문으로 덮인다.
    assert result.segments[0].target_text == "JA0"


# --------------------------------------------------------------------------
# 재시도
# --------------------------------------------------------------------------


def test_재시도_가능_실패는_다시_건다() -> None:
    provider = ScriptedProvider([RetryableProviderError("503"), _ok([0])])
    result = translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        sleep=lambda _s: None,
    )
    assert len(provider.calls) == 2
    assert result.failures == ()


def test_재시도가_소진되면_배치_전원이_실패한다() -> None:
    provider = ScriptedProvider([RetryableProviderError("503")] * 4)
    result = translate_segments(
        _segs(3),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        batch_size=3,
        max_retries=3,
        sleep=lambda _s: None,
    )
    # 최초 1회 + 재시도 3회 = 4회. **개별 폴백은 타지 않는다** (설계 §6.3).
    assert len(provider.calls) == 4
    assert [f.segment_id for f in result.failures] == ["s0", "s1", "s2"]
    assert all(f.reason == "provider_error" for f in result.failures)


def test_소진된_실패는_시도_횟수를_기록한다() -> None:
    # attempts가 없으면 "실패 800건"에서 한 번 만에 죽었는지 네 번 버텼는지
    # 구분되지 않는다.
    provider = ScriptedProvider([RetryableProviderError("503")] * 3)
    result = translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        max_retries=2,
        sleep=lambda _s: None,
    )
    assert [f.attempts for f in result.failures] == [3]


def test_성공한_뒤의_실패는_그_배치의_시도_횟수를_기록한다() -> None:
    # 재시도 2회 뒤 성공했지만 번역문이 비었다. attempts는 1이 아니라 3이다.
    raw = json.dumps({"translations": [{"id": 0, "text": ""}]})
    provider = ScriptedProvider([RetryableProviderError("503")] * 2 + [raw])
    result = translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        sleep=lambda _s: None,
    )
    assert [(f.reason, f.attempts) for f in result.failures] == [("empty_translation", 3)]


def test_max_retries_0이면_한_번만_호출한다() -> None:
    provider = ScriptedProvider([RetryableProviderError("503")])
    waited: list[float] = []
    result = translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        max_retries=0,
        sleep=waited.append,
    )
    assert len(provider.calls) == 1
    assert waited == []
    assert [f.attempts for f in result.failures] == [1]


def test_max_retries_1이면_두_번_호출한다() -> None:
    provider = ScriptedProvider([RetryableProviderError("503"), _ok([0])])
    waited: list[float] = []
    result = translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        max_retries=1,
        sleep=waited.append,
    )
    assert len(provider.calls) == 2
    assert waited == [1.0]
    assert result.failures == ()


def test_소진된_뒤에는_자지_않는다() -> None:
    # 마지막 실패 뒤에 자면 아무도 기다릴 이유가 없는 시간을 CLI가 쓴다.
    # 호출 4회에 대기는 3회다.
    provider = ScriptedProvider([RetryableProviderError("503")] * 4)
    waited: list[float] = []
    translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        max_retries=3,
        sleep=waited.append,
    )
    assert len(provider.calls) == 4
    assert waited == [1.0, 2.0, 4.0]


def test_음수_max_retries는_거부된다() -> None:
    # 설정(`cuesift.yaml`)의 오타다. 막지 않으면 재시도 루프가 한 번도 돌지
    # 않은 채 끝나 "잡은 예외가 없는데 던질 것을 찾는" 자리에 떨어진다.
    provider = EchoProvider()
    with pytest.raises(ValueError, match="max_retries"):
        translate_segments(
            _segs(1), provider=provider, source_lang="ko", target_lang="en", max_retries=-1
        )
    assert provider.calls == []


def test_치명적_실패는_즉시_전파된다() -> None:
    # 구분이 없으면 API 키 오타가 800건 실패 리포트로 나온다 (설계 §4.2).
    provider = ScriptedProvider([FatalProviderError("401 Unauthorized")])
    with pytest.raises(FatalProviderError):
        translate_segments(
            _segs(10),
            provider=provider,
            source_lang="ko",
            target_lang="en",
            batch_size=5,
            sleep=lambda _s: None,
        )
    # 재시도도 폴백도 하지 않았다.
    assert len(provider.calls) == 1


# --------------------------------------------------------------------------
# 백오프
# --------------------------------------------------------------------------


def test_백오프가_지수로_는다() -> None:
    waited: list[float] = []
    provider = ScriptedProvider([RetryableProviderError("503")] * 3 + [_ok([0])])
    translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        sleep=waited.append,
    )
    assert waited == [1.0, 2.0, 4.0]


def test_retry_after를_존중한다() -> None:
    # 무시하면 서버가 지정한 대기를 어겨 일시적 제한이 영구 차단으로
    # 승격될 수 있다.
    waited: list[float] = []
    provider = ScriptedProvider([RetryableProviderError("429", retry_after_s=7.5), _ok([0])])
    translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        sleep=waited.append,
    )
    assert waited == [7.5]


def test_retry_after_0을_존중한다() -> None:
    # 0은 유효한 힌트("지금 다시 걸어도 된다")이고 Task 1의 정규화도 0을
    # 통과시킨다. 참·거짓으로 판정하면 0이 None과 뭉뚱그려져 1초를 잔다.
    waited: list[float] = []
    provider = ScriptedProvider([RetryableProviderError("429", retry_after_s=0), _ok([0])])
    translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        sleep=waited.append,
    )
    assert waited == [0]


def test_상한_미만의_retry_after는_그대로_존중한다() -> None:
    waited: list[float] = []
    below = _MAX_BACKOFF_S - 0.5
    provider = ScriptedProvider([RetryableProviderError("429", retry_after_s=below), _ok([0])])
    translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        sleep=waited.append,
    )
    assert waited == [below]


def test_상한을_넘는_retry_after는_잘린다() -> None:
    # `Retry-After: 86400`은 일일 할당량을 리셋하는 실서비스가 흔히 보내는
    # 값이다. 그대로 자면 CLI가 하루 동안 무출력으로 멈춘다.
    waited: list[float] = []
    provider = ScriptedProvider([RetryableProviderError("429", retry_after_s=86400), _ok([0])])
    translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        sleep=waited.append,
    )
    assert waited == [_MAX_BACKOFF_S]


def test_지수_백오프도_상한에서_멈춘다() -> None:
    # 상한이 retry_after 경로에만 걸리면 max_retries를 크게 잡은 설정에서
    # 2**attempt가 그대로 자란다.
    waited: list[float] = []
    provider = ScriptedProvider([RetryableProviderError("503")] * 8 + [_ok([0])])
    translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        max_retries=8,
        sleep=waited.append,
    )
    assert waited == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, _MAX_BACKOFF_S, _MAX_BACKOFF_S]
    assert max(waited) == _MAX_BACKOFF_S


# --------------------------------------------------------------------------
# 프롬프트 조립 인자가 실제로 흐르는가
# --------------------------------------------------------------------------


@pytest.mark.parametrize("context_window", [1, 2])
def test_맥락_윈도우가_프롬프트에_실린다(context_window: int) -> None:
    # 1과 2를 모두 본다. 하나만 보면 "맥락을 통째로 비운다"와 "한 개만
    # 붙인다"가 구별되지 않는다.
    provider = EchoProvider()
    translate_segments(
        _segs(9),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        batch_size=3,
        context_window=context_window,
    )
    second = _user_content(provider, 1)
    assert "## 앞 맥락" in second
    assert "## 뒤 맥락" in second
    before_ids = [f"[{i}]" for i in range(3 - context_window, 3)]
    after_ids = [f"[{i}]" for i in range(6, 6 + context_window)]
    for token in before_ids + after_ids:
        assert token in second
    # 윈도우 밖은 실리지 않는다. 없으면 "전부 붙인다"와 구별되지 않는다.
    assert f"[{3 - context_window - 1}]" not in second


def test_context_window_0이면_맥락_절이_없다() -> None:
    provider = EchoProvider()
    translate_segments(
        _segs(6),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        batch_size=3,
        context_window=0,
    )
    second = _user_content(provider, 1)
    # 대상 절이 실제로 조립됐는지 먼저 본다 - 통째로 비어도 통과하는
    # `not in`을 공허하게 두지 않는다.
    assert "## 번역 대상" in second
    assert "맥락" not in second


def test_용어집이_프롬프트에_실린다() -> None:
    glossary = Glossary(entries=(GlossaryEntry(source="문장1", targets=("Sentence One",)),))
    provider = EchoProvider()
    translate_segments(
        _segs(3),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        glossary=glossary,
    )
    system = provider.calls[0][0].content
    assert "Sentence One" in system


def test_작품_맥락이_프롬프트에_실린다() -> None:
    provider = EchoProvider()
    translate_segments(
        _segs(1),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        work_context="기후 위기에 대한 TED 강연이다",
    )
    system = provider.calls[0][0].content
    assert "기후 위기에 대한 TED 강연이다" in system


def test_언어쌍이_프롬프트에_실린다() -> None:
    provider = EchoProvider()
    result = translate_segments(_segs(1), provider=provider, source_lang="ko", target_lang="ja")
    system = provider.calls[0][0].content
    assert "ko" in system
    assert "ja" in system
    assert result.target_lang == "ja"


# --------------------------------------------------------------------------
# 여러 줄 자막 (Task 3의 `\n` 이스케이프와 맞물린다)
# --------------------------------------------------------------------------


def _multiline_seg(index: int, text: str) -> Segment:
    return Segment(
        id=f"m{index}",
        index=index,
        start_ms=index * 1000,
        end_ms=index * 1000 + 900,
        source_text=text,
    )


def test_여러_줄_자막이_진짜_개행으로_돌아온다() -> None:
    # 프롬프트는 개행을 두 글자 `\n`으로 내보내고 모델은 JSON에 같은 두
    # 글자를 쓴다. `json.loads`가 그것을 진짜 개행으로 푸는 것이 의도한
    # 결과이고, 규격 검사(줄 수·줄당 문자)가 그 개행을 본다.
    segments = [_multiline_seg(0, "첫째 줄입니다\n둘째 줄입니다")]
    provider = EchoProvider()
    result = translate_segments(segments, provider=provider, source_lang="ko", target_lang="en")
    assert result.failures == ()
    assert result.segments[0].target_text == "EN:첫째 줄입니다\n둘째 줄입니다"


def test_대괄호로_시작하는_둘째_줄도_삼켜지지_않는다() -> None:
    # 이스케이프 전에는 둘째 줄이 번호 없는 줄로 나갔고, `[`로 시작하면
    # 프롬프트를 되읽는 파서가 `int("음악")`에서 죽었다.
    segments = [_multiline_seg(0, "노래가 흐른다\n[음악]")]
    provider = EchoProvider()
    result = translate_segments(segments, provider=provider, source_lang="ko", target_lang="en")
    assert result.failures == ()
    assert result.segments[0].target_text == "EN:노래가 흐른다\n[음악]"


def test_여러_줄_맥락도_대상_절을_오염시키지_않는다() -> None:
    # 앞 맥락이 두 줄이면 이스케이프 전에는 그 둘째 줄이 대상 절 판정을
    # 통과해 가짜가 맥락을 번역 대상으로 착각했다.
    segments = [
        _multiline_seg(0, "맥락 첫 줄\n맥락 둘째 줄"),
        _multiline_seg(1, "번역 대상"),
    ]
    provider = EchoProvider()
    result = translate_segments(
        segments,
        provider=provider,
        source_lang="ko",
        target_lang="en",
        batch_size=1,
        context_window=1,
    )
    assert result.failures == ()
    assert [s.target_text for s in result.segments] == [
        "EN:맥락 첫 줄\n맥락 둘째 줄",
        "EN:번역 대상",
    ]


# --------------------------------------------------------------------------
# 가짜가 Provider 계약을 지키는가 (요구 B)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fake", [ScriptedProvider, EchoProvider])
def test_가짜_프로바이더가_Provider_시그니처를_지킨다(fake: type) -> None:
    """`Provider`는 `@runtime_checkable`이 아니고 CI에 타입 검사기도 없다.

    그래서 `*`를 빠뜨렸거나 인자 이름이 어긋난 가짜도 엔진의 키워드 호출에서는
    정상 동작해 전부 통과한다 - 가장 흔한 이탈이 가장 안 잡힌다. 이 단언이
    이 저장소에서 프로토콜 준수를 강제하는 유일한 수단이다.

    `inspect.signature` 비교는 인자 이름·키워드 전용 여부·기본값·주석을
    전부 본다. 기본값까지 보는 것이 요점이다 - 프로토콜의 `max_tokens`에는
    기본값이 없으므로 가짜가 `= None`을 붙이면 여기서 죽는다.
    """
    assert inspect.signature(fake.complete) == inspect.signature(Provider.complete)
