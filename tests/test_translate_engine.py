"""실행 엔진 - 배치, 폴백, 재시도 (FR-2.6)."""

from __future__ import annotations

import inspect
import json
from dataclasses import replace

import pytest
from tests.fakes.provider import EchoProvider, ScriptedProvider

from cuesift.glossary import Glossary, GlossaryEntry
from cuesift.progress import ProgressUpdate
from cuesift.retry import MAX_BACKOFF_S
from cuesift.segment.models import Segment
from cuesift.translate.engine import translate_segments
from cuesift.translate.provider import (
    Completion,
    FatalProviderError,
    Provider,
    ProviderError,
    RetryableProviderError,
    TokenUsage,
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


def _expected_usage(provider: EchoProvider | ScriptedProvider) -> TokenUsage:
    """가짜가 **실제로 돌려준** 사용량의 합.

    기대값을 가짜의 산식과 중복 구현하지 않으려고 돌려준 것에서 되짚는다.
    이것만으로는 "가짜가 0을 내고 엔진도 0을 낸다"를 구별하지 못하므로
    호출부에서 `> 0`을 함께 단언한다.
    """
    total = TokenUsage()
    for completion in provider.returned:
        total = total + completion.usage
    return total


def _sections(user_content: str) -> dict[str, list[str]]:
    """유저 메시지를 `## ` 절로 쪼개 **절별 id 목록**을 낸다.

    `"[3]" in body` 형태로는 앞뒤 맥락이 **통째로 뒤바뀌어도** 통과한다 -
    두 절 다 같은 본문 안에 있기 때문이다. FR-2.2의 요점은 "몇 개를 붙이나"가
    아니라 "앞의 것을 앞에 붙이나"이고, 자막에서 앞뒤 역전은 대명사·시제·
    존대를 직접 망친다. 그래서 소속 절까지 함께 본다.
    """
    out: dict[str, list[str]] = {}
    current = ""
    for line in user_content.splitlines():
        if line.startswith("## "):
            # 헤더는 "## 앞 맥락 - 번역하지 말 것"처럼 꼬리가 붙는다.
            current = line.split(" - ")[0].strip()
            out.setdefault(current, [])
        elif line.startswith("[") and current:
            out[current].append(line.partition("]")[0][1:])
    return out


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
    # 호출 횟수만 보면 안 된다. NFR-2에서 사용자가 보는 숫자는 토큰 수이고,
    # 엔진이 토큰을 통째로 버려도 calls는 맞는다.
    assert result.usage == _expected_usage(provider)
    assert result.usage.prompt_tokens > 0
    assert result.usage.completion_tokens > 0


def test_기본_호출_인자를_그대로_넘긴다() -> None:
    # max_tokens를 값으로 단언하는 곳이 없으면 엔진이 max_tokens=16을
    # 보내도 아무도 모른다 - 잘린 응답은 여전히 유효 JSON이고 개수·번호도
    # 맞아 parse_translations를 통과하므로 **잘린 자막이 성공으로 보고된다.**
    provider = EchoProvider()
    translate_segments(
        _segs(25), provider=provider, source_lang="ko", target_lang="en", batch_size=10
    )
    assert provider.kwargs == [(0.0, None)] * 3


def test_빈_입력은_호출하지_않는다() -> None:
    provider = EchoProvider()
    result = translate_segments([], provider=provider, source_lang="ko", target_lang="en")
    assert provider.calls == []
    assert result.segments == ()
    assert result.usage.calls == 0


def test_temperature를_그대로_넘긴다() -> None:
    # WP8 자가일관성이 의도적으로 올려 쓴다 (설계 §8.2).
    #
    # 가짜가 kwargs를 기록하므로 인라인 서브클래스가 필요 없다. 그 서브클래스는
    # **주석 없는 시그니처**라 요구 B의 `inspect.signature` 단언 대상이 아니었고,
    # 프로토콜 준수를 강제하는 유일한 수단을 우회한 더블이 검증을 떠받치고 있었다.
    provider = EchoProvider()
    translate_segments(
        _segs(1), provider=provider, source_lang="ko", target_lang="en", temperature=0.9
    )
    assert provider.kwargs == [(0.9, None)]


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
    assert result.usage == _expected_usage(provider)
    assert result.usage.prompt_tokens > 0
    assert result.usage.completion_tokens > 0


def test_폴백_개별_호출의_파싱_실패도_사용량에_남는다() -> None:
    # 배치 경로의 같은 성질은 위 테스트가 지킨다. 폴백 개별 호출도 응답을
    # 받았으니 요금이 나갔다 - 같은 논리인데 짝이 없었다.
    provider = ScriptedProvider(["산문", "산문", _ok([1])])
    result = translate_segments(
        _segs(2), provider=provider, source_lang="ko", target_lang="en", batch_size=2
    )
    assert [f.segment_id for f in result.failures] == ["s0"]
    # 깨진 배치 1회 + 실패한 개별 1회 + 성공한 개별 1회가 모두 계상된다.
    assert result.usage.calls == 3
    assert result.usage == _expected_usage(provider)
    assert result.usage.completion_tokens > 0


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
    # 폴백 호출에 맥락이 **절별로** 실렸다. `in` 검사만 두면 맥락을 통째로
    # 버리는 구현에서도, 앞뒤를 맞바꾼 구현에서도 통과한다.
    assert _sections(_user_content(provider, 2)) == {
        "## 앞 맥락": ["0", "1"],
        "## 번역 대상": ["2"],
        "## 뒤 맥락": ["3", "4"],
    }
    assert _sections(_user_content(provider, 3)) == {
        "## 앞 맥락": ["1", "2"],
        "## 번역 대상": ["3"],
        "## 뒤 맥락": ["4", "5"],
    }


def test_맥락_윈도우가_파일보다_커도_폴백_맥락이_옳다() -> None:
    """앞에 있는 세그먼트 수보다 `context_window`가 큰 경우다.

    `local[max(0, pos - cw) : pos]`에서 `max(0, ...)`를 지워도 **대부분의
    조합에서는 결과가 같다** - 음수 시작이 stop보다 뒤라 슬라이스가 비기
    때문이다. 실제로 달라지는 것은 여기처럼 파일이 짧고 윈도우가 클 때이고,
    그때 s1은 앞 맥락 `[0]`을 통째로 잃는다(전수 조사에서 1,704쌍).

    `_fallback_individually`가 `context_window`를 **인자로 받는** 이유이기도
    하다. `max(len(before), len(after))`로 되짚으면 이 구성에서 둘 다 비어
    0이 되고, 그러면 s0도 s1을 맥락으로 받지 못한다.
    """
    provider = ScriptedProvider(["산문", _ok([0]), _ok([1])])
    result = translate_segments(
        _segs(2),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        batch_size=2,
        context_window=2,
    )
    assert len(provider.calls) == 3
    assert result.failures == ()
    # 배치 호출에는 맥락이 없다 - 파일에 다른 세그먼트가 없기 때문이다.
    assert _sections(_user_content(provider, 0)) == {"## 번역 대상": ["0", "1"]}
    # 폴백에서는 서로가 서로의 맥락이 된다.
    assert _sections(_user_content(provider, 1)) == {
        "## 번역 대상": ["0"],
        "## 뒤 맥락": ["1"],
    }
    assert _sections(_user_content(provider, 2)) == {
        "## 앞 맥락": ["0"],
        "## 번역 대상": ["1"],
    }


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


def test_배치가_실패해도_남은_배치를_계속_돈다() -> None:
    """FR-2.6의 "해당 세그먼트만 표시 후 진행"이 이 모듈의 존재 이유다.

    실패를 담은 테스트가 전부 **단일 배치**면 두 결함이 동시에 열린다.

    1. 첫 실패 배치에서 루프를 끊어도(`if batch_failures: break`) 아무도
       모른다. 800큐 파일에서 20번째가 비면 나머지 780개가 사라지는데
       `failures`는 1건이라 **조용히** 끝난다.
    2. `failures.extend(...)`를 `failures = list(...)`로 바꿔도 모른다.
       `translated.update`는 다중 배치 테스트가 지키는데 `failures`만 짝이
       없었다. 검수 트리아지 엔진에서 실패 목록 누락은 안전한 실패가 아니다 -
       **사람이 봐야 할 자막이 큐에서 빠진다.**
    """
    # 두 배치 모두에 빈 번역을 하나씩 넣는다.
    first = json.dumps(
        {
            "translations": [
                {"id": 0, "text": ""},
                {"id": 1, "text": "EN1"},
                {"id": 2, "text": "EN2"},
            ]
        }
    )
    second = json.dumps(
        {
            "translations": [
                {"id": 3, "text": "EN3"},
                {"id": 4, "text": "   "},
                {"id": 5, "text": "EN5"},
            ]
        }
    )
    provider = ScriptedProvider([first, second])
    result = translate_segments(
        _segs(6), provider=provider, source_lang="ko", target_lang="en", batch_size=3
    )
    # 첫 배치가 실패를 냈어도 둘째 배치를 불렀다.
    assert len(provider.calls) == 2
    # 두 배치의 실패가 **모두** 남는다.
    assert [f.segment_id for f in result.failures] == ["s0", "s4"]
    # 뒤 배치의 성공분이 실제로 채워졌다.
    assert [s.target_text for s in result.segments] == [
        None,
        "EN1",
        "EN2",
        "EN3",
        None,
        "EN5",
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


def test_재시도가_소진되면_그_배치만_전원_실패한다() -> None:
    """소진 테스트가 전부 **맥락이 빈 구성**이면 실패 명단이 검사되지 않는다.

    `for s in window.batch`를 `for s in (*window.before, *window.batch)`로
    바꿔도 죽는 테스트가 없었다. 그 변이본에서는 맥락 세그먼트가
    **`target_text`가 채워져 있으면서 동시에 실패**로 실린다 - `failures`와
    `segments`가 서로 다른 말을 한다. 게다가 실패 개수 부풀림은
    `review_ratio()`를 통해 **Recall@Budget 배수를 직접 오염시킨다.**
    """
    # 배치 [0,1,2]는 성공, 배치 [3,4,5]만 소진, 배치 [6,7,8]은 다시 성공.
    provider = ScriptedProvider(
        [_ok([0, 1, 2])] + [RetryableProviderError("503")] * 4 + [_ok([6, 7, 8])]
    )
    result = translate_segments(
        _segs(9),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        batch_size=3,
        context_window=3,
        max_retries=3,
        sleep=lambda _s: None,
    )
    # 성공 1 + (최초 1회 + 재시도 3회) + 성공 1 = 6회.
    # **개별 폴백은 타지 않는다** (설계 §6.3) - 탔다면 3회가 더 붙는다.
    assert len(provider.calls) == 6
    # 맥락(0~2·6~8)은 실패 명단에 없다.
    assert [f.segment_id for f in result.failures] == ["s3", "s4", "s5"]
    assert [f.reason for f in result.failures] == ["provider_error"] * 3
    # 실패한 것에는 번역문이 없고, 앞뒤 배치는 살아 있다 (FR-2.6).
    assert [s.target_text for s in result.segments] == [
        "EN0",
        "EN1",
        "EN2",
        None,
        None,
        None,
        "EN6",
        "EN7",
        "EN8",
    ]
    # 소진 경로는 응답을 한 번도 받지 못했으므로 그 배치의 토큰은 0이다.
    # 성공한 두 배치분만 남는다 ("실패한 호출은 계상하지 않는다"의 고정).
    assert result.usage.calls == 2
    assert result.usage == _expected_usage(provider)


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
    below = MAX_BACKOFF_S - 0.5
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
    assert waited == [MAX_BACKOFF_S]


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
    assert waited == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, MAX_BACKOFF_S, MAX_BACKOFF_S]
    assert max(waited) == MAX_BACKOFF_S


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
    # 절별 id 목록으로 본다. `"[2]" in body` 형태는 앞뒤 맥락이 **통째로
    # 뒤바뀌어도** 통과한다 - 둘 다 같은 본문 안이기 때문이다.
    sections = _sections(_user_content(provider, 1))
    assert sections["## 앞 맥락"] == [str(i) for i in range(3 - context_window, 3)]
    assert sections["## 번역 대상"] == ["3", "4", "5"]
    assert sections["## 뒤 맥락"] == [str(i) for i in range(6, 6 + context_window)]


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


def test_폴백_호출에도_용어집이_실린다() -> None:
    # 폴백은 구조가 복제된 두 번째 호출부라 검사가 통째로 빠지기 쉽다.
    # FR-2.3이 하필 **모델이 이미 형식을 어긴 자리**에서 빠지면 안 된다.
    #
    # 세 세그먼트 **모두**에 용어가 나오게 짠다. 한 세그먼트에만 두면
    # 나머지 폴백 호출에는 용어집 절이 정당하게 빠지므로(등장하지 않는
    # 용어는 주입하지 않는 것이 FR-2.3이다) 단언이 그 자리를 못 가린다.
    segments = [
        Segment(
            id=f"g{i}",
            index=i,
            start_ms=i * 1000,
            end_ms=i * 1000 + 900,
            source_text=f"기후 변화 이야기 {i}",
        )
        for i in range(3)
    ]
    glossary = Glossary(entries=(GlossaryEntry(source="기후 변화", targets=("climate change",)),))
    provider = EchoProvider(fail_batches_of_size=2)
    translate_segments(
        segments,
        provider=provider,
        source_lang="ko",
        target_lang="en",
        batch_size=3,
        glossary=glossary,
    )
    # 0번은 깨진 배치 호출이다. 1번부터가 개별 폴백이다.
    assert len(provider.calls) == 4
    for call_index in (1, 2, 3):
        assert "climate change" in provider.calls[call_index][0].content


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


def test_폴백_호출에도_작품_맥락이_실린다() -> None:
    provider = EchoProvider(fail_batches_of_size=2)
    translate_segments(
        _segs(3),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        batch_size=3,
        work_context="기후 위기에 대한 TED 강연이다",
    )
    assert len(provider.calls) == 4
    for call_index in (1, 2, 3):
        assert "기후 위기에 대한 TED 강연이다" in provider.calls[call_index][0].content


def test_폴백_호출에도_temperature가_그대로_간다() -> None:
    # 폴백에서 0.0으로 고정되면 WP8 자가일관성(설계 §8.2 - N회 개별 호출로
    # 온도를 올려 쓴다)이 하필 그 구간에서 무력화된다.
    provider = EchoProvider(fail_batches_of_size=2)
    translate_segments(
        _segs(3),
        provider=provider,
        source_lang="ko",
        target_lang="en",
        batch_size=3,
        temperature=0.7,
    )
    assert [t for t, _ in provider.kwargs] == [0.7, 0.7, 0.7, 0.7]


def test_언어쌍이_프롬프트에_실린다() -> None:
    # 문장으로 단언한다. `"ko" in system` + `"ja" in system`은 방향이
    # 뒤바뀐 프롬프트("ja 자막을 ko로")에서도 **양쪽 다 참**이라 통과한다.
    # 함께 있던 `result.target_lang == "ja"`도 인자를 되돌려준 값이라
    # 프롬프트를 전혀 보지 않는다. Q2가 ko->en/ja로 확정돼 있어 방향이
    # 뒤집히면 모델이 대체로 뭔가를 돌려주고 전 계층을 통과한다.
    provider = EchoProvider()
    result = translate_segments(_segs(1), provider=provider, source_lang="ko", target_lang="ja")
    system = provider.calls[0][0].content
    assert "ko 자막을 ja로 번역한다" in system
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

    `name`도 함께 본다. 프로토콜의 멤버는 `name`과 `complete` **둘**인데
    `complete`만 검사하면 "유일한 수단"이라는 이 독스트링의 선언이 반쪽이
    된다 - 실제로 `name` 줄을 통째로 지워도 죽는 테스트가 없었다.
    """
    assert inspect.signature(fake.complete) == inspect.signature(Provider.complete)
    assert isinstance(getattr(fake, "name", None), str)


# --------------------------------------------------------------------------
# Provider 계약 위반 — 독스트링의 표를 검증 가능하게 만든다
#
# `Provider.complete`의 독스트링이 "이렇게 구현하면 이렇게 깨진다"를 표로
# 적었다. **선언에 테스트가 없으면 그것은 계약이 아니라 주석이다.**
# 아래 둘은 현재 동작을 고정하는 특성 테스트다 - 누군가 engine에 방어를
# 넣으면 여기가 죽어 독스트링을 함께 고치게 만든다.
# --------------------------------------------------------------------------


def test_기반_ProviderError는_재시도도_폴백도_없이_샌다() -> None:
    """서드파티가 `ProviderError`를 직접 던지면 engine이 받지 못한다.

    `_call_with_retry`가 `RetryableProviderError`·`FatalProviderError` 두
    자손만 잡기 때문이다. `ProviderError`가 "호출부가 전부를 한 번에 잡을 수
    있게 한다"는 것은 **호출부가 그것을 잡을 때** 성립하는 말인데, 주 호출부인
    engine은 잡지 않는다.
    """

    class 기반예외프로바이더:
        name = "bare"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, *, temperature, max_tokens):  # noqa: ANN001, ANN202
            self.calls += 1
            raise ProviderError("기반 클래스를 직접 던진다")

    provider = 기반예외프로바이더()
    with pytest.raises(ProviderError):
        translate_segments(
            _segs(3),
            provider=provider,
            source_lang="ko",
            target_lang="en",
            sleep=lambda _s: None,
        )
    # 재시도도 폴백도 하지 않았다. 실패 1회가 곧 실행 전체의 죽음이다.
    assert provider.calls == 1


def test_Completion_text가_None이면_ProviderError_밖에서_죽는다() -> None:
    """`text`는 반드시 `str`이라는 계약이 지켜지지 않으면 폴백이 받지 못한다."""

    class None텍스트프로바이더:
        name = "none"

        def complete(self, messages, *, temperature, max_tokens):  # noqa: ANN001, ANN202
            return Completion(text=None, usage=TokenUsage(calls=1))  # type: ignore[arg-type]

    with pytest.raises(AttributeError) as exc:
        translate_segments(
            _segs(3),
            provider=None텍스트프로바이더(),
            source_lang="ko",
            target_lang="en",
            sleep=lambda _s: None,
        )
    # ProviderError 밖이라는 것이 요점이다 - engine의 폴백 두 절이 못 받는다.
    assert not isinstance(exc.value, ProviderError)


def test_진행_콜백이_최종적으로_전량을_보고한다() -> None:
    # 진행이 100%에 도달하지 않으면 사용자는 멈춘 것과 구별하지 못한다.
    events: list[ProgressUpdate] = []
    translate_segments(
        _segs(25),
        provider=EchoProvider(),
        source_lang="ko",
        target_lang="en",
        batch_size=10,
        on_progress=events.append,
    )
    assert len(events) == 3
    assert events[-1] == ProgressUpdate(25, 25)


def test_진행_콜백이_맥락을_함께_세지_않는다() -> None:
    # `BatchWindow`는 `batch`·`before`·`after` 셋을 갖는데 뒤 둘은 맥락이지
    # 번역 대상이 아니다. 더하면 done이 total을 넘고, 다음 배치에서
    # 줄어든 것처럼 보인다.
    events: list[ProgressUpdate] = []
    translate_segments(
        _segs(25),
        provider=EchoProvider(),
        source_lang="ko",
        target_lang="en",
        batch_size=10,
        context_window=3,
        on_progress=events.append,
    )
    assert [e.done for e in events] == [10, 20, 25]
    assert all(e.total == 25 for e in events)


def test_빈_입력은_진행도_내지_않는다() -> None:
    # `test_빈_입력은_호출하지_않는다`의 형제다. 배치가 0개면 이벤트도 0개다.
    events: list[ProgressUpdate] = []
    translate_segments(
        [],
        provider=EchoProvider(),
        source_lang="ko",
        target_lang="en",
        on_progress=events.append,
    )
    assert events == []


def test_콜백을_주지_않으면_기존_호출부가_그대로다() -> None:
    # 기본값이 None이고 그때 콜백은 **한 번도 호출되지 않는다**(설계 D3).
    # 기존 호출부 0줄 변경이 이 결정의 산물이다.
    result = translate_segments(
        _segs(3), provider=EchoProvider(), source_lang="ko", target_lang="en"
    )
    assert [s.target_text for s in result.segments] == ["EN:문장0", "EN:문장1", "EN:문장2"]


def test_stt_출처_플래그가_번역_결과까지_실려_온다() -> None:
    """`replace(s, target_text=...)`가 `source_from_stt`를 나르는 것이 **이
    기능의 유일한 살아 있는 전달 경로**인데, 오늘 그 줄을 지나는 테스트가
    하나도 없었다.

    e2e는 `TranslationResult`를 손으로 만들어 이 줄을 우회하고,
    `bench/track_io.py`가 이미 명시 조립으로 필드를 떨어뜨리는 전례다.
    누군가 여기를 같은 형태로 바꾸면 `review.json`이 조용히
    **"자막 파일이었다"**고 말한다 - 거짓이 예외 없이 나가는 부류다.
    """
    segments = [replace(s, source_from_stt=True) for s in _segs(2)]
    result = translate_segments(
        segments,
        provider=EchoProvider(),
        source_lang="ko",
        target_lang="en",
    )
    assert all(s.source_from_stt for s in result.segments), (
        "번역 결과가 STT 출처 플래그를 떨어뜨렸다"
    )
    # 반대 방향도 고정한다 - 무조건 True로 채우는 구현이면 이것이 빨개진다.
    plain = translate_segments(
        _segs(2), provider=EchoProvider(), source_lang="ko", target_lang="en"
    )
    assert not any(s.source_from_stt for s in plain.segments)
