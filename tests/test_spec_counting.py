"""문자 폭 계산 테스트 (요구사항정의서 FR-5.2, §8.3.1)."""

import pytest

from cuesift.spec import CharCounting, text_width


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (CharCounting.grapheme, 5.0),
        (CharCounting.latin_half, 5.0),
        (CharCounting.fullwidth, 5.0),
    ],
)
def test_pure_hangul_is_the_same_in_all_modes(mode, expected):
    """한글은 셋 다 전각 1자다. 모드 차이는 라틴 문자에서만 나타난다."""
    assert text_width("안녕하세요", mode) == expected


def test_latin_is_half_width_in_latin_half_mode():
    """ko 프로파일(16자/줄)은 한글 기준이므로 라틴은 반각으로 센다."""
    assert text_width("AI", CharCounting.latin_half) == 1.0


def test_latin_is_full_width_in_fullwidth_mode():
    """ja 프로파일(13자/줄)은 세 언어 중 가장 좁다. 반각을 관대하게 세면
    화면 넘침으로 직결되므로 가장 보수적으로 전각 취급한다 (결정 D2)."""
    assert text_width("AI", CharCounting.fullwidth) == 2.0


def test_latin_counts_as_one_each_in_grapheme_mode():
    """en 프로파일(42자/줄)은 문자 폭을 따지지 않는다."""
    assert text_width("AI", CharCounting.grapheme) == 2.0


def test_mixed_script_in_latin_half():
    """한글 3자(3.0) + 라틴 2자(1.0) = 4.0"""
    assert text_width("인공지AI", CharCounting.latin_half) == 4.0


def test_combining_marks_do_not_add_width_in_grapheme_mode():
    """'é'를 e + U+0301로 쓴 것은 화면에서 한 글자다. 두 자로 세면
    라틴 언어의 줄 길이가 과대평가된다."""
    assert text_width("é", CharCounting.grapheme) == 1.0


def test_ideographic_space_is_full_width():
    """전각 공백(U+3000)은 CJK 자막에서 실제로 한 칸을 차지한다."""
    assert text_width("　", CharCounting.fullwidth) == 1.0


def test_empty_text_is_zero_width():
    for mode in CharCounting:
        assert text_width("", mode) == 0.0
