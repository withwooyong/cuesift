"""`load_media`의 `IngestResult` 합성 (FR-1.2 · 설계 D5·D6·§4.4)."""

from __future__ import annotations

import inspect
from pathlib import Path

import pysubs2
import pytest
from tests.fakes.stt import FakeSttProvider

from cuesift.ingest import IngestError, load_input, load_media, load_subtitle, write_subtitle
from cuesift.risk import fuse
from cuesift.segment import Signal
from cuesift.signals import SignalContext, collect_all
from cuesift.spec import load_builtin
from cuesift.stt.provider import SttProvider
from cuesift.translate.provider import FatalProviderError
from cuesift.triage import review_ratio, select_by_budget

# **이 데이터가 D5 게이트의 분해능이다.** 값이 전부 `.5` tie면 짝수 반올림이
# 내림과 같은 값을 내서 `floor` 변이도, **`start`만 내리고 `end`는 반올림하는
# 비대칭 변이**(= D5가 막으려던 바로 그것)도 통째로 생존한다 - 실제로 첫 판에서
# 그랬다. 네 값이 각각 다른 것을 막는다 (전부 실측).
#
# | 경계 | `* 1000` | round | floor | ceil | 이 값이 잡는 변이 |
# | --- | --- | --- | --- | --- | --- |
# | `1.2345` | `1234.5` | 1234 | 1234 | 1235 | `ceil` · **짝수 반올림 증거(내림 tie)** |
# | `2.6667` | `2666.7…` | 2667 | 2666 | 2667 | **`floor`와 비대칭** |
# | `3.7775` | `3777.5` | 3778 | 3777 | 3778 | `floor` · **올림 tie** |
#
# 두 번째 큐의 앞뒤 공백은 `cue.text.strip()`을 지우는 변이를 잡는다.
CUES = [
    (0.0, 1.2345, "안녕하세요"),
    (1.2345, 2.6667, "  반갑습니다  "),
    (2.6667, 3.7775, "고맙습니다"),
    (3.7775, 5.0, "감사합니다"),
]
TARGETS = ["Hello", "Nice to meet you", "Thank you", "Thanks"]

# `tests/test_bench_measure.py`가 쓰는 것과 같은 조합이다 - 신호 수집이 실제
# 파이프라인과 같은 컨텍스트에서 돌아야 D8 게이트가 실물을 잰다.
CTX = SignalContext(
    profile=load_builtin("ted-en"), glossary=None, source_lang="ko", target_lang="en"
)


def _media(tmp_path: Path) -> Path:
    p = tmp_path / "talk.mp4"
    p.write_bytes(b"fake video")
    return p


def test_영상에서_세그먼트를_만든다(tmp_path: Path) -> None:
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert len(result.segments) == 4
    assert result.segments[0].source_text == "안녕하세요"
    assert result.source_path == _media(tmp_path)


def test_모든_세그먼트에_플래그가_붙는다(tmp_path: Path) -> None:
    # FR-1.4 - STT 원문은 전부 검수 필요 표시를 받는다.
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert all(seg.source_from_stt for seg in result.segments)


def test_id_규칙이_자막_경로와_같다(tmp_path: Path) -> None:
    # `_to_segments`와 같은 `f"{index:05d}"`, 0부터 연속.
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert [s.id for s in result.segments] == ["00000", "00001", "00002", "00003"]
    assert [s.index for s in result.segments] == [0, 1, 2, 3]


def test_초를_밀리초로_반올림한다(tmp_path: Path) -> None:
    """D5 - **기대값을 half-up으로 적으면 안 된다.**

    파이썬의 `round()`는 짝수 반올림이라 `1.2345 * 1000 = 1234.5`가
    **1234**(내림), `3.7775 * 1000 = 3777.5`가 **3778**(올림)이 된다(실측).
    half-up으로 1235를 기대하면 이 게이트 자신이 틀린다.

    **`2.6667`이 `floor` 변이를 잡는 유일한 값이다** - 나머지가 `.5` tie라
    내림과 반올림이 같아진다.
    """
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert [(s.start_ms, s.end_ms) for s in result.segments] == [
        (0, 1234),  # 내림 tie
        (1234, 2667),  # floor라면 2666
        (2667, 3778),  # 올림 tie. floor라면 3777
        (3778, 5000),
    ]


def test_인접_큐의_경계가_붙어_있다(tmp_path: Path) -> None:
    """D5의 목적. 한쪽만 내리고 한쪽만 올리면 원본에 없던 겹침을 우리가 만든다.

    `strict=False`인 `zip`은 세그먼트가 1개 이하면 **공허하게 통과**한다.
    아래 개수 단언이 그 상태에서 이 게이트가 초록을 내는 것을 막는다.
    """
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert len(result.segments) >= 2
    for prev, nxt in zip(result.segments, result.segments[1:], strict=False):
        assert prev.end_ms == nxt.start_ms


def test_format이_srt다(tmp_path: Path) -> None:
    """§4.4 - `subs.format`이 아니라 `IngestResult.format`이다.

    합성한 `SSAFile`의 `.format`은 이벤트를 넣어도 `None`으로 남고(실측),
    `writer.py`가 그 값을 `save(format_=)`로 넘겨 `.tmp`에서 죽는다.
    """
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert result.format == "srt"


def test_event_index가_항등_사상이다(tmp_path: Path) -> None:
    """§4.4 - STT 경로는 필터가 없으므로 원본 위치가 곧 순서다."""
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert result.event_index == {"00000": 0, "00001": 1, "00002": 2, "00003": 3}


def test_subs가_큐를_그대로_담는다(tmp_path: Path) -> None:
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert isinstance(result.subs, pysubs2.SSAFile)
    assert len(result.subs.events) == 4
    assert result.subs.events[0].plaintext == "안녕하세요"
    assert result.subs.events[0].end == 1234


def test_원문의_앞뒤_공백을_지운다(tmp_path: Path) -> None:
    """`cue.text.strip()`을 `cue.text`로 되돌리는 변이를 잡는다.

    공백이 남으면 CPS의 분자가 부풀고, 그 오탐은 hard fail이라
    FR-6.2에 따라 검수 예산을 우회한다.
    """
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert result.segments[1].source_text == "반갑습니다"


def test_오버라이드_태그는_원문_길이에_세지_않는다(tmp_path: Path) -> None:
    """`source_text`는 `event.plaintext`에서 받는다 - 자막 경로와 같은 불변식이다.

    `load_subtitle`은 `source_text=event.plaintext`라 오버라이드 블록이 빠진
    상태다. 원문을 그대로 넣으면 같은 내용이 두 경로에서 **9자 vs 2자**로
    갈리고(실측), CPS가 4.5배로 부푼다 - **그 오탐은 hard fail이라 FR-6.2에
    따라 검수 예산을 우회해 Recall@Budget 지표 자체를 무너뜨린다.**

    게다가 `writer.py`의 `_LEADING_OVERRIDES`가 `{music}`을 위치 태그로 오인해
    번역문 앞에 다시 붙이는데, SRT 저장 때 pysubs2가 지워 **예외도 경고도 없다.**
    """
    result = load_media(_media(tmp_path), FakeSttProvider([(0.0, 1.0, "{music}안녕")]))
    assert result.segments[0].source_text == "안녕"
    # 태그는 `subs`에 남는다 - 자막 경로에서 원본 파일에 남아 있는 것과 같다.
    assert result.subs.events[0].text == "{music}안녕"


def test_줄바꿈이_SSA_규약으로_들어간다(tmp_path: Path) -> None:
    """SSA의 줄바꿈은 실제 개행이 아니라 `\\N`이다.

    `SSAEvent(text=...)`로 직접 넣으면 raw 개행이 담긴다. `format`이 `"srt"`
    고정인 지금은 무해하지만 ass로 저장하면 `Dialogue:` 줄이 물리적으로 쪼개져
    **파일이 깨진다.** `plaintext` setter가 그 변환을 한다.
    """
    result = load_media(_media(tmp_path), FakeSttProvider([(0.0, 1.0, "가\n나")]))
    assert result.subs.events[0].text == "가\\N나"
    assert result.segments[0].source_text == "가\n나"


def test_write_subtitle_왕복이_성립한다(tmp_path: Path) -> None:
    """**이 스위트에서 두 번째로 중요한 게이트다** (위험 R3).

    번역 자막을 못 쓰면 이 작업 전체가 무의미하다. `format`이 `None`이면
    `.tmp`에서, `event_index`가 비면 `KeyError`로 죽는다 - 둘 다 실측했다.

    **쓴 파일을 다시 인제스트하는 것까지가 왕복이다.** 저장까지만 보면
    "우리가 쓴 파일을 우리가 못 읽는" 회귀가 이 게이트를 그냥 지나간다.
    """
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    for seg, en in zip(result.segments, TARGETS, strict=True):
        seg.target_text = en
    out = tmp_path / "talk.en.srt"
    write_subtitle(result, result.segments, out)

    body = out.read_text(encoding="utf-8")
    assert body.count("-->") == 4
    assert "Hello" in body
    assert "안녕하세요" not in body
    assert "00:00:01,234" in body

    reread = load_subtitle(out)
    assert [s.source_text for s in reread.segments] == TARGETS
    assert [(s.start_ms, s.end_ms) for s in reread.segments] == [
        (s.start_ms, s.end_ms) for s in result.segments
    ]


def test_source_lang은_선언값을_유지한다(tmp_path: Path) -> None:
    """**응답의 `language`로 덮지 않는다** (FR-1.5 · 컨트롤러 룰링).

    `transcript.language`의 값 도메인은 정의돼 있지 않다 - 백엔드가
    `"korean"`·`"ko"`·`"Korean"`을 제각각 낸다(§12 Q3). `signals/structural.py`의
    `_SCRIPT_RANGES` 키는 정확히 `ko`·`ja` 둘뿐이라 `"korean"`이 실리면
    `.get()`이 `None`을 내고 **미번역 신호가 예외 없이 꺼진다**(실측).
    미탐이 늘어 Recall@Budget이 조용히 내려간다.
    """
    provider = FakeSttProvider(CUES, language="korean")
    result = load_media(_media(tmp_path), provider, source_lang="ja")
    assert result.source_lang == "ja"


def test_language가_없어도_선언값을_쓴다(tmp_path: Path) -> None:
    # 백엔드가 그 필드를 안 낼 수 있다 (§12 Q3). `None`이 새어 나가면 안 된다.
    result = load_media(_media(tmp_path), FakeSttProvider(CUES, language=None), source_lang="ko")
    assert result.source_lang == "ko"


def test_큐가_0개면_입력_오류다(tmp_path: Path) -> None:
    # "0개 수집은 통과가 아니라 입력 오류다."
    # 프로바이더가 빈 배열을 막으므로(D4) 여기 오는 것은 전부 표시 불가 큐다.
    with pytest.raises(IngestError) as exc:
        load_media(_media(tmp_path), FakeSttProvider([(0.0, 1.0, "   ")]))
    assert exc.value.reason == "empty"


def test_없는_파일은_not_found다(tmp_path: Path) -> None:
    with pytest.raises(IngestError) as exc:
        load_media(tmp_path / "없다.mp4", FakeSttProvider(CUES))
    assert exc.value.reason == "not_found"


def test_프로바이더의_치명적_오류는_그대로_올라간다(tmp_path: Path) -> None:
    """`IngestError`로 감싸지 않는다.

    감싸면 CLI가 "자막 파일이 잘못됐다"로 보고하는데 실제 원인은 STT 백엔드다.
    호출부는 두 예외를 각각 다른 종료 코드로 바꾼다.
    """
    provider = FakeSttProvider(CUES, error=FatalProviderError("verbose_json 미지원"))
    with pytest.raises(FatalProviderError, match="verbose_json"):
        load_media(_media(tmp_path), provider)


def test_프로바이더에_경로와_언어를_넘긴다(tmp_path: Path) -> None:
    """**언어 힌트까지 단언한다.**

    경로만 보면 `language=None`으로 바꾸는 변이가 스위트 전체에서 생존한다 -
    키워드를 지우면 `TypeError`로 죽지만 **값이 틀린 것**은 아무도 안 본다.
    Whisper 계열에서 언어 힌트는 전사 결과를 실질적으로 바꾼다.
    """
    provider = FakeSttProvider(CUES)
    media = _media(tmp_path)
    load_media(media, provider, source_lang="ja")
    assert provider.calls == [media]
    assert provider.languages == ["ja"]


def test_가짜_프로바이더가_프로토콜과_같은_시그니처다() -> None:
    """`SttProvider`가 `@runtime_checkable`이 아니고 CI에 타입 검사기도 없다.

    어긋난 가짜도 `load_media`의 키워드 호출에서는 정상 동작해 조용히 통과한다.
    형제 넷(`test_translate_engine.py`·`test_translate_openai_compat.py`·
    `test_store_provider.py`·`test_stt_provider.py`)이 전부 이 단언을 갖고 있고,
    `tests/fakes/provider.py`의 독스트링이 그것을 **"이 저장소에서 그 이탈을
    잡는 유일한 수단"**이라고 적었다.

    `name`까지 보는 이유는 그 줄을 지워도 죽는 테스트가 없기 때문이다 -
    `load_media`의 `empty` 오류 메시지가 실제로 그것을 쓴다.
    """
    assert inspect.signature(FakeSttProvider.transcribe) == inspect.signature(
        SttProvider.transcribe
    )
    assert isinstance(getattr(FakeSttProvider, "name", None), str)


def test_빈_텍스트_큐를_걸러낸다(tmp_path: Path) -> None:
    """공백만 있는 큐는 화면에 아무것도 안 띄운다.

    남기면 CPS가 0으로 계산돼 규격 검사가 무의미한 세그먼트가 큐에 낀다.
    **거르고 나서 index를 0부터 다시 부여한다** - 자막 경로의
    `_keep_displayed` + `_to_segments`와 같은 규칙이다.
    """
    cues = [(0.0, 1.0, "가"), (1.0, 2.0, "   "), (2.0, 3.0, "나")]
    result = load_media(_media(tmp_path), FakeSttProvider(cues))
    assert [s.source_text for s in result.segments] == ["가", "나"]
    assert [s.index for s in result.segments] == [0, 1]
    # 걸러낸 뒤에도 `subs`와 `event_index`가 세그먼트와 짝이 맞아야 한다.
    assert len(result.subs.events) == 2
    assert result.event_index == {"00000": 0, "00001": 1}


def test_밀리초로_변환되지_않는_타임코드는_입력_오류다(tmp_path: Path) -> None:
    """`OverflowError`가 `IngestError`·`ProviderError` 밖으로 새던 자리다 (실측).

    `TranscriptCue`는 `1e308`을 통과시킨다 - 유한하고 음수가 아니며 역전도
    아니다. 그런데 `1e308 * 1000`은 `inf`가 되고 `round(inf)`가
    `OverflowError: cannot convert float infinity to integer`를 낸다.
    잡지 않으면 미처리 traceback이 되어 **종료 코드 1**로 나가는데, 이
    저장소에서 1은 "규격 위반 발견"이라 **STT 백엔드 결함이 자막 결함으로
    오보된다.** JSON이 `1e308`을 float으로 파싱하는 것은 백엔드 사정이고
    우리 통제 밖이다 (§12 Q3 - 능력이 균일하지 않다).
    """
    with pytest.raises(IngestError) as exc:
        load_media(_media(tmp_path), FakeSttProvider([(0.0, 1e308, "가")]))
    assert exc.value.reason == "bad_timecode"


def _subtitle(tmp_path: Path) -> Path:
    p = tmp_path / "talk.srt"
    p.write_text("1\n00:00:00,000 --> 00:00:02,000\n자막에서 왔다\n\n", encoding="utf-8")
    return p


def test_둘_다_주어지면_자막을_채택한다(tmp_path: Path) -> None:
    """FR-1.3. **STT를 부르지 않는 것까지가 계약이다.**

    부르고 버리면 사용자가 쓰지도 않을 전사에 돈과 시간을 낸다.
    **`provider.calls == []`가 그 계약의 유일한 게이트다** - 이 줄을 지우면
    "부르고 나서 버리는" 구현이 나머지 두 단언을 그대로 통과한다(실측).
    """
    provider = FakeSttProvider(CUES)
    result = load_input(subtitle=_subtitle(tmp_path), media=_media(tmp_path), provider=provider)
    assert result.segments[0].source_text == "자막에서 왔다"
    assert provider.calls == []
    # **이 줄은 위 두 단언과 다른 변이를 잡는다**(실측). 자막 경로
    # (`_to_segments`)가 `source_from_stt=True`를 켜는 변이를 전체 스위트에서
    # **이 단언 하나만** 죽인다 - 지우면 그 변이가 나머지 전부를 통과한다(실측).
    assert not any(seg.source_from_stt for seg in result.segments)


def test_자막만_주어지면_자막을_읽는다(tmp_path: Path) -> None:
    result = load_input(subtitle=_subtitle(tmp_path))
    assert result.segments[0].source_text == "자막에서 왔다"


def test_영상만_주어지면_전사한다(tmp_path: Path) -> None:
    result = load_input(media=_media(tmp_path), provider=FakeSttProvider(CUES))
    assert all(seg.source_from_stt for seg in result.segments)
    assert [s.source_text for s in result.segments] == [
        "안녕하세요",
        "반갑습니다",
        "고맙습니다",
        "감사합니다",
    ]


def test_영상만_주어졌는데_프로바이더가_없으면_거부한다(tmp_path: Path) -> None:
    # 기존 `_reject_non_subtitle`과 **같은 reason**을 단언한다. 근거는
    # `load_input` 안의 주석에 있다 - 여기 복사하지 않는 것은, 같은 문장을 두 곳에
    # 두면 한쪽만 고쳐져 갈라지기 때문이다(이 줄의 이전 판이 실제로 그랬다).
    with pytest.raises(IngestError) as exc:
        load_input(media=_media(tmp_path))
    assert exc.value.reason == "video_input"


def test_자막_경로가_틀리면_영상으로_폴백하지_않는다(tmp_path: Path) -> None:
    """자막을 명시했는데 없으면 `not_found`다 - 영상으로 폴백하지 않는다 (FR-1.3).

    폴백하면 경로 오타 하나로 **사용자가 모르는 사이에 STT 요금이 나간다.**
    `provider.calls == []`가 그것을 잡는다.
    """
    provider = FakeSttProvider(CUES)
    with pytest.raises(IngestError) as exc:
        load_input(subtitle=tmp_path / "없는파일.srt", media=_media(tmp_path), provider=provider)
    assert exc.value.reason == "not_found"
    assert provider.calls == []


def test_아무것도_주어지지_않으면_거부한다() -> None:
    with pytest.raises(IngestError) as exc:
        load_input()
    assert exc.value.reason == "no_input"


def test_source_lang이_양쪽_경로에_전달된다(tmp_path: Path) -> None:
    """두 경로가 `source_lang`에서 갈리지 않는지 본다 (FR-1.5).

    영상 쪽 프로바이더에 `language=None`을 준 것은 **`transcript.language`로
    덮는 변이를 죽이기 위해서다** - 덮으면 `source_lang`이 `None`이 되어
    `_SCRIPT_RANGES.get(None)`이 구조 신호를 통째로 끈다.
    """
    sub = load_input(subtitle=_subtitle(tmp_path), source_lang="ja")
    assert sub.source_lang == "ja"
    provider = FakeSttProvider(CUES, language=None)
    med = load_input(media=_media(tmp_path), provider=provider, source_lang="ja")
    assert med.source_lang == "ja"
    # 선언 언어가 프로바이더까지 내려가야 힌트가 산다 (`load_media`의 계약).
    assert provider.languages == ["ja"]


def test_STT_입력에서_실제_검수_비율이_1이_아니다(tmp_path: Path) -> None:
    """**이 게이트가 프로젝트의 핵심 주장을 지킨다** (설계 D8 · §8.1).

    STT 입력에서는 전 세그먼트가 `source_from_stt=True`다. 이 플래그가
    hard fail로 새면 FR-6.2에 따라 **전량이 검수 예산을 우회**해
    `review_ratio()`가 1.0이 되고, README 최상단의 무작위 베이스라인 대비
    배수가 **산출 불가능**해진다. 그 숫자가 "AI 래퍼가 아니다"를 증명하는
    유일한 자료다(요구사항정의서 §9.1 · §11 R4).

    플래그가 점수에 들어가는 것도 여기서 걸린다 - 전체가 같은 양만큼
    올라가면 순위에 정보를 하나도 주지 않으면서 상수만 더한다.
    """
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    for seg in result.segments:
        seg.target_text = "a fine translation here"

    by_id = collect_all(result.segments, CTX)
    risks = [fuse(seg.id, by_id[seg.id]) for seg in result.segments]
    scored = select_by_budget(risks, 0.34)

    ratio = review_ratio(scored)
    assert ratio < 1.0, "STT 플래그가 hard fail로 샜다 - README 배수가 산출 불가가 된다"
    assert not all(r.hard_fail for r in scored)
    # 플래그를 이름으로 삼은 신호가 하나도 수집되지 않아야 한다 (설계 D8).
    # 점수에 상수를 더하는 유출은 위 비율만으로는 드러나지 않는다.
    assert not any(sig.name == "source_from_stt" for r in scored for sig in r.signals)


def test_STT_플래그를_hard_fail로_올리면_비율이_1이_된다(tmp_path: Path) -> None:
    """**역가설을 고정한다** — 위 게이트가 실제로 무언가를 막고 있는가.

    이 리포에서 길이비 회귀 테스트가 버그 버전에서도 통과해 데이터를 다시
    짠 전례가 있다. 게이트를 만들면 반드시 실패시켜 봐야 하는데, 여기서는
    **변이를 넣었다 되돌리는 대신 승격 경로 자체를 테스트로 재현한다** -
    플래그가 hard fail이 될 수 있는 유일한 경로는 그것을 보는 수집기를
    등록하는 것이고, 이 테스트가 그 경로를 실행해 1.0을 보인다.

    위 테스트가 통과하고 이 테스트도 통과해야 게이트가 살아 있다는 뜻이다.
    """
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    for seg in result.segments:
        seg.target_text = "a fine translation here"

    # 누군가 D8을 어기고 만들 법한 수집기다. 전역 레지스트리를 건드리지
    # 않으려고 등록하지 않고 신호만 직접 만든다.
    risks = [
        fuse(seg.id, [Signal(name="source_from_stt", tier=0, score=1.0, hard_fail=True)])
        for seg in result.segments
        if seg.source_from_stt
    ]
    assert len(risks) == len(result.segments), "전량이 플래그를 갖는다는 전제를 고정한다"
    scored = select_by_budget(risks, 0.34)
    assert review_ratio(scored) == 1.0
