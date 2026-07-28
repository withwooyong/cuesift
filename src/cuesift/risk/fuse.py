"""신호 융합 (요구사항정의서 FR-6.1, FR-6.2, FR-6.4).

**가중 평균이지 합이 아니다.** 합을 쓰면 신호가 많이 붙은 세그먼트가
각 신호의 점수와 무관하게 상위로 올라가고, 결과가 0~1을 벗어나
triage의 정렬·임계 비교가 깨진다.

**가중치는 튜닝하지 않는다**(스펙 §6.3). 같은 데이터에서 맞춘 값은
새 데이터에서 재현되지 않는다. 튜닝이 필요해지면 분리된 검증 세트를
만드는 것이 순서다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from cuesift.segment import SegmentRisk, Signal

# 등록된 신호 8종에 균등 가중. 무튜닝 기본값이다.
DEFAULT_WEIGHTS: dict[str, float] = {
    "struct.untranslated": 1.0,
    "struct.empty": 1.0,
    "struct.degeneration": 1.0,
    "struct.number_missing": 1.0,
    "struct.tag_lost": 1.0,
    "spec.violation": 1.0,
    "glossary.miss": 1.0,
    "length.ratio": 1.0,
}

# 가중치 표에 없는 신호의 기본값. v0.2에서 QE 신호가 꽂혀도
# 설정 없이 동작해야 한다(FR-6.5).
_FALLBACK_WEIGHT = 1.0


def fuse(
    segment_id: str,
    signals: Sequence[Signal],
    weights: Mapping[str, float] | None = None,
) -> SegmentRisk:
    """신호 목록을 위험도 하나로 합성한다."""
    table = DEFAULT_WEIGHTS if weights is None else weights

    for name, weight in table.items():
        if weight < 0:
            # 음수는 "위험할수록 안전"을 뜻하게 되어 정렬이 뒤집힌다.
            raise ValueError(f"가중치가 음수다: {name}={weight}")

    hard_fail = any(s.hard_fail for s in signals)

    # 0점 신호를 사유에 넣으면 리포트가 "이것 때문에 뽑혔다"고 거짓말한다.
    reasons = [s.name for s in signals if s.score > 0.0]

    if hard_fail:
        # FR-6.2 — 가중합을 우회한다. 우회하지 않으면 다른 신호가 전부
        # 0인 세그먼트의 hard fail이 희석돼 예산 밖으로 밀린다.
        return SegmentRisk(
            segment_id=segment_id,
            signals=list(signals),
            risk_score=1.0,
            hard_fail=True,
            reasons=reasons,
        )

    total_weight = sum(table.get(s.name, _FALLBACK_WEIGHT) for s in signals)
    if total_weight <= 0:
        score = 0.0
    else:
        weighted = sum(table.get(s.name, _FALLBACK_WEIGHT) * s.score for s in signals)
        score = weighted / total_weight

    return SegmentRisk(
        segment_id=segment_id,
        signals=list(signals),
        risk_score=min(1.0, max(0.0, score)),
        hard_fail=False,
        reasons=reasons,
    )
