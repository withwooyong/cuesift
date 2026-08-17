"""④ measure — Recall@Budget 측정 (설계 스펙 §6).

**제품 모듈을 호출할 뿐 자체 판정 로직을 갖지 않는다.** 벤치가 자기 신호
계산을 가지면 "측정한 것"과 "출시하는 것"이 갈라진다.

**배수는 요청 예산이 아니라 `review_ratio()`가 낸 실제 검수 비율로 나눈다**
(§6.2). hard fail이 예산을 우회하므로 둘은 다르고, 요청 예산으로 나누면
README 최상단 숫자가 부풀려진다.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from bench.inject import Label
from cuesift.risk import fuse
from cuesift.segment import Segment, SegmentRisk
from cuesift.signals import SignalContext, collect_all, registry
from cuesift.triage import review_ratio, select_by_budget

# 스펙 §6.1 — 무작위 베이스라인은 기댓값이 b지만 실측한다.
BASELINE_SEEDS = 100

# 스펙 §9.1은 "hard-fail 오탐 ≈ 0"을 목표로 적었고 스펙 §6.4가 그것을 "= 0"이라는
# 검사로 옮겼다. 그 번역에서 "≈"가 "="이 됐고, 실데이터는 약 1%를 낸다 —
# 만/억과 billion의 표기 체계 차이("500억 달러" → "50 billion"), 영어 자막이
# 숫자를 단어로 푸는 관행("30억개" → "three billion"), 일부는 TED2020 정렬
# 자체의 부정확 때문이며 검출기 버그가 아니다.
# **= 0으로 두면 이 검사는 실데이터에서 항상 실패해 아무 결과도 못 낸다.**
HARD_FAIL_FALSE_POSITIVE_LIMIT = 0.02


@dataclass(frozen=True, slots=True)
class BudgetResult:
    """예산 하나에서의 결과. 세 열(요청·실제·Recall)을 **항상 함께** 낸다."""

    budget: float
    review_ratio: float
    recall: float
    lift: float
    oracle: float
    by_kind: dict[str, float] = field(default_factory=dict)


def _risks(
    segments: Sequence[Segment], ctx: SignalContext, enabled: Iterable[str] | None
) -> list[SegmentRisk]:
    signals = collect_all(segments, ctx, enabled=enabled)
    return [fuse(seg.id, signals[seg.id]) for seg in segments]


def _recall(selected_ids: set[str], error_ids: set[str]) -> float:
    """검수 큐가 포함한 오류의 비율.

    **`measure`와 `random_baseline`이 이 함수를 공유해야 불변식 2가 게이트가 된다.**
    각자 계산하면 무작위 베이스라인이 자기 자신만 검증하게 되고, 집계 버그가
    양쪽에 동시에 나타나지 않아 밴드를 벗어나지 못한다(실측 확인 — 분모를
    `len(errors)` 대신 `n`으로 바꾼 버그를 넣었을 때, 각자 계산이면
    baseline mean이 그대로 0.1083이라 불변식 2가 통과해 버그를 놓쳤고,
    공유 `_recall`이면 0.0108로 무너져 불변식 2가 실제로 실패했다).
    """
    if not error_ids:
        return 0.0
    return len(selected_ids & error_ids) / len(error_ids)


def random_baseline(
    segments: Sequence[Segment],
    error_ids: set[str],
    ratio: float,
    seed_count: int = BASELINE_SEEDS,
) -> tuple[float, float]:
    """무작위로 `ratio` 비율을 뽑았을 때의 **오류 포착률**. `(평균, 표준편차)`.

    기댓값은 `ratio`지만 **실측한다**(스펙 §6.1) — 계산과 실측이 어긋나면
    집계 로직이 틀린 것이고, 그 경우 배수의 분모가 통째로 의심스러워진다.

    브리프 원본(`len(chosen) / n`)은 `sample()`이 중복 없이 뽑으므로
    `len(chosen)`이 **항상** `take`라 표준편차가 늘 0이었다 — 즉 오류
    집합을 전혀 보지 않았다. 여기서는 `error_ids`를 받아 `_recall`로
    실제 포착률을 잰다.
    """
    if not segments or not error_ids:
        return 0.0, 0.0
    n = len(segments)
    take = round(n * ratio)
    if take <= 0:
        return 0.0, 0.0
    hits = [
        _recall({segments[i].id for i in random.Random(seed).sample(range(n), take)}, error_ids)
        for seed in range(seed_count)
    ]
    return statistics.fmean(hits), statistics.pstdev(hits)


def measure(
    segments: Sequence[Segment],
    labels: Sequence[Label],
    ctx: SignalContext,
    budgets: Sequence[float],
    *,
    enabled: Iterable[str] | None = None,
) -> list[BudgetResult]:
    """예산 스윕. 같은 위험도 목록에 여러 예산을 적용한다."""
    risks = _risks(segments, ctx, enabled)
    error_ids = {lb.segment_id for lb in labels}
    kinds = {lb.segment_id: lb.kind for lb in labels}
    total_errors = len(error_ids)

    results: list[BudgetResult] = []
    for budget in budgets:
        selected = select_by_budget(risks, budget)
        actual = review_ratio(selected)
        caught_ids = {r.segment_id for r in selected if r.selected}

        # `_recall`을 쓴다 — `random_baseline`과 같은 분모 계산을 공유해야
        # 집계 버그가 불변식 2에서 드러난다(위 `_recall` 독스트링 참고).
        recall = _recall(caught_ids, error_ids)
        error_rate = total_errors / len(segments) if segments else 0.0
        oracle = min(1.0, actual / error_rate) if error_rate else 0.0

        by_kind: dict[str, float] = {}
        for kind in sorted(set(kinds.values())):
            of_kind = {sid for sid, k in kinds.items() if k == kind}
            by_kind[kind] = _recall(caught_ids, of_kind)

        results.append(
            BudgetResult(
                budget=budget,
                review_ratio=actual,
                recall=recall,
                # 실제 비율로 나눈다. 요청 예산으로 나누면 숫자가 부풀려진다.
                lift=recall / actual if actual else 0.0,
                oracle=oracle,
                by_kind=by_kind,
            )
        )
    return results


def ablation(
    segments: Sequence[Segment],
    labels: Sequence[Label],
    ctx: SignalContext,
    budget: float,
) -> dict[str, float]:
    """신호를 하나씩 빼고 Recall 하락폭을 잰다.

    `spec.overlap`도 포함된다 — 재리뷰가 이 신호의 캐스케이드(단일 타임코드
    오타가 트랙 절반 이상을 flag)와 가중평균 희석(soft 신호를 최대 0.25 끌어내림)을
    실측했으므로, **A/B 대상 목록에서 빠지면 안 된다.**

    **ablation은 구조상 tier 0만 잰다.** 내부에서 부르는 `measure` -> `collect_all`이
    tier 0만 실행하기 때문이다(설계 §4.1 D6) - `collect_all`에 tier 1 이름을
    섞으면 `ValueError`가 난다. tier 1 기여도는 여기서 재지 않는다. **말없이
    빠지는 것이 아니다** - `enabled` 목록을 레지스트리 전체가 아니라 tier 0로
    좁히지 않으면 tier 1 수집기가 등록되는 순간(Task 5, `llm.self_consistency`)
    `measure`가 그 이름째로 `ValueError`를 던져 이 함수 전체가 죽는다. tier 1
    기여도는 2라운드 경로(`tier1.triage_with_tier1`)로만 측정한다.
    """
    names = sorted(n for n, c in registry().items() if c.tier == 0)
    full = measure(segments, labels, ctx, [budget])[0].recall
    drops: dict[str, float] = {}
    for name in names:
        without = [n for n in names if n != name]
        drops[name] = full - measure(segments, labels, ctx, [budget], enabled=without)[0].recall
    return drops


def label_counts(labels: Sequence[Label]) -> dict[str, int]:
    """유형별 라벨 건수. **리포트에 그대로 실린다.**

    "용어 위반 Recall 100%"가 실은 "1건도 주입 못 했음"일 수 있다 —
    링크 체커의 `0 broken`이 통과로 읽혔던 것과 같은 함정이다.
    `inject`의 0건 가드는 0건만 잡으므로 부분 미달(quota보다 적지만 0은
    아닌 경우)은 이 숫자로만 드러난다.
    """
    counts: dict[str, int] = {}
    for lb in labels:
        counts[lb.kind] = counts.get(lb.kind, 0) + 1
    return counts


def check_invariants(
    results: Sequence[BudgetResult],
    labels: Sequence[Label],
    segments: Sequence[Segment],
    risks: Sequence[SegmentRisk],
) -> float:
    """스펙 §6.4의 불변식 4개. **위반이면 결과를 내지 않는다.**

    측정 코드의 진짜 위험은 틀린 숫자가 그럴듯해 보인다는 것이다.

    반환값은 **주입하지 않은 세그먼트의 hard fail 오탐률**이다(불변식 4에서
    계산한 값 그대로) — 스펙 §9.1의 "hard-fail 오탐 ≈ 0" 목표를 리포트가
    실측으로 실을 수 있게 한다.
    """
    for r in results:
        if r.recall > r.oracle + 1e-9:
            raise ValueError(
                f"불변식 1 위반 — 예산 {r.budget}에서 Recall({r.recall:.4f})이 "
                f"오라클 상한({r.oracle:.4f})을 넘었다. 라벨 누수를 의심할 것."
            )

    for prev, curr in zip(results, results[1:], strict=False):
        if curr.recall < prev.recall - 1e-9:
            raise ValueError(
                f"불변식 3 위반 — 단조성이 깨졌다. 예산 {prev.budget}에서 {prev.recall:.4f}, "
                f"{curr.budget}에서 {curr.recall:.4f}. 정렬·절단 로직을 볼 것."
            )

    error_ids = {lb.segment_id for lb in labels}
    # 불변식 4 — 정정 2(사용자 결정, 2026-07-29): "= 0"이 아니라 "≤ 2%"다.
    # 실데이터가 만/억·billion 표기 차이 등으로 절대 0이 되지 않기 때문에,
    # 분모는 주입하지 않은 세그먼트로 한정한다(주입된 세그먼트의 hard fail은
    # 오히려 기대되는 검출이다).
    non_injected = [r for r in risks if r.segment_id not in error_ids]
    false_hard = [r.segment_id for r in non_injected if r.hard_fail]
    fp_rate = len(false_hard) / len(non_injected) if non_injected else 0.0
    if fp_rate > HARD_FAIL_FALSE_POSITIVE_LIMIT:
        raise ValueError(
            f"불변식 4 위반 — 주입하지 않은 세그먼트의 hard fail 오탐률"
            f"({fp_rate:.4f})이 한도({HARD_FAIL_FALSE_POSITIVE_LIMIT:.2%})를 넘었다. "
            f"{len(false_hard)}/{len(non_injected)}건. 정의상 오탐이며 실제 검수 비율을 "
            f"부풀려 배수를 파괴한다. 예: {false_hard[:5]}"
        )

    for r in results:
        mean, stdev = random_baseline(segments, error_ids, r.review_ratio)
        # 3σ 밴드. 표준편차가 0인 경우(표본이 작아 항상 같은 수를 뽑음)를 위해 하한을 둔다.
        band = max(3 * stdev, 0.02)
        if abs(mean - r.review_ratio) > band:
            raise ValueError(
                f"불변식 2 위반 — 무작위 베이스라인({mean:.4f})이 실제 비율"
                f"({r.review_ratio:.4f})과 다르다. 선별·집계 로직을 볼 것."
            )

    return fp_rate
