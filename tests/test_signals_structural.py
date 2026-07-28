"""구조 신호 테스트 (요구사항정의서 FR-3.1~FR-3.5)."""

import pytest

from cuesift.segment import Segment
from cuesift.signals import SignalContext
from cuesift.signals.structural import (
    Degeneration,
    Empty,
    NumberMissing,
    TagLost,
    Untranslated,
)
from cuesift.spec import load_builtin


@pytest.fixture
def ctx():
    return SignalContext(
        profile=load_builtin("en"), glossary=None, source_lang="ko", target_lang="en"
    )


def _seg(source: str, target: str | None) -> Segment:
    return Segment(
        id="s1", index=0, start_ms=0, end_ms=2000, source_text=source, target_text=target
    )


# --- FR-3.1 미번역 잔존 ---


def test_untranslated_fires_when_hangul_remains_in_english(ctx):
    sig = Untranslated().collect(_seg("안녕하세요", "안녕하세요"), ctx)
    assert sig is not None
    assert sig.hard_fail is True
    assert sig.score == 1.0


def test_untranslated_silent_on_clean_translation(ctx):
    assert Untranslated().collect(_seg("안녕하세요", "Hello"), ctx) is None


def test_untranslated_tolerates_a_single_stray_character(ctx):
    """FR-3.1은 '유의미하게' 남은 경우다. 고유명사 표기 등으로 한 글자가
    섞이는 일은 실제로 있고, 이걸 hard fail로 올리면 오탐이 쏟아진다."""
    assert Untranslated().collect(_seg("가나다라마바사", "A long English sentence 가"), ctx) is None


def test_untranslated_silent_when_target_lang_is_the_source_script(ctx):
    """ko→ko(원문 검수 경로)에서 한글이 남는 것은 정상이다."""
    same = SignalContext(load_builtin("ko"), None, "ko", "ko")
    assert Untranslated().collect(_seg("안녕하세요", "안녕하세요"), same) is None


# --- FR-3.2 빈 값 ---


@pytest.mark.parametrize("target", ["", "   ", "\n\n", None])
def test_empty_fires_on_blank_targets(ctx, target):
    sig = Empty().collect(_seg("원문이 있다", target), ctx)
    assert sig is not None
    assert sig.hard_fail is True


def test_empty_silent_when_source_is_also_blank(ctx):
    """원문이 비었으면 번역문이 빈 것은 오류가 아니다."""
    assert Empty().collect(_seg("   ", ""), ctx) is None


# --- FR-3.3 반복 붕괴 ---


def test_degeneration_fires_on_repeated_token(ctx):
    sig = Degeneration().collect(_seg("반복", "yes yes yes yes yes"), ctx)
    assert sig is not None
    assert sig.hard_fail is True


def test_degeneration_silent_on_natural_repetition(ctx):
    """'very very good'처럼 2회 반복은 자연스럽다. 여기서 발화하면
    강조 표현이 전부 오탐이 된다."""
    assert Degeneration().collect(_seg("아주 좋다", "very very good"), ctx) is None


def test_degeneration_silent_on_short_text(ctx):
    assert Degeneration().collect(_seg("네", "yes"), ctx) is None


@pytest.mark.parametrize(
    "target",
    [
        "the cat sat on the mat with the dog",
        "I know that you know that I know",
    ],
)
def test_degeneration_silent_on_non_consecutive_repetition(ctx, target):
    """관사·대명사는 보통 길이 문장에서 3회를 쉽게 넘긴다.

    전체 빈도를 세면 평범한 영어 문장이 전부 hard fail이 되어
    검수 예산을 우회하고 Recall@Budget 지표가 망가진다.
    """
    assert Degeneration().collect(_seg("원문", target), ctx) is None


def test_degeneration_detects_repeated_phrase(ctx):
    """FR-3.3은 '어절·구'다. 구 단위 연속 반복도 붕괴다."""
    sig = Degeneration().collect(_seg("원문", "I don't know I don't know I don't know"), ctx)
    assert sig is not None
    assert sig.detail["count"] == 3


def test_degeneration_detects_korean_repetition(ctx):
    sig = Degeneration().collect(_seg("원문", "그래 그래 그래 그래"), ctx)
    assert sig is not None


# --- FR-3.4 숫자 누락 ---


def test_number_missing_fires_when_a_number_disappears(ctx):
    """두 자리 이상 숫자가 통째로 사라지면 hard fail이다.

    한 자리 수(예: '3')는 영어 자막 관행상 단어로 적을 수 있어 소프트로
    낮췄다(결함4 수정) — 그 경계와 겹치지 않도록 여기는 두 자리 숫자로
    검증한다."""
    sig = NumberMissing().collect(_seg("15분 후에 만나자", "See you later"), ctx)
    assert sig is not None
    assert sig.hard_fail is True
    assert sig.detail["missing"] == ["15"]


def test_number_missing_silent_when_all_numbers_survive(ctx):
    assert NumberMissing().collect(_seg("3시 15분", "3:15"), ctx) is None


def test_number_missing_silent_when_source_has_no_number(ctx):
    assert NumberMissing().collect(_seg("만나자", "See you"), ctx) is None


def test_number_missing_reports_only_the_absent_ones(ctx):
    sig = NumberMissing().collect(_seg("3시 15분 20초", "3 minutes 15"), ctx)
    assert sig is not None
    assert sig.detail["missing"] == ["20"]


def test_number_missing_silent_on_thousands_separator(ctx):
    """원문은 콤마를 쓰고 번역문은 안 쓰는 일이 흔하다."""
    assert NumberMissing().collect(_seg("1,000원", "It costs 1000 won"), ctx) is None


def test_number_missing_silent_on_decimal_point(ctx):
    assert NumberMissing().collect(_seg("3.14를 기억해", "Remember 3.14"), ctx) is None


def test_number_missing_is_soft_when_only_single_digits_are_missing(ctx):
    """영어 자막은 한 자리 수를 단어로 적는다('three').

    신호는 내되 hard fail은 해제한다 — 정상 번역이 예산을 우회하면 안 된다.
    """
    sig = NumberMissing().collect(_seg("3시에 만나자", "See you at three"), ctx)
    assert sig is not None
    assert sig.hard_fail is False


def test_number_missing_stays_hard_when_a_multi_digit_number_is_missing(ctx):
    """연도·금액은 단어로 적는 일이 거의 없다."""
    sig = NumberMissing().collect(_seg("2023년 매출", "Revenue was strong"), ctx)
    assert sig is not None
    assert sig.hard_fail is True


# --- FR-3.5 태그 손실 ---


def test_tag_lost_fires_when_markup_disappears(ctx):
    sig = TagLost().collect(_seg("<i>기울임</i>", "italic"), ctx)
    assert sig is not None
    assert sig.hard_fail is True


def test_tag_lost_silent_when_markup_is_preserved(ctx):
    assert TagLost().collect(_seg("<i>기울임</i>", "<i>italic</i>"), ctx) is None


def test_tag_lost_silent_when_neither_side_has_markup(ctx):
    assert TagLost().collect(_seg("평문", "plain"), ctx) is None


def test_tag_lost_fires_on_added_markup(ctx):
    """없던 태그가 생긴 것도 불일치다. LLM이 서식을 지어내는 사고가 있다."""
    assert TagLost().collect(_seg("평문", "<i>plain</i>"), ctx) is not None


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("<font color='red'>빨강</font>", '<font color="red">red</font>'),
        ("<br>", "<br/>"),
        ('<font  color="red">빨강</font>', '<font color="red">red</font>'),
    ],
)
def test_tag_lost_silent_on_serialization_differences(ctx, source, target):
    """자막 편집기·파서 라운드트립은 태그를 재직렬화한다.

    표기만 바뀐 것을 마크업 손실로 잡으면 hard fail이 예산을 우회해 쌓인다.
    """
    assert TagLost().collect(_seg(source, target), ctx) is None


# --- FR-3.1 미번역 잔존 (짧은 세그먼트) ---


def test_untranslated_silent_on_short_segment_with_one_stray_char(ctx):
    """짧은 대사에서는 비율 분모가 작아 한 글자에도 임계를 넘는다.

    자막은 감탄사·짧은 응답이 매우 흔하다.
    """
    assert Untranslated().collect(_seg("안녕", "Hi 가"), ctx) is None
