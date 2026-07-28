"""외부 지식이 필요한 Tier 0 신호 (요구사항정의서 FR-3.6~FR-3.8).

셋 다 hard fail이 아니다. 규격 위반과 용어 위반은 오류가 맞지만 치명은
아니고, 길이비는 정의상 통계적 의심에 불과하다. hard fail을 남발하면
FR-6.2의 예산 우회가 사실상 전량 검수가 되어 트리아지가 무의미해진다.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from cuesift.segment import Segment, Signal
from cuesift.signals.base import SignalContext, register
from cuesift.spec import check_text, text_width

# 위반 건수를 0.5~1.0 점수로 옮긴다. 1건=0.5, 2건=0.75, 3건 이상=1.0.
#
# **하한이 0.5인 이유**: 위반 1건은 이미 "문제가 있다"는 판정이다.
# 0.33 같은 낮은 값을 주면 융합에서 "거의 안전"으로 읽혀 순위가 밀린다.
#
# **두 신호가 같은 식을 쓰는 이유**: 서로 다른 스케일을 쓰면 균등 가중
# 평균에서 암묵적 가중치가 생긴다. 계획은 가중치를 튜닝하지 않기로 했으므로
# 점수 스케일을 통해 몰래 가중이 들어가면 안 된다.
_VIOLATION_FLOOR = 0.5
_VIOLATION_STEP = 0.25

# 길이비 이상치 판정에 필요한 최소 표본. 이보다 적으면 분포를 말할 수 없다.
_RATIO_MIN_SAMPLES = 8

# 로버스트 z-점수가 이 값을 넘으면 이상치로 본다.
_RATIO_Z_THRESHOLD = 3.5

# MAD를 표준편차 척도로 환산하는 상수 (정규분포 가정).
_MAD_SCALE = 0.6745

# 평균절대편차를 표준편차 척도로 환산하는 상수. MAD가 0에 가까울 때 쓴다.
_MEAN_AD_SCALE = 1.2533


def _violation_score(count: int) -> float:
    """위반 건수를 0.5~1.0 범위의 점수로 옮긴다."""
    return min(1.0, _VIOLATION_FLOOR + _VIOLATION_STEP * (count - 1))


class SpecViolationSignal:
    """FR-3.8 — §5.5 규격 검사 결과를 신호로 바꾼다."""

    name = "spec.violation"
    tier = 0

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        # 검사 대상은 화면에 나가는 번역문이다. 원문을 재면 번역 품질과
        # 무관한 위반이 잡힌다.
        if not seg.target_text:
            return None

        violations = check_text(seg.target_text, seg.duration_ms, ctx.profile)
        if not violations:
            return None

        return Signal(
            name=self.name,
            tier=0,
            score=_violation_score(len(violations)),
            hard_fail=False,
            detail={
                "kinds": sorted(v.kind for v in violations),
                "count": len(violations),
            },
        )


class GlossaryMiss:
    """FR-3.7 — 원문에 용어집 키가 있으나 번역문에 대응어가 없다."""

    name = "glossary.miss"
    tier = 0

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        # 용어집이 없거나 비었으면 판정하지 않는다. 0점 신호를 내면
        # "검사했고 통과"로 읽혀 용어집 누락이 숨는다.
        if ctx.glossary is None or ctx.glossary.is_empty or not seg.target_text:
            return None

        hits = ctx.glossary.violations(seg.source_text, seg.target_text)
        if not hits:
            return None

        return Signal(
            name=self.name,
            tier=0,
            score=_violation_score(len(hits)),
            hard_fail=False,
            detail={"terms": [e.source for e in hits]},
        )


class LengthRatio:
    """FR-3.6 — 원문 대비 번역 길이비가 언어쌍 분포에서 이상치다.

    **중앙값과 MAD를 쓴다.** 평균·표준편차를 쓰지 않는 이유는, 트랙에
    미번역·반복 붕괴 같은 극단값이 이미 섞여 있기 때문이다. 평균이 그쪽으로
    끌려가면 정작 이상치가 정상 범위로 들어오고, 정상군이 이상치로 뒤집힌다.
    """

    name = "length.ratio"
    tier = 0

    def collect_batch(self, segments: Sequence[Segment], ctx: SignalContext) -> dict[str, Signal]:
        mode = ctx.profile.char_counting
        ratios: dict[str, float] = {}

        for seg in segments:
            # 빈 번역은 FR-3.2가 hard fail로 잡는다. 분포에 0을 넣으면
            # 중앙값이 끌려가 정상 세그먼트가 이상치로 뒤집힌다.
            if not seg.target_text or not seg.target_text.strip():
                continue
            source_width = text_width(seg.source_text, mode)
            if source_width <= 0:
                continue
            ratios[seg.id] = text_width(seg.target_text, mode) / source_width

        # 표본이 적으면 분포를 말할 수 없다. 근거 없는 신호가 위험도에
        # 섞이면 리포트의 설명(FR-6.4)이 거짓말이 된다.
        if len(ratios) < _RATIO_MIN_SAMPLES:
            return {}

        values = list(ratios.values())
        median = statistics.median(values)
        deviations = [abs(v - median) for v in values]

        # **두 척도 중 큰 쪽을 쓴다.**
        #
        # MAD만 쓰면 정상군이 조밀하게 뭉칠 때 척도가 0에 가까워져
        # 중앙값 대비 0.4% 편차도 z=4.7이 된다 — 정상 세그먼트가 무더기로
        # 이상치가 되어 신호가 변별력을 잃는다. 번역 스타일이 일관된
        # 트랙에서 실제로 일어나는 상황이다.
        #
        # 평균절대편차는 로버스트하지 않아 이상치가 많으면 척도가 부풀지만,
        # 이 신호는 hard fail이 아니므로 미탐이 오탐보다 낫다.
        # MAD가 정확히 0인 경우(합성 벤치마크의 균일한 정상군)도 이 식이
        # 자동으로 흡수한다 — 그쪽이 항상 크거나 같기 때문이다.
        scale = max(
            statistics.median(deviations) / _MAD_SCALE,
            statistics.fmean(deviations) * _MEAN_AD_SCALE,
        )

        # 값이 전부 동일하면 두 척도가 모두 0이다. 이때는 이상치가
        # 정의되지 않는다 — 판정하지 않는 것이 맞다.
        if scale == 0:
            return {}

        result: dict[str, Signal] = {}
        for seg_id, ratio in ratios.items():
            z = abs(ratio - median) / scale
            if z <= _RATIO_Z_THRESHOLD:
                continue
            result[seg_id] = Signal(
                name=self.name,
                tier=0,
                # z가 임계의 2배면 1.0에 도달한다.
                score=min(1.0, z / (_RATIO_Z_THRESHOLD * 2)),
                hard_fail=False,
                detail={
                    "ratio": round(ratio, 3),
                    "median": round(median, 3),
                    "z": round(z, 2),
                },
            )
        return result


for _collector in (SpecViolationSignal(), GlossaryMiss(), LengthRatio()):
    register(_collector)
