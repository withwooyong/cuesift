"""규격 검사 테스트 (요구사항정의서 FR-5.1, FR-3.8)."""

from cuesift.segment import Segment
from cuesift.spec import check_overlaps, check_text, load_builtin

KO = load_builtin("ko")  # 16자/줄, latin_half, 12 CPS, 2줄, 833~7000ms


def _kinds(violations):
    return sorted(v.kind for v in violations)


def test_conforming_text_has_no_violations():
    # 8자 / 2000ms = 4 CPS < 12
    assert check_text("안녕하세요반갑", 2000, KO) == []


def test_line_too_long_is_reported_with_line_index():
    """17자는 ko 한도 16자를 넘는다."""
    text = "가나다라마바사아자차카타파하거너더"
    violations = check_text(text, 7000, KO)
    long = [v for v in violations if v.kind == "line_length"]
    assert len(long) == 1
    assert long[0].line_index == 0
    assert long[0].measured == 17.0
    assert long[0].limit == 16.0


def test_only_the_offending_line_is_reported():
    """두 줄 중 둘째 줄만 길면 둘째 줄만 지목해야 한다.
    하이라이트가 엉뚱한 줄을 가리키면 검수자가 리포트를 신뢰하지 않는다."""
    text = "짧은줄\n가나다라마바사아자차카타파하거너더"
    long = [v for v in check_text(text, 7000, KO) if v.kind == "line_length"]
    assert len(long) == 1
    assert long[0].line_index == 1


def test_too_many_lines():
    text = "한줄\n두줄\n세줄"
    v = [x for x in check_text(text, 7000, KO) if x.kind == "line_count"]
    assert len(v) == 1
    assert v[0].measured == 3
    assert v[0].limit == 2


def test_cps_uses_the_configured_counting_mode():
    """ko는 latin_half다. 라틴 20자는 폭 10.0이므로 1000ms에서 10 CPS다.
    grapheme으로 셌다면 20 CPS가 되어 위반이 됐을 것이다."""
    assert [v for v in check_text("a" * 20, 1000, KO) if v.kind == "cps"] == []


def test_cps_violation_is_reported():
    # 폭 12.0(한글 12자) / 500ms = 24 CPS > 12
    v = [x for x in check_text("가나다라마바사아자차카타", 500, KO) if x.kind == "cps"]
    assert len(v) == 1
    assert v[0].measured == 24.0


def test_cps_counts_the_whole_text_not_per_line():
    """줄바꿈은 화면에 동시에 보이므로 읽기 속도는 전체 기준이다.
    줄마다 따로 재면 2줄 자막의 CPS가 절반으로 과소평가된다."""
    v = [x for x in check_text("가나다라마바\n사아자차카타", 500, KO) if x.kind == "cps"]
    assert len(v) == 1
    assert v[0].measured == 24.0


def test_duration_too_short():
    v = [x for x in check_text("가", 400, KO) if x.kind == "duration_short"]
    assert len(v) == 1
    assert v[0].limit == 833


def test_duration_too_long():
    v = [x for x in check_text("가", 9000, KO) if x.kind == "duration_long"]
    assert len(v) == 1
    assert v[0].limit == 7000


def test_zero_duration_does_not_divide_by_zero():
    """duration 0은 파싱 사고이지 CPS 무한대가 아니다. 예외로 죽으면
    자막 하나 때문에 전체 실행이 멈춘다."""
    violations = check_text("가나다", 0, KO)
    assert "duration_short" in _kinds(violations)
    assert "cps" not in _kinds(violations)


def test_empty_text_has_no_length_or_cps_violation():
    """빈 값은 FR-3.2가 hard fail로 따로 잡는다. 규격 검사가 중복
    보고하면 신호 하나가 두 번 세어져 위험도가 부풀려진다."""
    violations = check_text("", 2000, KO)
    assert _kinds(violations) == []


def test_overlapping_segments_are_detected():
    segs = [
        Segment(id="a", index=0, start_ms=0, end_ms=2000, source_text="가"),
        Segment(id="b", index=1, start_ms=1500, end_ms=3000, source_text="나"),
    ]
    result = check_overlaps(segs)
    assert set(result) == {"b"}
    assert result["b"].measured == 500


def test_touching_segments_do_not_overlap():
    """end == start는 겹침이 아니다. 경계에서 오탐이 나면
    모든 자막이 위반으로 표시된다."""
    segs = [
        Segment(id="a", index=0, start_ms=0, end_ms=2000, source_text="가"),
        Segment(id="b", index=1, start_ms=2000, end_ms=3000, source_text="나"),
    ]
    assert check_overlaps(segs) == {}


def test_overlaps_are_checked_in_time_order_not_list_order():
    """입력이 정렬돼 있지 않아도 판정이 같아야 한다."""
    segs = [
        Segment(id="b", index=1, start_ms=1500, end_ms=3000, source_text="나"),
        Segment(id="a", index=0, start_ms=0, end_ms=2000, source_text="가"),
    ]
    assert set(check_overlaps(segs)) == {"b"}
