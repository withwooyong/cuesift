"""신호 융합 (요구사항정의서 FR-6.1, FR-6.2, FR-6.4).

**noisy-or다.** `1 - ∏(1 - sᵢ)^wᵢ`로 "적어도 하나가 진짜일 가능성"을 낸다.

가중 평균을 쓰면 **문제를 하나 더 찾을 때 위험도가 내려간다** —
규격 위반(1.0) 하나는 1.0인데 용어 위반(0.5)이 붙으면 0.75가 되어
검수 큐에서 밀려난다. 실측으로 그 비용을 쟀다: 오라클 대비 달성률이
예산 10%에서만 69.6%로 꺼졌고(1~5% 86.4%, 20~30% 86~89%), 그 구간이
바로 이 프로젝트의 기본 운영점이다.

**가중치는 지수로 들어간다.** `(1 - s)^w`는 "이 신호를 w번 독립적으로
관측했다"로 읽히므로 `w=0`이 "관측하지 않음"이 되어 ablation의 신호 끄기와
의미가 일치한다. 점수 스케일 가중(`1 - ∏(1 - w·s)`)은 `w·s > 1`에서
`(1 - w·s)`가 음수가 되어 곱의 부호를 뒤집으므로 쓰지 않는다.

합을 쓰지 않는 이유는 그대로다 — 결과가 0~1을 벗어나면 triage의
정렬·임계 비교가 깨진다. noisy-or는 산식 자체가 [0, 1]을 보장한다.

**가중치는 튜닝하지 않는다**(스펙 §6.3). 같은 데이터에서 맞춘 값은
새 데이터에서 재현되지 않는다. 튜닝이 필요해지면 분리된 검증 세트를
만드는 것이 순서다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from cuesift.segment import SegmentRisk, Signal

# 등록된 신호 10종에 균등 가중. 무튜닝 기본값이다.
DEFAULT_WEIGHTS: dict[str, float] = {
    "struct.untranslated": 1.0,
    "struct.empty": 1.0,
    "struct.degeneration": 1.0,
    "struct.number_missing": 1.0,
    "struct.tag_lost": 1.0,
    "spec.violation": 1.0,
    "glossary.miss": 1.0,
    "length.ratio": 1.0,
    "spec.overlap": 1.0,
    # Tier 1 (FR-4.1). **가중치는 튜닝하지 않는다**(스펙 §6.3) - 같은
    # 데이터에서 맞춘 값은 새 데이터에서 재현되지 않는다.
    "llm.self_consistency": 1.0,
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
        if not math.isfinite(weight) or weight < 0:
            # 음수는 "위험할수록 안전"을 뜻하게 되어 정렬이 뒤집힌다.
            #
            # nan·inf도 함께 막는다. `nan < 0`은 IEEE 754상 False라
            # 음수 검사를 그냥 통과하고, 이후 `max(0.0, nan)`이 NaN을
            # 전파하지 않고 0.0을 반환해 **최대 위험이 최소 위험으로 뒤집힌다.**
            # YAML이 `.nan`·`.inf`를 파싱하므로 설정 파일 오타로 도달한다.
            raise ValueError(f"가중치가 유효하지 않다: {name}={weight}")

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

    # 산식이 총합을 쓰지 않으므로 이 값은 **검증 전용**이다.
    total_weight = sum(table.get(s.name, _FALLBACK_WEIGHT) for s in signals)

    # 개별 값이 전부 유한해도 **합계**는 넘칠 수 있다(`1e308` 두 개).
    #
    # **noisy-or에서는 이 경로로 점수가 뒤집히지 않는다** — 곱셈이라 `inf`가
    # 산식에 들어오지 않는다. 가중 평균 시절 D-22가 막던 실패 모드
    # (`inf / inf = nan` → `max(0.0, nan) = 0.0` → 최대 위험이 최소 위험으로)는
    # 사라졌다. 그럼에도 막는 것은 `inf` 총합이 **설정 오타의 신호**이기
    # 때문이다. YAML이 큰 수를 파싱하므로 도달 가능하고, 조용히 삼키면
    # 사용자가 오타를 모른다.
    if not math.isfinite(total_weight):
        raise ValueError(f"가중치 총합이 유한하지 않다: {total_weight}")

    if total_weight <= 0:
        # 신호가 없으면 위험도 0은 옳다 — 판단할 것이 없다.
        # 그러나 신호가 있는데 총합이 0이면 설정이 모든 신호를 죽인 것이다.
        # 모든 `w=0`은 `(1-s)^0 = 1`이라 곱이 1, 점수가 0.0이 된다.
        # 조용히 0.0을 내면 **전체 세그먼트가 안전 판정된다.**
        if signals:
            raise ValueError(
                f"가중치 총합이 0이다. 설정이 이 세그먼트의 신호를 전부 무효화했다: "
                f"{sorted(s.name for s in signals)}"
            )
        score = 0.0
    else:
        # 1 - ∏(1 - sᵢ)^wᵢ
        #
        # **범위 정합성이 `Signal.score ∈ [0, 1]`에 의존한다.** 밑 `(1 - sᵢ)`가
        # [0, 1]이고 지수 `wᵢ ≥ 0`이면 거듭제곱도 [0, 1]이고 그 곱도 [0, 1]이다.
        # 점수가 1을 넘으면 밑이 음수가 되어 짝수 지수에서 부호가 뒤집히고,
        # 점수 범위 검증이 `Signal`에서 사라지면 여기가 조용히 깨진다 —
        # 가중 평균 시절에는 아래 clamp가 마지막 방어선이었지만 이제는
        # 산식 자신이 보장하고, 그 보장이 상류 모델을 전제로 한다.
        product = 1.0
        for s in signals:
            product *= (1.0 - s.score) ** table.get(s.name, _FALLBACK_WEIGHT)
        score = 1.0 - product

    return SegmentRisk(
        segment_id=segment_id,
        signals=list(signals),
        risk_score=min(1.0, max(0.0, score)),
        hard_fail=False,
        reasons=reasons,
    )
