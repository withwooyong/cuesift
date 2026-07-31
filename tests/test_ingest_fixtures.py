"""픽스처가 심었다고 선언한 경계를 실제로 갖는지 검증한다.

픽스처는 검사 대상이 아니라 **검사 도구**다. 편집기나 git이 줄바꿈·인코딩을
바꾸면 이후 테스트가 **다른 이유로 통과**하고 그 사실은 드러나지 않는다.
`.gitattributes`의 `*.srt -text`가 실제로 작동하는지도 여기서 확인된다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"

EXPECTED = (
    "minimal.srt",
    "crlf_bom.srt",
    "overlap.vtt",
    "tags.ass",
    "empty_cue.srt",
    "reversed.srt",
    "all_comments.ass",
    "not_subtitle.txt",
    "cp949.srt",
    "multiline.vtt",
)


def test_all_fixtures_exist():
    """개수를 고정한다 — 파일이 사라져도 다른 테스트는 조용히 건너뛸 수 있다."""
    actual = sorted(p.name for p in FIXTURES.iterdir() if p.is_file())
    assert actual == sorted(EXPECTED)


def test_crlf_bom_fixture_keeps_crlf_and_bom():
    """git이 줄바꿈을 정규화하면 이 픽스처는 의미를 잃는다."""
    raw = (FIXTURES / "crlf_bom.srt").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "BOM이 없다"
    assert b"\r\n" in raw, "CRLF가 LF로 정규화됐다"


def test_minimal_fixture_is_lf_only():
    """대조군 — 이 파일에 CRLF가 있으면 위 테스트가 무의미해진다."""
    assert b"\r\n" not in (FIXTURES / "minimal.srt").read_bytes()


def test_cp949_fixture_is_not_utf8():
    """utf-8로 읽히면 decode 오류 경로를 검증할 수 없다."""
    raw = (FIXTURES / "cp949.srt").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    raw.decode("cp949")  # 예외가 나지 않아야 한다


def test_tags_ass_has_styles_section():
    """[V4+ Styles]가 없으면 pysubs2가 FormatAutodetectionError를 낸다(설계 §12).

    그러면 이 픽스처는 태그 처리가 아니라 파싱 실패를 테스트하게 된다.
    """
    text = (FIXTURES / "tags.ass").read_text(encoding="utf-8")
    assert "[V4+ Styles]" in text
