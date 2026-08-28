"""구조 신호 테스트 (요구사항정의서 FR-3.1~FR-3.5)."""

import pytest

from cuesift.segment import Segment
from cuesift.signals import SignalContext
from cuesift.signals.structural import (
    _DEGENERATION_MAX_TOKENS,
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


@pytest.fixture
def ctx_ja():
    """ko→ja. `NumberMissing`은 profile을 보지 않지만, 전각 숫자가
    일본어 자막의 현상이라는 것을 테스트가 스스로 설명하게 둔다."""
    return SignalContext(
        profile=load_builtin("ja"), glossary=None, source_lang="ko", target_lang="ja"
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


def test_degeneration_is_bounded_on_pathological_input(ctx):
    """탐지기가 탐지 대상에서 느려지면 안 된다.

    디코딩 루프에 빠진 LLM은 같은 토큰을 수천 번 뱉는다 — 이 신호가
    존재하는 이유가 그 실패 모드인데, 검사 비용이 이차로 늘면 정작
    그 입력에서 파이프라인이 멈춘다.
    """
    runaway = " ".join(["yes"] * 5000)
    sig = Degeneration().collect(_seg("원문", runaway), ctx)
    assert sig is not None
    assert sig.hard_fail is True
    # 상한을 넘겨 세지 않는다.
    assert sig.detail["count"] <= _DEGENERATION_MAX_TOKENS


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


def test_number_missing_silent_on_fullwidth_digits(ctx_ja):
    """전각 숫자는 반각과 같은 수다.

    NFKC 정규화 없이 집합 비교하면 `'５０' != '50'`이라 누락 판정되고,
    두 자리라 `multi_digit` → **hard fail**이다. hard fail은 검수 예산을
    우회하므로(FR-6.2) 이 오탐 하나가 실제 검수 비율을 부풀려
    Recall@Budget의 배수를 파괴한다.

    ja-ko 자연 오탐 41건 중 13건(31.7%)이 이 경로였다.
    """
    sig = NumberMissing().collect(
        _seg("지금은 하루 50센트 이하입니다.", "今では一日５０セント以下になりました"), ctx_ja
    )
    assert sig is None


def test_number_missing_still_hard_when_number_truly_absent(ctx_ja):
    """정규화가 오탐을 없애면서 미탐을 만들면 안 된다.

    이 테스트가 없으면 위 테스트는 **'검사를 껐다'로도 통과한다** —
    `_numbers`가 빈 리스트를 반환하게 만들어도 녹색이 된다.
    """
    sig = NumberMissing().collect(_seg("2023년 매출", "売上は好調でした"), ctx_ja)
    assert sig is not None
    assert sig.hard_fail is True
    assert sig.detail["missing"] == ["2023"]


def test_number_missing_marks_the_number_position_in_the_source(ctx):
    """누락된 숫자의 원문 위치를 span으로 낸다 (FR-7.3)."""
    seg = _seg("2024년에 시작했다", "It started")
    sig = NumberMissing().collect(seg, ctx)

    assert sig is not None
    assert len(sig.spans) == 1
    span = sig.spans[0]
    assert span.side == "source"
    assert seg.source_text[span.start : span.end] == "2024"


def test_number_missing_span_uses_pre_normalization_offsets(ctx_ja):
    """**정규화 전 위치**를 낸다.

    `_numbers`는 추출 후 NFKC 정규화하므로 `detail`의 값은 `"50"`이지만
    원문은 전각 `５０`이다. 값으로 되찾으면 `find`가 -1을 내고 하이라이트가
    조용히 빈다(§3.4). 오프셋은 원본 문자열 기준이어야 한다.
    """
    seg = _seg("５０개가 있다", "There are some")
    sig = NumberMissing().collect(seg, ctx_ja)

    assert sig is not None
    assert sig.detail["missing"] == ["50"]
    span = sig.spans[0]
    assert seg.source_text[span.start : span.end] == "５０"


def test_number_missing_span_covers_the_thousands_separator(ctx):
    """천 단위 구분자를 포함한 원문 표기 전체를 덮는다.

    `detail`의 값은 `"1000"`이지만 원문은 `1,000`이다. 구간은 원문 표기를
    가리켜야 검수자가 그 자리를 본다.
    """
    seg = _seg("1,000명이 왔다", "People came")
    sig = NumberMissing().collect(seg, ctx)

    assert sig is not None
    span = sig.spans[0]
    assert seg.source_text[span.start : span.end] == "1,000"


def test_number_missing_spans_only_cover_missing_numbers(ctx):
    """살아남은 숫자는 칠하지 않는다. **누락된 것만** 칠한다.

    원문 숫자 3개 중 2개는 번역문에 살아 있고 하나만 없다. 이때 span이
    3개 나오면 정상 번역된 `3`·`15`까지 위험 구간으로 보여 검수자가
    헛짚는다.

    **이 단언이 `spans`를 실제로 세지 않으면 테스트가 아니다** — 구현의
    `missing`을 `source_matches`로 바꾼 변이가 통과해 버린다(리뷰 실측).
    `sig is None`만 보는 형태로 되돌리지 말 것.
    """
    seg = _seg("3시 15분 20초", "3 minutes 15")
    sig = NumberMissing().collect(seg, ctx)

    assert sig is not None
    assert sig.detail["missing"] == ["20"]
    assert len(sig.spans) == 1
    assert seg.source_text[sig.spans[0].start : sig.spans[0].end] == "20"


def test_number_missing_span_stops_at_a_trailing_comma(ctx):
    """구간은 숫자에서 끝난다. 뒤따르는 쉼표는 숫자가 아니다.

    `_NUMBER`는 천 단위 구분자를 살리려고 `[\\d,]*`를 쓰므로 `"3, 4"`에서
    `"3,"`까지 매치한다. 값은 콤마를 지워 `"3"`이 되는데 구간만 2글자면
    **`detail`이 말하는 것과 칠해지는 것이 어긋난다.** 문장 부호가 위험
    구간에 섞이면 검수자는 무엇이 지적된 것인지 읽어내야 한다.
    """
    seg = _seg("3, 4가 남았다", "Some remain")
    sig = NumberMissing().collect(seg, ctx)

    assert sig is not None
    assert sig.detail["missing"] == ["3", "4"]
    assert seg.source_text[sig.spans[0].start : sig.spans[0].end] == "3"


def test_number_missing_span_count_matches_detail(ctx):
    """span 개수와 `missing` 개수가 일치한다."""
    seg = _seg("2024년과 1999년", "Some years")
    sig = NumberMissing().collect(seg, ctx)

    assert sig is not None
    assert len(sig.spans) == len(sig.detail["missing"]) == 2


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


def test_tag_lost_marks_the_missing_tag_in_the_source(ctx):
    """번역문에서 사라진 태그의 **원문** 위치를 칠한다 (FR-7.3)."""
    seg = _seg("This is <i>important</i>", "이것은 중요하다")
    sig = TagLost().collect(seg, ctx)

    assert sig is not None
    source_spans = [s for s in sig.spans if s.side == "source"]
    assert len(source_spans) == 2  # <i> 와 </i>
    assert seg.source_text[source_spans[0].start : source_spans[0].end] == "<i>"


def test_tag_lost_marks_the_invented_tag_in_the_target(ctx):
    """번역문에만 생긴 태그는 **번역문** 위치를 칠한다.

    LLM이 서식을 지어내는 사고가 있다(`TagLost` 주석). 그때 원문에는 칠할
    것이 없으므로 side가 target이어야 한다.
    """
    seg = _seg("This is important", "이것은 <b>중요하다</b>")
    sig = TagLost().collect(seg, ctx)

    assert sig is not None
    target_spans = [s for s in sig.spans if s.side == "target"]
    assert len(target_spans) == 2
    assert seg.target_text[target_spans[0].start : target_spans[0].end] == "<b>"


def test_tag_lost_span_side_splits_in_both_directions(ctx):
    """양쪽이 동시에 어긋나면 span도 양쪽에 생긴다.

    **이 신호만 side가 갈린다.** 다른 두 신호(용어·숫자 누락)는 언제나
    source라, 여기서 상수 고정 변이가 죽지 않으면 `Span.side`가 존재할
    이유 자체가 검증되지 않는다.
    """
    seg = _seg("<i>A</i>", "<b>B</b>")
    sig = TagLost().collect(seg, ctx)

    assert sig is not None
    assert {s.side for s in sig.spans} == {"source", "target"}


def test_tag_lost_ignores_attributes_when_locating(ctx):
    """속성이 있어도 태그 전체를 덮는다.

    `_TAG`가 `[^>]*?/?>`로 속성을 삼키므로 구간은 `<font color="red">`
    전체다. 이름만 덮으면 검수자가 어디까지가 그 태그인지 못 본다.
    """
    seg = _seg('<font color="red">A</font>', "A")
    sig = TagLost().collect(seg, ctx)

    assert sig is not None
    first = [s for s in sig.spans if s.side == "source"][0]
    assert seg.source_text[first.start : first.end] == '<font color="red">'


def test_tag_lost_spans_skip_the_tags_that_survived(ctx):
    """살아남은 태그는 칠하지 않는다.

    **이 테스트가 `lost`/`invented` 필터의 유일한 게이트다.** 다른 입력은
    전부 "모든 태그가 손실"이라 필터를 지우고 전부 칠해도 통과한다 —
    판정은 맞고 하이라이트만 틀린 상태로, Task 2에서 실제로 생존한 변이와
    같은 형태다.
    """
    seg = _seg("<i>A</i> and <b>B</b>", "<i>가</i> 그리고 B")
    sig = TagLost().collect(seg, ctx)

    assert sig is not None
    painted = [seg.source_text[s.start : s.end] for s in sig.spans if s.side == "source"]
    assert painted == ["<b>", "</b>"]


def test_tag_lost_paints_every_tag_of_a_name_when_one_of_many_is_lost(ctx):
    """같은 이름이 여러 개면 그 이름의 태그를 **모두** 칠한다.

    개수만 줄어든 경우 어느 것이 사라졌는지 알 방법이 없다. 하나만 골라
    칠하면 검수자가 엉뚱한 곳을 본다 — 후보를 모두 보여 세게 한다
    (`TagLost.collect` 주석).
    """
    seg = _seg("<i>A</i><i>B</i>", "<i>가</i>")
    sig = TagLost().collect(seg, ctx)

    assert sig is not None
    painted = [seg.source_text[s.start : s.end] for s in sig.spans if s.side == "source"]
    assert painted == ["<i>", "</i>", "<i>", "</i>"]


# --- FR-3.1 미번역 잔존 (짧은 세그먼트) ---


def test_untranslated_silent_on_short_segment_with_one_stray_char(ctx):
    """짧은 대사에서는 비율 분모가 작아 한 글자에도 임계를 넘는다.

    자막은 감탄사·짧은 응답이 매우 흔하다.
    """
    assert Untranslated().collect(_seg("안녕", "Hi 가"), ctx) is None
