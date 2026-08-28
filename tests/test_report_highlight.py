"""구간 분할 테스트 (FR-7.3 · 설계 D6)."""

from __future__ import annotations

from cuesift.report.highlight import Fragment, split_spans
from cuesift.segment import Span


def _s(start: int, end: int) -> Span:
    return Span(start=start, end=end, side="source")


def test_구간이_없으면_조각이_하나다() -> None:
    assert split_spans("abcdef", []) == [Fragment(text="abcdef", signals=())]


def test_구간_하나가_텍스트_일부를_덮는다() -> None:
    assert split_spans("abcdef", [("sig", _s(2, 4))]) == [
        Fragment(text="ab", signals=()),
        Fragment(text="cd", signals=("sig",)),
        Fragment(text="ef", signals=()),
    ]


def test_포함_관계인_겹침은_안쪽_조각이_두_신호를_갖는다() -> None:
    """A=[0,4) B=[0,8) - 경계점 {0,4,8,10}."""
    result = split_spans("0123456789", [("A", _s(0, 4)), ("B", _s(0, 8))])

    assert [f.text for f in result] == ["0123", "4567", "89"]
    assert [f.signals for f in result] == [("A", "B"), ("B",), ()]


def test_교차하는_겹침도_평평하게_쪼갠다() -> None:
    """A=[0,5) B=[3,8) - 중첩 태그로는 표현할 수 없는 경우다."""
    result = split_spans("0123456789", [("A", _s(0, 5)), ("B", _s(3, 8))])

    assert [f.text for f in result] == ["012", "34", "567", "89"]
    assert [f.signals for f in result] == [("A",), ("A", "B"), ("B",), ()]


def test_인접한_구간은_합쳐지지_않는다() -> None:
    """A=[0,4) B=[4,8) - 경계가 맞닿아도 별개다."""
    result = split_spans("0123456789", [("A", _s(0, 4)), ("B", _s(4, 8))])

    assert [f.signals for f in result] == [("A",), ("B",), ()]


def test_텍스트_끝에_닿는_구간은_꼬리_조각을_만들지_않는다() -> None:
    result = split_spans("abcd", [("A", _s(2, 4))])

    assert [f.text for f in result] == ["ab", "cd"]


def test_빈_구간은_무시한다() -> None:
    """start == end면 덮을 문자가 없다. 조각도 경계점도 만들지 않는다."""
    assert split_spans("abcd", [("A", _s(2, 2))]) == [Fragment(text="abcd", signals=())]


def test_범위를_벗어난_구간은_무시한다() -> None:
    """수집기가 잘못된 오프셋을 내도 렌더러가 죽지 않는다.

    죽는 대신 그 구간만 빠진다 - 리포트 전체를 잃는 것보다 낫다.
    """
    assert split_spans("abcd", [("A", _s(2, 99))]) == [Fragment(text="abcd", signals=())]


def test_음수_start를_가진_구간은_무시한다() -> None:
    """`Span`은 `end < start`만 막는다 - 음수 start는 생성자를 통과한다.

    거르지 않으면 경계점에 `-3`이 섞여 `text[-3:0]`이 **빈 조각**을 낸다.
    빈 `<mark>`는 화면에 보이지 않으므로 실물 확인으로도 안 잡힌다.
    """
    assert split_spans("abcd", [("A", _s(-3, 2))]) == [Fragment(text="abcd", signals=())]


def test_빈_텍스트는_조각이_없다() -> None:
    assert split_spans("", [("A", _s(0, 0))]) == []


def test_신호_이름은_정렬해_담는다() -> None:
    """출력이 결정론적이어야 한다 - 같은 입력이 다른 HTML을 내면 안 된다.

    이름을 넷 둔 것은 파이썬이 문자열 해시를 프로세스마다 무작위화하기
    때문이다. `sorted`를 지운 변이가 집합 순회 순서만으로 정렬과 같아지면
    이 게이트는 그날만 통과한다 - **이름 수를 줄이면 게이트가 확률적으로
    샌다.** 실측으로 이름 둘이면 20회 중 2회 생존, 넷이면 0회였다.
    """
    spans = [("zebra", _s(0, 4)), ("alpha", _s(0, 4)), ("mike", _s(0, 4)), ("bravo", _s(0, 4))]

    result = split_spans("abcd", spans)

    assert result[0].signals == ("alpha", "bravo", "mike", "zebra")


def test_같은_신호가_두_구간을_덮어도_이름은_한_번만_담긴다() -> None:
    result = split_spans("abcdef", [("A", _s(0, 3)), ("A", _s(1, 4))])

    assert result[1].signals == ("A",)
