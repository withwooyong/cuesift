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


# --- FR-3.4 숫자 누락 ---


def test_number_missing_fires_when_a_number_disappears(ctx):
    sig = NumberMissing().collect(_seg("3시에 만나자", "See you later"), ctx)
    assert sig is not None
    assert sig.hard_fail is True
    assert sig.detail["missing"] == ["3"]


def test_number_missing_silent_when_all_numbers_survive(ctx):
    assert NumberMissing().collect(_seg("3시 15분", "3:15"), ctx) is None


def test_number_missing_silent_when_source_has_no_number(ctx):
    assert NumberMissing().collect(_seg("만나자", "See you"), ctx) is None


def test_number_missing_reports_only_the_absent_ones(ctx):
    sig = NumberMissing().collect(_seg("3시 15분 20초", "3 minutes 15"), ctx)
    assert sig is not None
    assert sig.detail["missing"] == ["20"]


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
