"""`TriageOutcome`의 파생 수치 (FR-7.2 · 설계 §7.1)."""

from __future__ import annotations

import pytest

from cuesift.report import TriageOutcome
from cuesift.segment import Segment, SegmentRisk, Signal


def _risk(
    seg_id: str,
    *,
    selected: bool = False,
    hard_fail: bool = False,
    reasons: list[str] | None = None,
    score: float = 0.5,
    signals: list[Signal] | None = None,
) -> SegmentRisk:
    return SegmentRisk(
        segment_id=seg_id,
        signals=[] if signals is None else signals,
        risk_score=score,
        hard_fail=hard_fail,
        selected=selected,
        reasons=[] if reasons is None else reasons,
    )


def _segment(seg_id: str, *, index: int = 0) -> Segment:
    return Segment(
        id=seg_id,
        index=index,
        start_ms=0,
        end_ms=1000,
        source_text="원문",
        target_text="target",
    )


def _outcome(
    *,
    risks: tuple[SegmentRisk, ...],
    excluded_failures: int = 0,
    segments: tuple[Segment, ...] | None = None,
) -> TriageOutcome:
    """기본값은 `segments`를 `risks`에서 파생해 불변식을 만족시킨다.

    `segments`를 명시하면 그 파생을 우회한다 - 길이 불일치 방어를 실제로
    발동시키려면 헬퍼가 대신 맞춰 주지 않아야 한다.
    """
    return TriageOutcome(
        source_lang="ko",
        target_lang="en",
        profile_name="en",
        policy_label="예산 10%",
        policy_kind="budget",
        policy_value=0.1,
        risks=risks,
        segments=(
            tuple(_segment(r.segment_id, index=i) for i, r in enumerate(risks))
            if segments is None
            else segments
        ),
        excluded_failures=excluded_failures,
        usage=None,
    )


def test_total은_triaged와_excluded의_합이다() -> None:
    """설계 §6.2 — 이 산수가 파일 안에서 검산된다.

    셋을 하나로 합치면 `review_ratio`의 분모가 무엇인지 소비자가 알 수 없고,
    배수의 분모가 조용히 틀린다.

    **픽스처를 비대칭으로 짠다.** risks 1개·선별 0개로는 `len(risks)`도,
    여집합 `sum(not selected)`도, `len(segments)`도 전부 1을 내 `triaged`를
    무엇으로 바꿔 놓아도 통과한다. 선별 1개를 섞어 여집합이 다른 값을 내게 한다.
    """
    outcome = _outcome(
        risks=(_risk("00000", selected=True), _risk("00001")),
        excluded_failures=3,
    )

    assert outcome.triaged_segments == 2  # 여집합(선별 안 된 것)은 1이다
    assert outcome.excluded_failures == 3
    assert outcome.total_segments == 5


def test_selected는_참인_것만_원래_순서로_낸다() -> None:
    """이 튜플의 순서가 곧 `review.json`의 `segments[]` 순서라 NFR-3 대상이다.

    **선별을 2개로 둔다.** 1개뿐이면 `tuple(reversed(...))`로 뒤집어도 결과가
    같아 순서가 검증되지 않는다 - 같은 파일이 `signal_hits`에는 정렬 테스트를
    따로 두고 있어 비대칭이었다.
    """
    outcome = _outcome(
        risks=(
            _risk("00000", selected=True),
            _risk("00001"),
            _risk("00002", selected=True),
        )
    )

    assert outcome.selected_for_review == 2
    assert [r.segment_id for r in outcome.selected] == ["00000", "00002"]


def test_signal_hits는_선별되지_않은_것도_건수로_센다() -> None:
    """집계는 `risks` **전체**를 본다.

    선별분으로 좁히면 "예산 밖으로 밀린 위험"이 사라져 사용자가 다음 예산을
    정할 근거를 잃는다. 화면 요약이 이미 같은 규칙을 따른다.

    **같은 사유를 2회 담는다.** 모든 사유가 1회씩이면 집계를 버리고 존재
    여부만 내는 구현(`{k: 1 for k in sorted(counts)}`)도, 반환형을 `set`으로
    바꾼 구현도 통과한다 - 프로퍼티 이름이 "적발 **건수**"이고 화면이
    `"{name} {count}개"`로 찍는데도 그렇다.
    """
    outcome = _outcome(
        risks=(
            _risk("00000", selected=True, reasons=["spec.violation"]),
            _risk("00001", selected=False, reasons=["struct.empty", "spec.violation"]),
        )
    )

    assert outcome.signal_hits == {"spec.violation": 2, "struct.empty": 1}


def test_signal_hits는_이름순으로_정렬된다() -> None:
    """NFR-3 재현성 — `Counter`의 순서는 삽입 순이라 세그먼트 순서가 바뀌면 흔들린다."""
    outcome = _outcome(
        risks=(
            _risk("00000", reasons=["struct.empty"]),
            _risk("00001", reasons=["glossary.miss"]),
        )
    )

    assert list(outcome.signal_hits) == ["glossary.miss", "struct.empty"]


def test_hard_fail_count는_전체에서_센다() -> None:
    """**3개 중 2개로 비대칭이다.** 2개 중 1개면 여집합(`if not r.hard_fail`)도
    똑같이 1을 내 뒤집힌 구현이 살아남는다. hard fail은 검수 예산을 우회하므로
    (FR-6.2) 이 수치가 뒤집히면 실제 검수 비율이 부풀어 Recall@Budget이 무너진다.
    """
    outcome = _outcome(
        risks=(
            _risk("00000", hard_fail=True),
            _risk("00001", hard_fail=True),
            _risk("00002"),
        )
    )

    assert outcome.hard_fail_count == 2  # 여집합은 1이다


def test_review_ratio는_triaged를_분모로_쓴다() -> None:
    """실패분을 분모에 넣으면 README 배수가 무너진다 (설계 §6.2)."""
    outcome = _outcome(
        risks=(_risk("00000", selected=True), _risk("00001"), _risk("00002"), _risk("00003")),
        excluded_failures=6,
    )

    # 분모가 triaged(4)면 0.25, total(10)이면 0.1이다.
    assert outcome.review_ratio == pytest.approx(0.25)


def test_risks가_비면_review_ratio는_0이다() -> None:
    """전량 번역 실패 경로. `ZeroDivisionError`가 아니라 0.0이어야 한다."""
    outcome = _outcome(risks=(), excluded_failures=10)

    assert outcome.review_ratio == 0.0
    assert outcome.total_segments == 10
    assert outcome.selected_for_review == 0


def test_segments와_risks의_길이가_다르면_거부한다() -> None:
    """`segments`는 `risks`와 같은 집합이라는 것이 이 모델의 계약이다.

    깨진 채로 통과시키면 리포트 생성기의 `by_id[risk.segment_id]`가 KeyError를
    내는데, 그 시점은 파일을 쓰는 도중이라 어느 조합이 어긋났는지 스택에 남지
    않는다. 생성 시점으로 실패를 앞당긴다.
    """
    with pytest.raises(ValueError, match="길이가 다르다"):
        _outcome(
            risks=(_risk("00000"), _risk("00001")),
            segments=(_segment("00000"),),
        )


def test_excluded_failures가_음수면_거부한다() -> None:
    """음수는 `total_segments`를 `triaged_segments`보다 작게 만들어 화면이
    "2개 중 5개 검수"라는 불가능한 요약을 낸다. 프로그램은 정상 종료하고 종료
    코드도 0이라 어떤 게이트에도 걸리지 않는다.
    """
    with pytest.raises(ValueError, match="음수다"):
        _outcome(risks=(_risk("00000"),), excluded_failures=-5)
