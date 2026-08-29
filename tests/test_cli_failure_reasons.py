"""번역 실패 사유 렌더링 (파킹 #2 · FR-2.6).

`SegmentFailure.reason`은 4곳에서 생산되는데 소비자가 0건이었다 -
`engine.py:78`이 명시한 "서버인지 모델인지"를 화면이 말하지 않았다.
"""

from __future__ import annotations

from cuesift.cli import _format_failure_lines
from cuesift.translate import SegmentFailure


def _f(seg_id: str, reason: str, attempts: int) -> SegmentFailure:
    return SegmentFailure(segment_id=seg_id, reason=reason, attempts=attempts)


def test_실패가_없으면_줄을_내지_않는다() -> None:
    assert _format_failure_lines(()) == []


def test_사유별로_묶고_ID를_전부_나열한다() -> None:
    lines = _format_failure_lines(
        (
            _f("00003", "provider_error", 4),
            _f("00007", "provider_error", 4),
            _f("00012", "empty_translation", 1),
        )
    )
    # **ID 나열을 유지한다.** 원문이 남은 자막은 겉보기에 정상이라 개수만
    # 보고 넘기면 미번역 자막이 그대로 배포된다(기존 독스트링의 근거).
    assert lines == [
        "  실패 세그먼트(원문 유지) 3건:",
        "    provider_error 2건 (시도 4회): 00003, 00007",
        "    empty_translation 1건 (시도 1회): 00012",
    ]


def test_같은_사유에_시도_횟수가_다르면_범위로_적는다() -> None:
    lines = _format_failure_lines(
        (_f("00001", "invalid_response", 1), _f("00002", "invalid_response", 4))
    )
    assert lines == [
        "  실패 세그먼트(원문 유지) 2건:",
        "    invalid_response 2건 (시도 1~4회): 00001, 00002",
    ]


def test_사유_순서가_입력_순서에_좌우되지_않는다() -> None:
    """**같은 실행을 두 번 돌리면 같은 화면이 나와야 한다** (NFR-3).

    dict 삽입 순서를 그대로 쓰면 배치 스케줄에 따라 줄 순서가 바뀌고,
    로그를 diff하는 CI가 매번 변경을 본다.
    """
    a = _format_failure_lines((_f("1", "empty_translation", 1), _f("2", "provider_error", 1)))
    b = _format_failure_lines((_f("2", "provider_error", 1), _f("1", "empty_translation", 1)))
    assert a[1:] == b[1:]


def test_알_수_없는_사유도_그대로_낸다() -> None:
    """**화이트리스트로 거르지 않는다.** `engine.py`가 사유를 하나 더 넣었을 때
    화면에서 조용히 사라지면 그것이 정확히 이 태스크가 고치는 결함이다."""
    lines = _format_failure_lines((_f("00001", "새_사유", 2),))
    assert "새_사유 1건 (시도 2회): 00001" in lines[1]
