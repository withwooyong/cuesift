"""인제스트 (요구사항정의서 FR-1.1·1.3·1.5).

설계: docs/superpowers/specs/2026-07-31-ingest-design.md
"""

from __future__ import annotations

from pathlib import Path

from cuesift.ingest import load_subtitle

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
