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
) -> TriageOutcome:
    return TriageOutcome(
        source_lang="ko",
        target_lang="en",
        profile_name="en",
        policy_label="예산 10%",
        policy_kind="budget",
        policy_value=0.1,
        risks=risks,
        segments=tuple(_segment(r.segment_id, index=i) for i, r in enumerate(risks)),
        excluded_failures=excluded_failures,
        usage=None,
    )


def test_total은_triaged와_excluded의_합이다() -> None:
    """설계 §6.2 — 이 산수가 파일 안에서 검산된다.

    셋을 하나로 합치면 `review_ratio`의 분모가 무엇인지 소비자가 알 수 없고,
    배수의 분모가 조용히 틀린다.
    """
    outcome = _outcome(risks=(_risk("00000"),), excluded_failures=3)

    assert outcome.triaged_segments == 1
    assert outcome.excluded_failures == 3
    assert outcome.total_segments == 4


def test_selected는_selected_플래그가_참인_것만_낸다() -> None:
    outcome = _outcome(risks=(_risk("00000", selected=True), _risk("00001")))

    assert outcome.selected_for_review == 1
    assert [r.segment_id for r in outcome.selected] == ["00000"]


def test_signal_hits는_선별되지_않은_것도_센다() -> None:
    """집계는 `risks` **전체**를 본다.

    선별분으로 좁히면 "예산 밖으로 밀린 위험"이 사라져 사용자가 다음 예산을
    정할 근거를 잃는다. 화면 요약이 이미 같은 규칙을 따른다.
    """
    outcome = _outcome(
        risks=(
            _risk("00000", selected=True, reasons=["spec.violation"]),
            _risk("00001", selected=False, reasons=["struct.empty"]),
        )
    )

    assert outcome.signal_hits == {"spec.violation": 1, "struct.empty": 1}


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
    outcome = _outcome(risks=(_risk("00000", hard_fail=True), _risk("00001")))

    assert outcome.hard_fail_count == 1


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
