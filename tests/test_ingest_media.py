"""`load_media`의 `IngestResult` 합성 (FR-1.2 · 설계 D5·D6·§4.4)."""

from __future__ import annotations

from pathlib import Path

import pysubs2
import pytest
from tests.fakes.stt import FakeSttProvider

from cuesift.ingest import IngestError, load_media, write_subtitle
from cuesift.translate.provider import FatalProviderError

CUES = [(0.0, 1.2345, "안녕하세요"), (1.2345, 3.5, "반갑습니다"), (3.5, 5.0, "감사합니다")]


def _media(tmp_path: Path) -> Path:
    p = tmp_path / "talk.mp4"
    p.write_bytes(b"fake video")
    return p


def test_영상에서_세그먼트를_만든다(tmp_path: Path) -> None:
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert len(result.segments) == 3
    assert result.segments[0].source_text == "안녕하세요"
    assert result.source_path == _media(tmp_path)


def test_모든_세그먼트에_플래그가_붙는다(tmp_path: Path) -> None:
    # FR-1.4 - STT 원문은 전부 검수 필요 표시를 받는다.
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert all(seg.source_from_stt for seg in result.segments)


def test_id_규칙이_자막_경로와_같다(tmp_path: Path) -> None:
    # `_to_segments`와 같은 `f"{index:05d}"`, 0부터 연속.
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert [s.id for s in result.segments] == ["00000", "00001", "00002"]
    assert [s.index for s in result.segments] == [0, 1, 2]


def test_초를_밀리초로_반올림한다(tmp_path: Path) -> None:
    """D5 - **기대값을 half-up으로 적으면 안 된다.**

    파이썬의 `round()`는 짝수 반올림이라 `1.2345 * 1000 = 1234.5`가
    **1234**가 된다(실측). half-up으로 1235를 기대하면 이 게이트 자신이 틀린다.
    """
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert result.segments[0].start_ms == 0
    assert result.segments[0].end_ms == 1234
    assert result.segments[1].start_ms == 1234
    assert result.segments[2].end_ms == 5000


def test_인접_큐의_경계가_붙어_있다(tmp_path: Path) -> None:
    # D5의 목적. 한쪽만 내리고 한쪽만 올리면 원본에 없던 겹침을 우리가 만든다.
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
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
    assert result.event_index == {"00000": 0, "00001": 1, "00002": 2}


def test_subs가_큐를_그대로_담는다(tmp_path: Path) -> None:
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    assert isinstance(result.subs, pysubs2.SSAFile)
    assert len(result.subs.events) == 3
    assert result.subs.events[0].plaintext == "안녕하세요"
    assert result.subs.events[0].end == 1234


def test_write_subtitle_왕복이_성립한다(tmp_path: Path) -> None:
    """**이 스위트에서 두 번째로 중요한 게이트다** (위험 R3).

    번역 자막을 못 쓰면 이 작업 전체가 무의미하다. `format`이 `None`이면
    `.tmp`에서, `event_index`가 비면 `KeyError`로 죽는다 - 둘 다 실측했다.
    """
    result = load_media(_media(tmp_path), FakeSttProvider(CUES))
    for seg, en in zip(result.segments, ["Hello", "Nice to meet you", "Thanks"], strict=True):
        seg.target_text = en
    out = tmp_path / "talk.en.srt"
    write_subtitle(result, result.segments, out)

    body = out.read_text(encoding="utf-8")
    assert body.count("-->") == 3
    assert "Hello" in body
    assert "안녕하세요" not in body
    assert "00:00:01,234" in body


def test_source_lang은_응답의_language를_쓴다(tmp_path: Path) -> None:
    # FR-1.5 - 기록만 한다.
    result = load_media(_media(tmp_path), FakeSttProvider(CUES, language="korean"))
    assert result.source_lang == "korean"


def test_language가_없으면_호출자가_준_값을_쓴다(tmp_path: Path) -> None:
    # 백엔드가 그 필드를 안 낼 수 있다 (§12 Q3).
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


def test_프로바이더에_경로를_넘긴다(tmp_path: Path) -> None:
    provider = FakeSttProvider(CUES)
    media = _media(tmp_path)
    load_media(media, provider)
    assert provider.calls == [media]


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
