"""규격 검사 테스트 (요구사항정의서 FR-5.1, FR-3.8)."""

from cuesift.segment import Segment
from cuesift.spec import check_empty_cues, check_overlaps, check_text, check_track, load_builtin

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


def test_long_segment_overlapping_a_later_one_is_detected():
    """긴 세그먼트가 뒤쪽 세그먼트를 덮는데 사이에 안 겹치는 것이 끼어 있는 경우.

    인접 쌍만 비교하면 C가 검사에서 통째로 빠진다.
    """
    segs = [
        Segment(id="A", index=0, start_ms=0, end_ms=10000, source_text="가"),
        Segment(id="B", index=1, start_ms=100, end_ms=200, source_text="나"),
        Segment(id="C", index=2, start_ms=5000, end_ms=6000, source_text="다"),
    ]
    result = check_overlaps(segs)
    assert set(result) == {"B", "C"}


def test_overlap_amount_is_the_actual_intersection():
    """포함 관계에서 겹침량은 앞 세그먼트의 끝이 아니라 실제 교집합이다.

    B(100~200)는 A(0~10000) 안에 완전히 들어 있으므로 겹침은 100ms다.
    """
    segs = [
        Segment(id="A", index=0, start_ms=0, end_ms=10000, source_text="가"),
        Segment(id="B", index=1, start_ms=100, end_ms=200, source_text="나"),
        Segment(id="C", index=2, start_ms=5000, end_ms=6000, source_text="다"),
    ]
    result = check_overlaps(segs)
    assert result["B"].measured == 100
    assert result["C"].measured == 1000


def test_check_text_does_not_flag_empty_text():
    """빈 텍스트는 check_text의 대상이 아니다 (설계 §4.2).

    여기에 빈 큐 판정을 넣으면 translate 경로에서 struct.empty와 이중 계산되고,
    그 부풀림이 spec.violation 점수를 통해 위험도로 흘러 검수 비율을 밀어 올린다.
    검수 비율은 Recall@Budget 배수의 분모다 — 여기서 새면 프로젝트의 핵심 주장이 무너진다.
    """
    profile = load_builtin("ko")
    assert check_text("", 2000, profile) == []
    assert check_text("   \n  ", 2000, profile) == []


def test_check_empty_cues_flags_blank_and_whitespace_only():
    """FR-5.1 — 텍스트 없는 큐는 배포 자막의 결함이다."""
    segments = [
        Segment(id="00000", index=0, start_ms=0, end_ms=2000, source_text="있는 텍스트"),
        Segment(id="00001", index=1, start_ms=2500, end_ms=4000, source_text=""),
        Segment(id="00002", index=2, start_ms=4500, end_ms=6000, source_text="   \n  "),
    ]

    found = check_empty_cues(segments)

    assert set(found) == {"00001", "00002"}
    assert found["00001"].kind == "empty_cue"
    assert found["00002"].kind == "empty_cue"


def test_check_empty_cues_is_silent_on_a_clean_track():
    segments = [
        Segment(id="00000", index=0, start_ms=0, end_ms=2000, source_text="첫 줄"),
        Segment(id="00001", index=1, start_ms=2500, end_ms=4000, source_text="둘째 줄"),
    ]
    assert check_empty_cues(segments) == {}


def test_check_track_orders_violations_by_list_order_not_by_time():
    """리스트 순서가 1차 정렬이다. `start_ms`도 `segment_id`도 아니다 (설계 §4).

    **인제스트는 시간순을 보장하지 않는다** — pysubs2도 `_to_segments`도 정렬하지 않으므로
    리스트 순서가 곧 **파일 순서**다. 사람이 파일을 위에서 아래로 읽으므로 그것이 맞다.

    세 정렬 키(리스트·`start_ms`·`segment_id`)가 동시에 단조인 픽스처로는 무엇이 1차인지
    구분할 수 없다. 여기서는 셋을 일부러 어긋나게 둔다.
    """
    segments = [
        # 덮개 큐(00002) 안에 들어가 겹친다.
        Segment(id="00000", index=0, start_ms=1000, end_ms=3000, source_text="본문"),
        # 뒤의 둘을 통째로 덮는 긴 큐. 3줄이라 line_count. 폭은 전부 16 미만이다.
        # **덮개를 리스트 가운데 두는 것이 이 픽스처의 요점이다.** 덮개는 반드시 가장
        # 이른 start_ms를 갖는데, 그것을 리스트 맨 앞에 두면 리스트 순서와 start_ms
        # 순서가 같아져 `sorted(key=start_ms)` 변이가 무탐지로 빠져나간다.
        Segment(
            id="00002", index=1, start_ms=0, end_ms=7000, source_text="첫 줄\n둘째 줄\n셋째 줄"
        ),
        # 겹치면서 비어 있다. duration 1000ms는 min 833을 넘으므로 duration_short는 없다.
        Segment(id="00001", index=2, start_ms=4000, end_ms=5000, source_text=""),
    ]

    found = check_track(segments, KO)

    assert [(tv.segment_id, tv.violation.kind) for tv in found] == [
        ("00000", "overlap"),
        ("00002", "line_count"),
        ("00001", "overlap"),
        ("00001", "empty_cue"),
    ]


def test_check_track_keeps_multiple_violations_of_one_segment_together():
    """한 세그먼트가 여러 위반을 내면 이어 붙는다."""
    segments = [
        Segment(
            id="00000",
            index=0,
            start_ms=0,
            end_ms=1000,
            source_text="짧은 줄\n열여섯 자를 확실히 넘기는 아주 긴 두 번째 줄",
        ),
    ]

    found = check_track(segments, KO)

    assert [tv.violation.kind for tv in found] == ["line_length", "cps"]
    assert all(tv.segment_id == "00000" for tv in found)
    # line_index는 0-based다. 두 번째 줄이 길다.
    assert found[0].violation.line_index == 1
    assert found[0].violation.measured == 22.0
    # CPS는 글자 수가 아니라 표시 폭으로 잰다 — 3.5 + 22.0 = 25.5.
    assert found[1].violation.measured == 25.5


def test_track_violation_carries_start_ms_for_output():
    """cli가 타임코드를 찍으려면 위반에 시각이 붙어 있어야 한다."""
    segments = [Segment(id="00000", index=0, start_ms=83400, end_ms=84400, source_text="")]

    found = check_track(segments, KO)

    assert found[0].start_ms == 83400


def test_check_track_is_silent_on_a_clean_track():
    """오탐 회귀 그물을 겸한다.

    기호만 있는 큐(`♪`·`…`·`.`)는 자막에서 정상이고 실제 코퍼스에 존재한다
    (TED2020 151만 줄 실측: 5건). 이 프로젝트에서 **오탐은 검수 비율을 부풀려
    Recall@Budget 지표 자체를 파괴하므로** 미탐보다 비싸다. 누군가 빈 큐 판정을
    "개선"이라며 넓히면 여기가 먼저 깨져야 한다.
    """
    segments = [
        Segment(id="00000", index=0, start_ms=0, end_ms=3000, source_text="안녕하세요\n두 번째 줄"),
        Segment(id="00001", index=1, start_ms=3500, end_ms=6000, source_text="세 번째 큐"),
        Segment(id="00002", index=2, start_ms=6500, end_ms=8000, source_text="♪"),
        Segment(id="00003", index=3, start_ms=8500, end_ms=10000, source_text="…"),
        Segment(id="00004", index=4, start_ms=10500, end_ms=12000, source_text="."),
    ]
    assert check_track(segments, KO) == []


def test_check_track_keeps_every_violation_of_one_cue_in_a_fixed_order():
    """`check_overlaps`와 `check_empty_cues`가 **같은 키 공간**을 쓴다.

    둘 다 `dict[str, SpecViolation]`을 `seg.id`로 키잉하므로 `{**overlaps, **empties}`로
    합치면 한쪽이 **조용히 소실된다.** 그것이 이 함수를 쓰는 가장 자연스러운 오답이다.

    한 세그먼트 안의 순서도 계약이다 — `check_text`가 낸 것들 → `overlap` → `empty_cue`.
    Task 5의 출력이 이 순서를 그대로 쓴다.

    빈 큐가 앞 큐의 타임코드를 복제한 채 남는 것은 흔한 저작 아티팩트라
    "빈 큐이면서 겹침"은 인위적 조합이 아니다.
    """
    segments = [
        Segment(id="00000", index=0, start_ms=0, end_ms=5000, source_text="본문"),
        # 앞 큐(0~5000) 안에 들어가고(겹침), 텍스트가 없고(빈 큐), 노출이 833ms 미만이다.
        # 세 판정이 동시에 걸려야 세그먼트 내부 순서가 관찰된다 — 한 건만 나오는
        # 픽스처로는 append 순서를 바꾸는 변이가 전부 통과한다.
        Segment(id="00001", index=1, start_ms=1000, end_ms=1500, source_text=""),
    ]

    kinds = [tv.violation.kind for tv in check_track(segments, KO) if tv.segment_id == "00001"]

    assert kinds == ["duration_short", "overlap", "empty_cue"]


def test_check_track_accepts_a_generator_not_just_a_list():
    """제너레이터를 넣어도 리스트와 결과가 같아야 한다.

    `check_track`은 입력을 **3회 순회한다**(`check_overlaps` · `check_empty_cues` · 본체 루프).
    제너레이터는 첫 순회에서 소진되므로 방어하지 않으면 본체 루프가 빈 채로 돌아
    **위반 0건**이 되고, `check`는 그것을 "깨끗한 파일"로 읽어 종료 코드 0을 낸다.
    실패가 예외가 아니라 **조용한 통과**라 이 저장소가 명시적으로 금지한 형태다.

    `Sequence[Segment]` 애노테이션은 이것을 막지 못한다 — 이 리포에는 타입 검사 게이트가
    없고(dev 의존성이 pytest·pytest-cov·ruff 셋뿐) ruff의 `E,F,I,UP,B,SIM`도 잡지 않는다.
    """
    segments = [
        Segment(id="00000", index=0, start_ms=0, end_ms=5000, source_text="본문"),
        Segment(id="00001", index=1, start_ms=1000, end_ms=1500, source_text=""),
    ]

    from_list = [(tv.segment_id, tv.violation.kind) for tv in check_track(segments, KO)]
    from_generator = [
        (tv.segment_id, tv.violation.kind) for tv in check_track((s for s in segments), KO)
    ]

    # 둘 다 빈 리스트면 위 단언은 아무것도 지키지 않는다. 픽스처가 위반을 내는지 먼저 못 박는다.
    assert from_list != []
    assert from_generator == from_list


def test_translate_path_does_not_import_the_empty_cue_check():
    """`check_empty_cues`는 `check` 경로 전용이라는 계약을 고정한다.

    `spec/check.py`의 독스트링이 "`translate` 경로는 이 함수를 부르지 않는다"를
    계약으로 선언하지만, **독스트링만 있는 불변식은 게이트가 아니다.**
    누군가 `signals/derived.py`에 배선해도 지금은 전체 스위트가 통과한다.
    """
    import cuesift.signals.derived as derived

    assert not hasattr(derived, "check_empty_cues")
