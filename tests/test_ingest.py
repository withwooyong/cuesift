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
