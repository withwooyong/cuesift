"""타임코드 합성 테스트 (설계 스펙 §4.2)."""

from __future__ import annotations

from bench.corpus import SentencePair
from bench.timing import GAP_MS, SAFETY, plan_segment, required_duration_ms, wrap_text

from cuesift.spec import check_text, load_builtin, text_width

PROFILES = {
    "ko": load_builtin("ted-ko"),
    "en": load_builtin("ted-en"),
    "ja": load_builtin("ted-ja"),
}


def test_wrap_keeps_short_text_on_one_line():
    assert wrap_text("짧은 문장", PROFILES["ko"]) == "짧은 문장"


def test_wrap_splits_on_spaces_when_available():
    p = PROFILES["en"]
    # 15회 * "word "(5자) = 74자. ted-en 실제 용량은 42자 * 2줄 = 84자다.
    # 20회(99자)로 하면 어떤 알고리즘으로도 2줄에 담을 수 없어(99 > 84)
    # wrapped가 항상 None이 되고 이 테스트는 구현과 무관하게 실패한다.
    long_en = "word " * 15
    wrapped = wrap_text(long_en.strip(), p)
    assert wrapped is not None
    lines = wrapped.split("\n")
    assert len(lines) <= p.max_lines
    assert all(text_width(ln, p.char_counting) <= p.max_chars_per_line for ln in lines)


def test_wrap_falls_back_to_character_split_without_spaces():
    """공백이 없는 텍스트도 있다(URL 등). 이때 문자 단위 폴백이 필요하다.

    공백 분할만 쓰면 공백 없는 텍스트가 통째로 한 줄이 되어 전량 줄길이
    위반이 되는데, 깨끗한 트랙이라는 전제가 무너진다.
    """
    p = PROFILES["ja"]
    no_space = "あ" * int(p.max_chars_per_line + 3)
    wrapped = wrap_text(no_space, p)
    assert wrapped is not None
    assert "\n" in wrapped
    assert all(
        text_width(ln, p.char_counting) <= p.max_chars_per_line for ln in wrapped.split("\n")
    )


def test_wrap_returns_none_when_two_lines_cannot_hold_it():
    """담을 수 없는 것을 억지로 담으면 그 세그먼트가 영구 오탐이 된다."""
    p = PROFILES["ja"]
    too_long = "あ" * int(p.max_chars_per_line * p.max_lines + 5)
    assert wrap_text(too_long, p) is None


def test_required_duration_takes_the_strictest_language():
    """가장 빡빡한 언어가 duration을 정한다. 하나라도 CPS를 넘으면 오염이다."""
    texts = {"ko": "가" * 10, "en": "a" * 60, "ja": "あ" * 10}
    got = required_duration_ms(texts, PROFILES)
    for lang, text in texts.items():
        p = PROFILES[lang]
        cps = text_width(text, p.char_counting) / (got / 1000)
        assert cps <= p.max_cps


def test_planned_segment_is_clean_under_every_profile():
    """**이 테스트가 합성의 존재 이유다.**

    합성 결과가 규격을 위반하면 이후 검출되는 위반이 주입분인지
    합성 실패인지 구분할 수 없다. 스펙 §4.2가 "규격 위반 0건인 깨끗한 트랙"을
    요구하는 이유다.
    """
    pair = SentencePair(
        "기후 변화는 우리 시대의 가장 큰 도전입니다",
        "Climate change is the greatest challenge of our time",
    )
    planned = plan_segment(pair, "en", PROFILES)
    assert planned is not None
    for lang, text in (("ko", planned.source_text), ("en", planned.target_text)):
        violations = check_text(text, planned.duration_ms, PROFILES[lang])
        assert violations == [], f"{lang}: {violations}"


def test_impossible_segment_is_excluded_not_forced():
    """max_duration으로도 세 언어를 만족시킬 수 없으면 표본에서 뺀다."""
    pair = SentencePair("가" * 400, "a" * 2000)
    assert plan_segment(pair, "en", PROFILES) is None


def test_gap_is_fixed_and_positive():
    """세그먼트 간 간격이 0이면 경계에서 겹침 판정이 흔들린다(FR-5.1)."""
    assert GAP_MS > 0
    assert SAFETY > 1.0
