"""인제스트 (요구사항정의서 FR-1.1·1.3·1.5).

설계: docs/superpowers/specs/2026-07-31-ingest-design.md
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cuesift.ingest import IngestError, load_subtitle

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
