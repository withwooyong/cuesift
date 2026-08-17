"""`cuesift translate`의 트리아지 배선 검증 (FR-6.3 · 설계 §5·§7).

**네트워크를 타지 않는다.** `_build_provider`를 monkeypatch해 가짜를 꽂는다 -
`test_cli_translate.py`와 같은 방식이다.
"""

from __future__ import annotations

import pytest

from cuesift.cli import _parse_review_budget


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10%", 0.10),
        ("0.1", 0.10),
        ("5%", 0.05),
        ("0", 0.0),
        ("0%", 0.0),
        ("100%", 1.0),
        ("1.0", 1.0),
        # `1`은 100%다. `1%`를 의도한 사용자가 전량을 받지만 Tier 0만 쓰므로
        # LLM 비용이 0이고 요약이 "실제 100.0%"를 내 즉시 드러난다(설계 §5.2).
        ("1", 1.0),
        ("  10%  ", 0.10),
    ],
)
def test_비율을_파싱한다(raw: str, expected: float) -> None:
    assert _parse_review_budget(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw",
    [
        "50",  # 개수 지정 - 범위 밖이다
        "-5%",
        "1.5",
        "101%",
        "abc",
        "",
        "   ",
        "%",
        # NaN·inf를 비교 연산의 우연에 맡기지 않는다. `nan <= 1.0`이 False라
        # 범위 검사에서 거부되는 것이 **의도**이므로 테스트로 못 박는다 -
        # `policy.py`가 같은 부류의 결함(Task 9)을 겪은 전례가 있다.
        "nan",
        "inf",
    ],
)
def test_잘못된_값은_ValueError다(raw: str) -> None:
    with pytest.raises(ValueError):
        _parse_review_budget(raw)


def test_개수를_주면_비율로_지정하라고_안내한다() -> None:
    with pytest.raises(ValueError, match="비율로 지정하라"):
        _parse_review_budget("50")
