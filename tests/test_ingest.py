"""인제스트 (요구사항정의서 FR-1.1·1.3·1.5).

설계: docs/superpowers/specs/2026-07-31-ingest-design.md
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from cuesift.ingest import IngestError, load_subtitle
from cuesift.spec import check_overlaps

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


def test_srt_becomes_segments():
    result = load_subtitle(FIXTURES / "minimal.srt")

    assert result.format == "srt"
    assert result.source_path == FIXTURES / "minimal.srt"
    assert [s.id for s in result.segments] == ["00000", "00001"]
    assert [s.index for s in result.segments] == [0, 1]
    assert result.segments[0].start_ms == 1000
    assert result.segments[0].end_ms == 3000
    assert result.segments[0].source_text == "안녕하세요\n두 번째 줄"
    assert result.segments[1].source_text == "세 번째 큐"


def test_segments_leave_translation_fields_empty():
    """번역은 WP7이 채운다. 인제스트가 미리 채우면 미번역 신호가 눈이 먼다."""
    result = load_subtitle(FIXTURES / "minimal.srt")

    assert all(s.target_text is None for s in result.segments)
    assert all(s.speaker is None for s in result.segments)
    assert all(s.meta == {} for s in result.segments)


def test_source_lang_defaults_to_ko_and_is_overridable():
    """FR-1.5 — 값을 받아 기록만 한다. 우선순위 해결은 WP6."""
    assert load_subtitle(FIXTURES / "minimal.srt").source_lang == "ko"
    assert load_subtitle(FIXTURES / "minimal.srt", source_lang="ja").source_lang == "ja"


def test_crlf_and_bom_do_not_leak_into_text():
    """파이썬 텍스트 모드가 CRLF를 처리한다(설계 §12).

    `\\r`이 남으면 규격 검사가 `split("\\n")`할 때 줄 끝마다 1자씩
    더 세고, 마지막에 빈 줄이 하나 생겨 line_count가 +1 된다.
    """
    result = load_subtitle(FIXTURES / "crlf_bom.srt")

    assert result.segments[0].source_text == "안녕하세요\n두 번째 줄"
    assert "\r" not in result.segments[0].source_text
    assert "﻿" not in result.segments[0].source_text  # BOM은 눈에 안 보이므로 이스케이프로 쓴다


def test_vtt_multiline_keeps_newlines():
    """`\\N`이 `\\n`으로 정규화돼야 spec.check_text의 줄 분리가 맞는다."""
    result = load_subtitle(FIXTURES / "multiline.vtt")

    assert result.format == "vtt"
    assert result.segments[0].source_text == "첫째 줄\n둘째 줄\n셋째 줄"


def test_missing_file_raises_not_found(tmp_path):
    with pytest.raises(IngestError) as exc:
        load_subtitle(tmp_path / "없는파일.srt")

    assert exc.value.reason == "not_found"


def test_video_input_raises_video_input(tmp_path):
    """FR-1.3 — 영상은 STT 경로이고 v0.1에 STT가 없다.

    확장자로 먼저 거르지 않으면 mp4가 텍스트로 열려 UnicodeDecodeError가 나고,
    사용자에게 "utf-8로 변환하라"는 **틀린 조언**이 간다.
    """
    video = tmp_path / "episode01.mp4"
    video.write_bytes(b"\x00\x00\x00\x20ftypisom")

    with pytest.raises(IngestError) as exc:
        load_subtitle(video)

    assert exc.value.reason == "video_input"


@pytest.mark.parametrize(
    "suffix", [".mkv", ".mov", ".webm", ".m4v", ".avi", ".mp3", ".m4a", ".wav"]
)
def test_all_media_suffixes_are_rejected(tmp_path, suffix):
    media = tmp_path / f"episode01{suffix}"
    media.write_bytes(b"\x00\x01\x02")

    with pytest.raises(IngestError) as exc:
        load_subtitle(media)

    assert exc.value.reason == "video_input"


def test_cp949_file_raises_decode():
    """설계 §5.3 — utf-8 고정이다. `--encoding` 플래그가 없으므로
    설정할 수 없는 인자를 만들지 않는다. 진단 메시지가 변환을 안내한다.
    """
    with pytest.raises(IngestError) as exc:
        load_subtitle(FIXTURES / "cp949.srt")

    assert exc.value.reason == "decode"


def test_non_subtitle_text_raises_parse():
    """`load()`는 자동 판별을 하므로 FormatAutodetectionError를 던진다(설계 §12).

    초판 스펙은 이것을 "0큐"로 적었으나 그 측정은 `format_`을 강제한 것이었다.
    """
    with pytest.raises(IngestError) as exc:
        load_subtitle(FIXTURES / "not_subtitle.txt")

    assert exc.value.reason == "parse"


# pysubs2의 JSON 포맷은 **내용으로** 판별된다 — `text.startswith('{"') and '"info":' in text`.
# 따라서 확장자로는 막을 수 없고, 스키마가 조금이라도 어긋나면 파서가 KeyError·TypeError·
# AttributeError를 낸다. 이것들은 Pysubs2Error도 ValueError도 아니다.
_JSON_SHAPED = {
    "12바이트": '{"info": {}}',
    "events 키 없음": '{"info": {}, "styles": {}}',
    "events가 null": '{"info": {}, "styles": {}, "events": null}',
    "다른 도구 스키마": '{"info": {}, "styles": {}, "events": [{"begin": 0, "end": 1}]}',
    "styles가 리스트": '{"info": {}, "styles": [], "events": []}',
}


@pytest.mark.parametrize("label", list(_JSON_SHAPED))
def test_json_shaped_files_raise_ingest_error_not_a_bare_keyerror(tmp_path, label: str):
    """JSON처럼 생긴 파일이 `IngestError` 밖으로 새면 안 된다 (설계 §5).

    `_load`의 `except (Pysubs2Error, ValueError)`는 pysubs2의 JSON 파서가 내는
    `KeyError`·`TypeError`·`AttributeError`를 **하나도 못 잡는다.** 새면 호출자의
    `except IngestError`도 통과해 미처리 traceback이 되고 종료 코드 1이 된다 —
    1은 "규격 위반 발견"이라 **12바이트짜리 쓰레기 파일이 자막 결함으로 오보된다.**

    `tmp_path`에 내용을 직접 쓰는 것은 이 입력들이 자막 픽스처가 아니라 **퇴화 입력**이라
    파일로 두면 `test_ingest_fixtures.py`의 목록만 6줄 늘고 내용은 안 보이기 때문이다.
    """
    target = tmp_path / "input.json"
    target.write_text(_JSON_SHAPED[label], encoding="utf-8")

    with pytest.raises(IngestError):
        load_subtitle(target)


# 기본 본문이 **규격을 위반하는** 긴 줄인 것은 의도적이다(ko는 16자/줄).
# 위반이 0건이면 `_format_report`가 `_format_timecode`에 **닿지 않아**
# float 타임코드가 수정 전에도 `exit 0 · "위반 없음"`으로 조용히 통과한다(실측).
# 그 문서로 테스트를 짜면 "리포트에서 죽는다"는 근거 서술이 자기 픽스처에 대해 거짓이 된다.
_VIOLATING_LINE = "열여섯 자를 확실히 넘기는 아주 긴 줄입니다 정말로"


def _json_track(start: object, end: object, text: object = _VIOLATING_LINE) -> str:
    """스키마가 **정상인** pysubs2 JSON 한 큐. 타입만 갈아 끼운다.

    C2(깨진 JSON)와 갈라놓는 것이 요점이다 — 스키마를 깨면 `parse`로 잡혀
    타입 검사가 없어도 66이 나오고, 그러면 이 테스트가 **아무것도 검증하지 않는다.**
    아래 `reason`을 `timecode_type`으로 못 박는 이유가 그것이다.
    """
    return json.dumps(
        {
            "info": {},
            "styles": {"Default": {}},
            "events": [
                {
                    "start": start,
                    "end": end,
                    "text": text,
                    "marked": False,
                    "layer": 0,
                    "style": "Default",
                    "name": "",
                    "marginl": 0,
                    "marginr": 0,
                    "marginv": 0,
                    "effect": "",
                    "type": "Dialogue",
                }
            ],
        }
    )


def test_the_json_fixture_helper_is_valid_with_int_times(tmp_path):
    """대조군 — 이 문서가 int일 때 로드되지 않으면 아래 테스트들이 스키마 오류를 재는 것이다."""
    target = tmp_path / "ok.json"
    target.write_text(_json_track(1000, 4000), encoding="utf-8")

    result = load_subtitle(target)

    assert result.format == "json"
    assert result.segments[0].start_ms == 1000


@pytest.mark.parametrize(
    ("label", "start", "end"),
    [
        # 위반이 있는 본문이므로 이 문서는 수정 전 `_format_timecode`에서 ValueError를 냈다.
        ("float", 1000.0, 4000.0),
        ("str", "1000", "4000"),
        # bool은 int의 하위형이라 산술이 **통과한다.** 크래시가 아니라 조용히 틀린
        # 리포트가 나왔다 — `_VIOLATING_LINE` 기준으로 `false`/`true`는 길이 1ms짜리
        # 큐가 되어 `cps 24500.0 > 12.0`까지 날조했다(실측).
        ("bool", True, True),
        ("bool-false-true", False, True),
    ],
)
def test_non_integer_timecodes_are_rejected_at_the_ingest_boundary(
    tmp_path, label: str, start: object, end: object
):
    """`Segment`의 `start_ms: int`는 **런타임에 아무것도 막지 않는다** (`@dataclass`다).

    pysubs2의 json 포맷만 `SSAEvent(**fields)`로 원본 값을 그대로 넣는다 —
    srt·vtt·ass·ssa는 `times_to_ms`·`make_time`이 int를 반환하므로 이 경로가 없다.

    **검증을 `Segment.__post_init__`에 두면 exit이 여전히 1이다.** `load_subtitle`이
    `_to_segments`를 `try` **밖에서** 부르므로 `ValueError`가 `IngestError`를 우회한다.
    지적이 옳아도 위치가 틀리면 아무것도 안 고쳐진다 — 그래서 경계인 `_to_segments`가 막는다.
    """
    target = tmp_path / f"{label}.json"
    target.write_text(_json_track(start, end), encoding="utf-8")

    with pytest.raises(IngestError) as exc:
        load_subtitle(target)

    # `parse`가 나오면 스키마가 깨져 C2 경로로 잡힌 것이라 타입 검사를 검증하지 못한 것이다.
    # `bad_timecode`를 재사용하지 않는 것은 "역전"과 "타입 틀림"이 섞이면 진단이 무뎌지기 때문이다.
    assert exc.value.reason == "timecode_type"


@pytest.mark.parametrize(
    ("label", "text"),
    [("null", None), ("숫자", 123), ("객체", {"a": 1}), ("배열", [1, 2])],
)
@pytest.mark.parametrize("suffix", [".json", ".srt"])
def test_non_string_text_is_rejected_at_the_ingest_boundary(
    tmp_path, label: str, text: object, suffix: str
):
    """`text`가 문자열이 아니면 **필터링 단계에서** 죽는다 (설계 §4).

    `_keep_displayed`의 `e.is_drawing`이 `parse_tags(self.text)`를 부르는데 그것이
    `TypeError: expected string or bytes-like object`를 낸다.

    **C3(타임코드 타입) 수정이 이 경로를 못 막았다.** `_require_int_timecodes`는
    `_to_segments` 안에 있는데 `_keep_displayed`가 **그보다 먼저** 돌기 때문이다.
    C3에서 배운 "검사는 우회되지 않는 위치에 둬야 한다"가 같은 파일 안에서
    한 단계 더 앞으로 밀린 것이다.
    """
    target = tmp_path / f"text{suffix}"
    target.write_text(_json_track(0, 3000, text=text), encoding="utf-8")

    with pytest.raises(IngestError) as exc:
        load_subtitle(target)

    assert exc.value.reason == "text_type"


@pytest.mark.parametrize(
    ("label", "start", "end"),
    [
        ("str/int", "1000", 4000),
        ("int/str", 1000, "4000"),
        ("float/str", 1000.0, "4000"),
        ("null/int", None, 4000),
        ("int/null", 1000, None),
    ],
)
def test_mixed_type_timecodes_prove_the_check_runs_before_the_reversal_test(
    tmp_path, label: str, start: object, end: object
):
    """**타입 검사가 역전 검사보다 먼저라는 순서를 고정한다.**

    `_to_segments`는 `_require_int_timecodes` 다음에 `event.end < event.start`를 본다.
    순서를 뒤집으면 그 비교가 `TypeError`를 내고 `IngestError`를 우회해 **exit 1**이 된다.

    **혼합 타입이어야만 이 게이트가 작동한다**(실측). 두 필드가 **같은** 잘못된 타입이면
    비교가 조용히 성공한다 — `"4000" < "1000"`은 사전순으로 `False`다. 그래서
    `("1000", "4000")` 같은 str/str 케이스로는 순서를 뒤집어도 **아무것도 드러나지 않는다.**
    실제로 순서만 뒤집은 변이에서 리포 전체 테스트가 통과한 적이 있다.
    """
    target = tmp_path / f"{label.replace('/', '_')}.json"
    target.write_text(_json_track(start, end), encoding="utf-8")

    with pytest.raises(IngestError) as exc:
        load_subtitle(target)

    assert exc.value.reason == "timecode_type"


@pytest.mark.parametrize(
    ("label", "start", "end"),
    [("둘 다 음수", -5000, -1000), ("start만 음수", -3000, 1000)],
)
def test_negative_timecodes_are_rejected_at_the_ingest_boundary(
    tmp_path, label: str, start: int, end: int
):
    """음수 타임코드는 **재생할 수 없는 좌표**이므로 파일이 깨진 것이다 (exit 66).

    **역전 검사도 타입 검사도 이것을 잡지 못했다**(실측). 둘 다 통과한 뒤
    `_format_timecode`의 `max(ms, 0)`이 `00:00:00.000`을 찍어, `(-5000, -1000)`처럼
    규격 위반이 없는 트랙은 **exit 0 · "위반 없음"으로 조용히 통과했다.**

    규격 위반(exit 1)으로 내지 않는 이유는 고칠 대상이 다르기 때문이다 — CPS·줄길이는
    검수자가 그 큐의 텍스트를 고치면 되지만, 음수 좌표는 싱크·변환 파이프라인의 사고다.
    섞으면 CI가 두 사고에 같은 대응을 한다.
    """
    target = tmp_path / "negative.json"
    target.write_text(_json_track(start, end), encoding="utf-8")

    with pytest.raises(IngestError) as exc:
        load_subtitle(target)

    assert exc.value.reason == "negative_timecode"


def test_a_cue_starting_at_zero_loads_normally():
    """**`< 0`이지 `<= 0`이 아니다** — 이 경계를 지키는 유일한 게이트다.

    `0`은 영상 첫 프레임을 가리키는 정상 값이고 `00:00:00,000`으로 시작하는 자막은
    흔하다. `<= 0`으로 조이면 그런 자막이 **전부 exit 66("파일 내용이 틀림")**이 된다.

    **이 테스트가 없을 때 그 변이가 497건 전체 스위트를 통과했다**(리뷰 실측, 두 리뷰어가
    독립적으로 같은 결론). 바로 옆 `spec/profile.py`의 `_require_positive`가 `<= 0`을
    쓰므로 "타임코드도 0보다 커야지"라는 오독은 그럴듯하다.

    **픽스처를 새로 만들어야 했다** — 기존 13종 중 `0`을 지나가는 것이 하나도 없었다.
    `_json_track(0, ...)`을 쓰는 테스트가 둘 있지만 둘 다 `text`를 비정상으로 넣어
    `_keep_displayed`에서 먼저 죽으므로 부호 검사에 닿지 않는다.
    """
    result = load_subtitle(FIXTURES / "starts_at_zero.srt")

    assert result.segments[0].start_ms == 0, "0에서 시작하는 큐가 살아남지 못했다"
    assert len(result.segments) == 2


def test_negative_timecodes_are_rejected_in_ass_not_only_json():
    """**음수의 진입로는 json 하나가 아니다** — 이 저장소가 세 곳에 적었던 전제가 거짓이었다.

    pysubs2는 ASS·SAMI(`.smi`)·MPL2에서 음수를 **의도적으로** 파싱한다 —
    `substation.py`에 `# handle negative timestamps` 분기가 있고 `mpl2.py`의 정규식에
    부호가 명시돼 있으며 `sami.py`는 `int()`로 그대로 받는다. 반면 SRT·VTT·MicroDVD·TMP는
    부호 자리가 없어 앞의 `-`를 **조용히 무시**한다(전부 리뷰 실측).

    이것이 중요한 이유는 README가 SAMI를 "한국 레거시 자막의 주력 포맷"으로 **지원
    선언**했기 때문이다. json 픽스처만 두면 다음 사람이 "json만 조심하면 된다"는 틀린
    지도를 받고 평문 포맷 회귀 테스트를 만들지 않는다.

    **쓰기 경로는 pysubs2가 전부 클램프한다**(실측). 그래서 "우리 도구로 왕복시켜 보니
    괜찮더라"는 검증이 통과해 버리고, 진짜 위험인 **외부 도구가 만든 파일**은 그 검증을
    통째로 비켜 간다. 라이브러리 경계는 read 경로와 write 경로를 따로 봐야 한다.
    """
    with pytest.raises(IngestError) as exc:
        load_subtitle(FIXTURES / "negative_timecode.ass")

    assert exc.value.reason == "negative_timecode"


def test_negative_check_runs_before_the_reversal_test(tmp_path):
    """**음수 검사가 역전 검사보다 먼저라는 순서를 고정한다.**

    `(1000, -3000)`은 음수이면서 역전이다. 순서가 뒤집히면 `bad_timecode`가 나오는데,
    그 메시지는 `start=1000ms > end=-3000ms`만 말하고 **음수라는 더 근본적인 결함을
    숨긴다.** 사용자는 역전만 고치고 여전히 재생 불가능한 파일을 얻는다.

    두 검사가 겹치지 않는 입력(`(-5000, -1000)`)으로는 순서를 뒤집어도
    아무것도 드러나지 않는다 — 위 `test_mixed_type_timecodes_...`와 같은 구조다.
    """
    target = tmp_path / "negative_and_reversed.json"
    target.write_text(_json_track(1000, -3000), encoding="utf-8")

    with pytest.raises(IngestError) as exc:
        load_subtitle(target)

    assert exc.value.reason == "negative_timecode"


def test_non_integer_timecodes_are_rejected_regardless_of_extension(tmp_path):
    """포맷 판별이 내용 기준이므로 `.srt` 이름을 붙여도 json 파서로 간다."""
    target = tmp_path / "looks_like_a_subtitle.srt"
    target.write_text(_json_track(1000.0, 4000.0), encoding="utf-8")

    with pytest.raises(IngestError) as exc:
        load_subtitle(target)

    assert exc.value.reason == "timecode_type"


def test_json_detection_ignores_the_extension(tmp_path):
    """확장자로 막을 수 없다는 것이 이 결함의 핵심이다.

    pysubs2는 **내용**으로 포맷을 고르므로 `.srt` 이름을 붙여도 JSON 파서로 간다.
    확장자 화이트리스트를 대책으로 삼으면 이 경로가 그대로 열린 채 남는다.
    """
    target = tmp_path / "looks_like_a_subtitle.srt"
    target.write_text('{"info": {}}', encoding="utf-8")

    with pytest.raises(IngestError):
        load_subtitle(target)


def test_unreadable_file_raises_unreadable_not_a_bare_oserror(tmp_path):
    """읽을 수 없는 파일도 `IngestError`로 모은다 (설계 §5).

    **정규화하지 않으면 `PermissionError`가 호출자의 `except IngestError`를 통과해**
    미처리 traceback이 되고 종료 코드 1이 된다 — 이 저장소에서 1은 "규격 위반 발견"이라
    **잠긴 파일이 자막 결함으로 오보된다.** Windows에서는 편집기·트랜스코더·OneDrive가
    자막을 잡고 있는 것이 흔하다.

    `path.is_file()`은 존재만 보고 읽기 권한은 보지 않으므로 이 경로를 못 막는다.
    실제로 읽을 수 없게 만들어 확인한다 — mock으로 확인하면 `pysubs2.load`가 정말
    `OSError`를 내는지를 우리가 가정하게 되고, 그 가정이 이 테스트의 검증 대상이다.
    """
    target = tmp_path / "locked.srt"
    target.write_bytes((FIXTURES / "minimal.srt").read_bytes())

    with _unreadable(target):
        # 잠금·권한이 실제로 읽기를 막았는지 먼저 본다. 막지 못했다면 아래 단언은
        # 통과해도 아무것도 검증하지 않은 것이다 — 검사하지 않고 통과하는 게이트다.
        try:
            target.read_bytes()
        except OSError:
            pass
        else:
            pytest.skip("이 환경에서는 파일을 읽을 수 없게 만들지 못했다 (root 등)")

        with pytest.raises(IngestError) as exc:
            load_subtitle(target)

    # 기존 6종(empty·decode·parse·not_found·video_input·bad_timecode)과 겹치지 않는다.
    assert exc.value.reason == "unreadable"


@contextmanager
def _unreadable(path: Path) -> Iterator[None]:
    """파일을 실제로 읽을 수 없게 만든다 — 플랫폼마다 수단이 다르다.

    Windows에서 `chmod 000`은 읽기를 막지 못하고(읽기 전용 플래그만 선다)
    POSIX에는 `msvcrt`가 없다. 한쪽 수단만 쓰면 다른 플랫폼에서 조용히 통과한다.
    """
    if sys.platform == "win32":
        import msvcrt

        size = path.stat().st_size
        with path.open("r+b") as handle:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, size)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, size)
    else:
        original = path.stat().st_mode
        path.chmod(0o000)
        try:
            yield
        finally:
            # 되돌리지 않으면 pytest의 tmp_path 정리가 실패한다.
            path.chmod(original)


def test_comments_and_drawings_are_excluded_from_segments():
    """설계 §4 — 화면에 나오지 않는 것은 세그먼트가 아니다."""
    result = load_subtitle(FIXTURES / "tags.ass")

    assert result.format == "ass"
    assert [s.source_text for s in result.segments] == ["기울임 대사", "두 번째 대사"]


def test_filtered_events_survive_in_subs_for_roundtrip():
    """설계 §2.1 — FR-7.1이 원본 포맷 출력을 요구하므로 원본은 온전해야 한다."""
    result = load_subtitle(FIXTURES / "tags.ass")

    assert len(result.subs) == 4
    assert any(e.is_comment for e in result.subs)
    assert any(e.is_drawing for e in result.subs)


def test_event_index_maps_to_the_right_original_event():
    """설계 §2.2 — 필터가 segments와 subs를 어긋나게 한다.

    이 대응표가 틀리면 WP5가 번역문을 한 칸씩 밀어서 되쓰고,
    **예외 없이 조용히** 밀린다.
    """
    result = load_subtitle(FIXTURES / "tags.ass")

    assert result.event_index == {"00000": 0, "00001": 3}
    for seg in result.segments:
        assert result.subs[result.event_index[seg.id]].plaintext == seg.source_text


def test_empty_text_cue_is_preserved():
    """설계 §4 — FR-3.2가 hard fail로 잡을 대상을 인제스트가 미리 지우지 않는다."""
    result = load_subtitle(FIXTURES / "empty_cue.srt")

    assert len(result.segments) == 2
    assert result.segments[1].source_text == ""


def test_all_comment_file_raises_empty():
    """설계 §5.4 — 파싱은 됐는데 표시할 큐가 0개인 유일한 실측 경로.

    CLAUDE.md의 "0개 수집은 통과가 아니라 설정 오류다"가 이 지점이다.
    """
    with pytest.raises(IngestError) as exc:
        load_subtitle(FIXTURES / "all_comments.ass")

    assert exc.value.reason == "empty"


def test_reversed_timecode_raises_bad_timecode_with_cue_number():
    """pysubs2는 역전 타임코드를 통과시킨다(설계 §12).

    `Segment.__post_init__`가 ValueError를 던지지만 그 메시지에는
    **몇 번째 큐인지가 없어** 사람이 파일에서 찾을 수 없다.
    1-based는 SRT 파일의 인덱스 표기와 맞춘 것이다.
    """
    with pytest.raises(IngestError) as exc:
        load_subtitle(FIXTURES / "reversed.srt")

    assert exc.value.reason == "bad_timecode"
    assert "2" in str(exc.value), "몇 번째 큐인지가 메시지에 없다"


def test_ssa_format_is_reported_as_ssa_not_ass():
    """FR-1.1은 ASS와 **SSA**를 함께 요구한다.

    `SSAFile.format`은 `.ssa`에 대해 `"ass"`가 아니라 `"ssa"`를 낸다(설계 §12).
    이 단언이 없으면 4개 포맷 중 3개만 검증된 채 FR-1.1이 완료로 표시된다 —
    이 저장소가 금지하는 "검사하지 않고 통과하는 게이트"다.
    """
    result = load_subtitle(FIXTURES / "basic.ssa")

    assert result.format == "ssa"
    assert len(result.segments) == 1
    assert result.segments[0].source_text == "에스에스에이 대사"
    assert result.segments[0].start_ms == 1000
    assert result.segments[0].end_ms == 3000


def test_bad_timecode_reports_original_cue_number_not_filtered_index():
    """필터가 앞 이벤트를 걸러도 큐 번호는 **원본 위치**여야 한다.

    `comment_then_reversed.ass`는 원본 0=주석·1=정상·2=역전이다. 주석이 걸리므로
    역전 큐의 `index`는 1이고 `raw_index`는 2다 — 1-based 보고는 **3**이어야 한다.
    `index + 1`을 쓰면 2가 나와 사용자가 엉뚱한 줄을 본다.

    `reversed.srt`는 주석이 없어 두 값이 같으므로 이 축을 구분하지 못한다.
    """
    with pytest.raises(IngestError) as exc:
        load_subtitle(FIXTURES / "comment_then_reversed.ass")

    assert exc.value.reason == "bad_timecode"
    assert "3번째" in str(exc.value)
    assert "2번째" not in str(exc.value)


def test_overlapping_cues_reach_spec_check_overlaps():
    """설계 §9.3 — 겹침이 있는 실제 트랙이 처음으로 파이프라인에 들어온다.

    벤치 하네스는 `build_track`이 겹침 0건으로 트랙을 합성했고 주입기의 `spec`
    유형은 duration을 줄일 뿐이라 겹침을 만들지 못했다. 그래서 `spec.overlap`의
    기여도 `+0.0%`는 "쓸모없다"가 아니라 **"측정하지 못했다"** 였다.

    이 테스트가 그 경로를 처음으로 연다 — 인제스트가 낸 `Segment`가
    `check_overlaps`에 그대로 들어가고 겹침 1000ms가 검출된다.
    """
    result = load_subtitle(FIXTURES / "overlap.vtt")

    violations = check_overlaps(result.segments)

    assert len(violations) == 1
    assert violations["00001"].kind == "overlap"
    assert violations["00001"].measured == 1000.0
