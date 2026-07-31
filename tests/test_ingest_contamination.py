"""필터가 없으면 무엇이 깨지는지 수치로 고정한다 (설계 §9.2 축 6).

이 프로젝트의 규율: **게이트를 만들면 반드시 실패시켜 봐야 한다.**
길이비 회귀 테스트가 버그 버전에서도 통과해 데이터를 다시 짠 전례가 있다.

여기서 고정하는 사실은 "필터가 있으니 안전하다"가 아니라
**"필터가 없으면 CPS가 실제로 넘친다"** 이다. 후자가 반증 가능하다.
"""

from __future__ import annotations

from pathlib import Path

from cuesift.ingest import load_subtitle
from cuesift.spec import check_text, load_builtin, text_width

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


def test_filtered_segments_have_no_spec_violations():
    """필터를 지난 세그먼트는 ko 프로파일에서 깨끗하다 — 대조군."""
    profile = load_builtin("ko")
    result = load_subtitle(FIXTURES / "tags.ass")

    for seg in result.segments:
        kinds = {v.kind for v in check_text(seg.source_text, seg.duration_ms, profile)}
        assert kinds == set(), f"{seg.id}에서 예상치 못한 위반: {kinds}"


def test_unfiltered_drawing_would_violate_cps_and_line_length():
    """드로잉을 세그먼트로 넣었다면 CPS와 줄 길이가 둘 다 터진다.

    실측값(2026-07-31, `ko` 프로파일 max_cps=12.0 · max_chars=16.0):
      텍스트 69자 → latin_half 폭 34.5 → 2000ms 기준 CPS 17.25

    **드로잉이 짧으면 이 테스트는 성립하지 않는다.** 27자짜리 드로잉은
    폭 13.5 · CPS 6.75로 한계를 넘지 못해 `tags.ass`의 드로잉을
    69자로 만들었다. 픽스처를 줄이면 여기가 먼저 깨진다.
    """
    profile = load_builtin("ko")
    result = load_subtitle(FIXTURES / "tags.ass")

    drawing = next(e for e in result.subs if e.is_drawing)
    width = text_width(drawing.plaintext, profile.char_counting)
    cps = width / (drawing.duration / 1000)

    assert drawing.duration == 2000
    assert width == 34.5
    assert cps > profile.max_cps

    kinds = {v.kind for v in check_text(drawing.plaintext, drawing.duration, profile)}
    assert "cps" in kinds
    assert "line_length" in kinds


def test_comment_line_would_be_spec_checked_if_kept():
    """주석 줄은 화면에 안 나오는데 필터가 없으면 규격 검사를 받는다."""
    result = load_subtitle(FIXTURES / "tags.ass")

    comment = next(e for e in result.subs if e.is_comment)
    assert comment.plaintext not in [s.source_text for s in result.segments]
