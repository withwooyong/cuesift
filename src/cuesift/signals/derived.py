"""외부 지식이 필요한 Tier 0 신호 (요구사항정의서 FR-3.6~FR-3.8).

셋 다 hard fail이 아니다. 규격 위반과 용어 위반은 오류가 맞지만 치명은
아니고, 길이비는 정의상 통계적 의심에 불과하다. hard fail을 남발하면
FR-6.2의 예산 우회가 사실상 전량 검수가 되어 트리아지가 무의미해진다.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from cuesift.glossary import term_offsets
from cuesift.segment import Segment, Signal, Span
from cuesift.signals.base import SignalContext, register
from cuesift.spec import check_overlaps, check_text, text_width

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

# 로버스트 z-점수가 이 값을 넘으면 통계적으로 극단이다.
_RATIO_Z_THRESHOLD = 3.5

# 그리고 중앙값 대비 이 비율 이상 실제로 달라야 한다.
#
# **통계적 극단만으로는 부족하다.** 번역 스타일이 일관된 트랙에서는
# 길이비가 조밀하게 뭉쳐 MAD가 0에 가까워지고, 중앙값 대비 0.4% 편차도
# z가 4를 넘는다 — 정상 세그먼트가 무더기로 이상치가 되어 변별력이 사라진다.
#
# 25%인 근거: 원문 20자·번역 48자인 전형적 세그먼트에서 이 게이트는
# 약 12자, 즉 두세 어절에 해당한다. 그보다 작은 차이는 번역 품질 문제와
# 평범한 어휘 선택을 구분하지 못한다.
_RATIO_MIN_RELATIVE_DEVIATION = 0.25

# 원문이 이보다 짧으면 길이비를 판정하지 않는다.
#
# 분모가 작으면 정수 문자 수가 비율을 거칠게 튀게 만든다 — '네'->'Yes'가 3.0이다.
# 자막은 짧은 감탄사·응답이 매우 흔해 이것이 우연이 아니라 계통 오차가 된다.
# structural.py의 _UNTRANSLATED_MIN_CHARS와 같은 병리에 대한 같은 대응이다.
#
# 값 4는 실측으로 골랐다: 깨끗한 400건 트랙의 오탐이 33건 -> 0건이 되고,
# 주입 40건의 재현율은 100%로 유지된다(6으로 올리면 재현율이 90%로 떨어진다).
#
# **이 수치는 현재 저장소에서 재현할 수 없다** — 오류 주입기가 아직 없어
# 일회성 스크립트로 측정했다. 벤치 하네스가 생기면 재측정해 이 주석을
# 갱신하거나, 재측정값이 다르면 상수를 다시 고를 것. 재현할 수 없는 근거는
# 근거가 아니라 기억이다.
#
# **글자 수가 아니라 `text_width` 폭이다.** CJK는 세 counting mode에서 모두 1.0이라
# ko 원문의 실제 컷오프는 어느 프로파일에서든 **4글자**다 — '그래'·'고마워'·'알겠어'처럼
# 3글자 이하는 판정 자체를 받지 않는 사각지대다. 라틴 원문은 `latin_half`에서 0.5씩
# 세이므로 같은 4.0이 8자를 뜻한다(ko->en/ja 범위 밖이지만 언어쌍이 늘면 재확인할 것).
_RATIO_MIN_SOURCE_WIDTH = 4.0

# MAD를 표준편차 척도로 환산하는 상수 (정규분포 가정).
_MAD_SCALE = 0.6745

# 평균절대편차를 표준편차 척도로 환산하는 상수. MAD가 0일 때만 쓴다.
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

        # 위반 용어가 원문에서 차지하는 구간. FR-7.3 리포트가 여기를 칠한다.
        #
        # **번역문이 아니라 원문을 가리킨다** — 이 신호는 "번역문에 대응어가
        # 없다"는 판정이라 번역문에는 칠할 것이 없다(`Span` 독스트링).
        #
        # **위치 순으로 정렬한다.** `hits`는 용어집 등재 순이고(`violations`가
        # 그 순서를 유지한다) 용어 여럿의 구간이 섞이면 순서가 뒤엉킨다.
        # `review.json`에 배열로 직렬화되므로 순서가 비결정적이면 같은 입력이
        # 다른 파일을 낸다(NFR-3 · 설계 D9).
        offsets = sorted(
            offset for entry in hits for offset in term_offsets(seg.source_text, entry.source)
        )

        return Signal(
            name=self.name,
            tier=0,
            score=_violation_score(len(hits)),
            hard_fail=False,
            spans=tuple(Span(start=s, end=e, side="source") for s, e in offsets),
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
            if source_width < _RATIO_MIN_SOURCE_WIDTH:
                continue
            ratios[seg.id] = text_width(seg.target_text, mode) / source_width

        # 표본이 적으면 분포를 말할 수 없다. 근거 없는 신호가 위험도에
        # 섞이면 리포트의 설명(FR-6.4)이 거짓말이 된다.
        if len(ratios) < _RATIO_MIN_SAMPLES:
            return {}

        values = list(ratios.values())
        median = statistics.median(values)
        deviations = [abs(v - median) for v in values]

        # MAD를 주 척도로 쓴다. 평균절대편차는 로버스트하지 않아
        # 이상치가 많으면 척도가 부풀어 정작 그 이상치를 놓친다 —
        # 주입률 10%에서 재현율이 절반으로 떨어지는 것을 실측했다.
        scale = statistics.median(deviations) / _MAD_SCALE

        if scale == 0:
            # 정상군이 완전히 균일하면 MAD가 0이 된다. 합성 벤치마크에서는
            # 이 상황이 예외가 아니라 기본이다 — 정상 세그먼트가 같은 길이로
            # 생성되고 주입된 오류만 튄다. 여기서 빈손으로 돌아가면
            # 가장 명백한 이상치를 놓친다.
            scale = statistics.fmean(deviations) * _MEAN_AD_SCALE

        # 값이 전부 동일하면 두 척도가 모두 0이다. 이상치가 정의되지 않는다.
        if scale == 0:
            return {}

        result: dict[str, Signal] = {}
        for seg_id, ratio in ratios.items():
            deviation = abs(ratio - median)
            z = deviation / scale
            # 통계적으로 극단이면서 실질적으로도 달라야 한다.
            if z <= _RATIO_Z_THRESHOLD or deviation < median * _RATIO_MIN_RELATIVE_DEVIATION:
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
                    "deviation": round(deviation, 3),
                },
            )
        return result


class OverlapSignal:
    """FR-5.1 — 세그먼트 시간이 겹친다.

    트랙 전체를 봐야 판정되므로 배치 수집기다. `check_text`는 세그먼트 하나만
    보므로 겹침을 판정할 수 없어, 이 신호가 없으면 FR-5.1의 중첩 금지가
    위험도에 도달하지 않는다.

    **hard fail이 아니다.** 겹침은 타이밍 결함이지 번역 결함이 아니고,
    hard fail을 늘리면 예산 우회가 커져 트리아지가 무의미해진다.
    """

    name = "spec.overlap"
    tier = 0

    def collect_batch(self, segments: Sequence[Segment], ctx: SignalContext) -> dict[str, Signal]:
        return {
            seg_id: Signal(
                name=self.name,
                tier=0,
                # 겹침은 건수가 아니라 유무의 문제다. 다른 신호의 하한과
                # 스케일을 맞춘다(_violation_score(1) == 0.5).
                #
                # **이 점수가 바꾸는 것은 이 신호의 값이 아니라 같은 세그먼트에
                # 붙은 다른 신호의 값이다.** risk.fuse가 가중 평균이라 신호가
                # 하나 늘면 분모가 늘고, Tier 0 신호는 전부 최소 0.5를 내므로
                # 이 고정값은 **내리는 방향으로만 작용한다** — spec.violation
                # 1.0 단독이 0.75가 된다. 동시에 이 신호는 단일 end_ms 오타로
                # 트랙의 절반 이상에 발화하는 유일한 신호라(check_overlaps가
                # 누적 최대와 비교하므로), 결백한 세그먼트를 0.0에서 0.5로
                # 올리기까지 한다. 순위가 양방향으로 오염된다.
                #
                # 지금 고치지 않는다 — 첫 측정 전에 융합 규칙을 바꾸면
                # 베이스라인이 없어 개선을 입증할 수 없다. 벤치 하네스의
                # ablation(enabled=[...])으로 A/B 판정할 항목이다.
                score=_violation_score(1),
                hard_fail=False,
                detail={"overlap_ms": violation.measured},
            )
            for seg_id, violation in check_overlaps(segments).items()
        }


for _collector in (SpecViolationSignal(), GlossaryMiss(), LengthRatio(), OverlapSignal()):
    register(_collector)
