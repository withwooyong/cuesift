"""측정과 불변식 테스트 (설계 스펙 §6).

**정정 1(P-5)**: `random_baseline`은 `(segments, error_ids, ratio)`를 받아
**오류 포착률**을 실측한다 — 브리프 원본(`len(chosen)/n`)은 `sample()`이 중복
없이 뽑으므로 `len(chosen)`이 항상 `take`라 표준편차가 늘 0이고, 불변식 2가
`random_baseline(len(segments), r.review_ratio)`로 자기 자신과 비교돼
**어떤 버그로도 실패하지 않았다**(실측: 분모를 `n`으로 바꾼 사본에서도 밴드
안에 들어 통과). `measure`와 공유하는 `_recall` 헬퍼로 계산해야 두 계산
경로에 같은 버그가 동시에 나타나 밴드를 벗어난다.

**정정 2(P-6)**: 불변식 4는 "주입 안 된 세그먼트의 hard fail = 0"이 아니라
"≤ 2%"다. 실데이터(en-ko 0.96%, ja-ko 0.93%)가 "500억 달러"→"50 billion" 같은
만/억·billion 표기 체계 차이로 절대 0이 되지 않기 때문이다(검출기 버그 아님).
`check_invariants`는 이 실측 오탐률을 **반환**한다.

**정정 3(Task 6 리뷰)**: `inject`의 "0건이면 실패" 가드는 부분 미달(quota보다
적지만 0은 아닌 경우)을 드러내지 못한다. `label_counts`가 유형별 라벨 건수를
노출해 리포트가 이를 드러낼 수 있게 한다.
"""

from __future__ import annotations

import pytest
from bench.inject import Label, inject
from bench.measure import (
    HARD_FAIL_FALSE_POSITIVE_LIMIT,
    BudgetResult,
    ablation,
    check_invariants,
    label_counts,
    measure,
    random_baseline,
)

from cuesift.glossary import Glossary, GlossaryEntry
from cuesift.risk import fuse
from cuesift.segment import Segment, SegmentRisk
from cuesift.signals import SignalContext, collect_all, register, registry
from cuesift.spec import load_builtin

PROFILE = load_builtin("ted-en")
GLOSSARY = Glossary(entries=(GlossaryEntry(source="기후", targets=("climate",)),))
CTX = SignalContext(profile=PROFILE, glossary=GLOSSARY, source_lang="ko", target_lang="en")


def _clean_track(n: int = 200) -> list[Segment]:
    """**3건에 1건꼴로 부정 표현을 넣는다.**

    negation 주입기가 제거 전용이라, 부정 표현이 없는 트랙은 자격이 0건이고
    `inject`가 "실주입 0건" 가드로 죽는다. 비율을 1/3로 둔 이유는
    `tests/test_bench_inject.py::_track`과 같다 — 자격률이 `scarcity`
    정렬 순서를 바꾸기 때문이다.
    """
    segs = []
    for i in range(n):
        start = i * 5000
        target = f"We look at climate issue {i} today"
        if i % 3 == 0:
            target = f"We do not look at climate issue {i} today"
        segs.append(
            Segment(
                id=f"s{i:03d}",
                index=i,
                start_ms=start,
                end_ms=start + 4500,
                source_text=f"기후 변화 문제 {i} 번을 봅니다",
                target_text=target,
            )
        )
    return segs


def _risks(segments, ctx=CTX):
    """`measure`가 내부에서 하는 것과 같은 계산 — 불변식 4를 재는 테스트용."""
    signals = collect_all(segments, ctx)
    return [fuse(seg.id, signals[seg.id]) for seg in segments]


def test_random_baseline_matches_the_ratio():
    """기댓값은 비율이지만 실측한다. 어긋나면 집계 로직이 틀렸다.

    **정정 1** — 오류 집합을 실제로 봐야 한다. 균등 무작위 선별에서는 오류가
    어디에 있든 선택될 확률이 동일하므로 기댓값은 여전히 `ratio`다.
    """
    segs = _clean_track(1000)
    error_ids = {s.id for s in segs[:100]}  # 10% 오류
    mean, stdev = random_baseline(segs, error_ids, ratio=0.10, seed_count=100)
    assert abs(mean - 0.10) < 0.03
    assert stdev >= 0.0


def test_random_baseline_standard_deviation_is_not_always_zero():
    """브리프 원본 결함의 직접 회귀 — `sample()`은 중복이 없어 `len(chosen)`이
    항상 `take`라, 분모를 세그먼트 총수로 잘못 잡으면 표준편차가 늘 0이 된다.
    `_recall`을 공유하는 정정 구현은 시드마다 실제로 다른 오류를 잡으므로
    표본이 작을수록(오류가 적을수록) 시드 간 변동이 뚜렷하다.
    """
    segs = _clean_track(200)
    error_ids = {s.id for s in segs[:20]}  # 10% 오류, 20건뿐이라 변동이 보인다
    _, stdev = random_baseline(segs, error_ids, ratio=0.10, seed_count=100)
    assert stdev > 0.0


def test_recall_is_monotonic_in_budget():
    """예산을 늘렸는데 Recall이 떨어지면 정렬·절단 로직 버그다."""
    segs = _clean_track()
    mutated, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
    results = measure(mutated, labels, CTX, [0.01, 0.05, 0.10, 0.20, 0.30])
    recalls = [r.recall for r in results]
    assert recalls == sorted(recalls)


def test_recall_never_exceeds_the_oracle():
    """초과하면 라벨 누수다 — 검출기가 정답을 보고 있다."""
    segs = _clean_track()
    mutated, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
    for r in measure(mutated, labels, CTX, [0.01, 0.05, 0.10, 0.20]):
        assert r.recall <= r.oracle + 1e-9


def test_lift_uses_actual_review_ratio_not_requested_budget():
    """**여기서 부풀리면 프로젝트의 핵심 주장이 무너진다**(스펙 §6.2).

    hard fail이 예산을 우회하므로 요청 예산과 실제 비율은 다르다.
    """
    segs = _clean_track()
    mutated, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
    r = measure(mutated, labels, CTX, [0.01])[0]
    assert r.review_ratio >= 0.01
    assert r.lift == pytest.approx(r.recall / r.review_ratio)


def test_invariant_violation_raises_instead_of_reporting():
    """**게이트를 만들면 반드시 실패시켜 본다.**

    불변식이 통과만 하는 것을 확인해서는 그것이 무엇을 잡는지 알 수 없다.
    """
    bogus = [
        BudgetResult(budget=0.10, review_ratio=0.10, recall=0.95, lift=9.5, oracle=0.5, by_kind={}),
    ]
    with pytest.raises(ValueError, match="오라클"):
        check_invariants(bogus, labels=[], segments=[], risks=[])


def test_invariant_catches_non_monotonic_recall():
    results = [
        BudgetResult(
            budget=0.05, review_ratio=0.05, recall=0.60, lift=12.0, oracle=1.0, by_kind={}
        ),
        BudgetResult(budget=0.10, review_ratio=0.10, recall=0.40, lift=4.0, oracle=1.0, by_kind={}),
    ]
    with pytest.raises(ValueError, match="단조"):
        check_invariants(results, labels=[], segments=[], risks=[])


def test_invariant_catches_hard_fail_on_clean_segments():
    """깨끗한 트랙에서 주입하지 않은 세그먼트 전부가 hard fail이면
    오탐률이 100%로 한도(2%)를 넘으므로 정의상 오탐이다.
    """
    segments = [
        Segment(
            id="clean", index=0, start_ms=0, end_ms=4000, source_text="가나다", target_text="abc"
        )
    ]
    risks = [SegmentRisk(segment_id="clean", signals=[], risk_score=1.0, hard_fail=True)]
    with pytest.raises(ValueError, match="hard fail"):
        check_invariants([], labels=[], segments=segments, risks=risks)


def test_invariant_tolerates_hard_fail_false_positive_rate_at_or_under_the_limit():
    """**정정 2** — 실데이터가 절대 0이 되지 않는다(만/억·billion 표기 차이).

    한도(2%) 이내면 통과하고, 실측 오탐률을 반환해야 리포트가 실을 수 있다.
    """
    segments = [
        Segment(
            id=f"clean{i}",
            index=i,
            start_ms=i * 4000,
            end_ms=i * 4000 + 3500,
            source_text="가나다",
            target_text="abc",
        )
        for i in range(100)
    ]
    # 100건 중 1건만 hard fail => 오탐률 1% <= 한도 2%.
    risks = [
        SegmentRisk(segment_id=s.id, signals=[], risk_score=0.0, hard_fail=(i == 0))
        for i, s in enumerate(segments)
    ]
    rate = check_invariants([], labels=[], segments=segments, risks=risks)
    assert rate == pytest.approx(0.01)
    assert rate <= HARD_FAIL_FALSE_POSITIVE_LIMIT


def test_by_kind_recall_covers_every_injected_type():
    """유형별 Recall이 빠지면 negation의 0이 보이지 않는다 — 그게 Tier 1 근거 숫자다."""
    segs = _clean_track()
    mutated, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
    r = measure(mutated, labels, CTX, [0.20])[0]
    assert set(r.by_kind) == {lb.kind for lb in labels}


def test_ablation_reports_a_number_for_every_signal():
    """신호별 기여도. 오타로 신호를 껐는데 '기여도 0'으로 읽히면 안 된다.

    **tier 0 한정이다.** `ablation` -> `measure` -> `collect_all`이 tier 0만
    실행하므로(설계 §4.1 D6, Task 2), tier 1 수집기가 등록돼 있어도 그
    이름은 여기 집합에 들지 않는다. `set(registry())`로 두면 Task 5가
    `llm.self_consistency`(tier 1)를 등록하는 순간 이 단언이 깨진다 -
    실제로는 `ablation`이 tier 1 이름을 `collect_all`에 넘기다 `ValueError`로
    먼저 죽으므로, 이 단언에 닿기도 전에 실패한다."""
    segs = _clean_track()
    mutated, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
    drops = ablation(mutated, labels, CTX, budget=0.10)
    assert set(drops) == {n for n, c in registry().items() if c.tier == 0}


def test_ablation_survives_a_registered_tier1_signal():
    """A1 회귀 — Task 5가 `llm.self_consistency`(tier 1)를 전역 등록하는
    순간을 흉내 낸다.

    이 태스크(Tier 1 실행 격리) 착지 전에는 `ablation`이 `sorted(registry())`로
    **레지스트리 전체**를 `collect_all(enabled=...)`에 되먹였다 - tier 1
    수집기가 하나라도 등록되면 `collect_all`이 `ValueError: collect_all은
    tier 0만 실행한다`로 죽어 `ablation` 전체가 실패했다(리뷰어 2명이
    독립적으로 확인). tier 0로 좁힌 지금은 tier 1 이름이 애초에 `names`에
    들지 않으므로 살아남고, `drops`에도 나타나지 않는다 - "말없이 빠진다"가
    아니라 "애초에 이 함수의 대상이 아니다"인 이유는 위 `ablation` 독스트링에
    적혀 있다.
    """

    class _FakeTier1:
        name = "test.fake_tier1_for_ablation"
        tier = 1

        def collect_tier1(self, seg, ctx):
            return None

    saved = dict(registry())
    register(_FakeTier1())
    try:
        segs = _clean_track()
        mutated, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
        drops = ablation(mutated, labels, CTX, budget=0.10)
        assert "test.fake_tier1_for_ablation" not in drops
    finally:
        registry().clear()
        registry().update(saved)


def test_check_invariants_passes_on_real_pipeline_output_and_returns_the_rate():
    """정상 실행에서는 통과하고, 반환값이 실제 hard fail 오탐률이어야 한다."""
    segs = _clean_track()
    mutated, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
    results = measure(mutated, labels, CTX, [0.05, 0.10, 0.20])
    risks = _risks(mutated)
    rate = check_invariants(results, labels, mutated, risks)
    assert 0.0 <= rate <= HARD_FAIL_FALSE_POSITIVE_LIMIT


def test_label_counts_reports_per_kind_totals():
    """**정정 3** — `inject`의 0건 가드는 0건만 잡는다. 부분 미달은 이 숫자로만 드러난다."""
    segs = _clean_track()
    _, labels, _ = inject(segs, GLOSSARY, PROFILE, rate=0.10, seed=3)
    counts = label_counts(labels)
    assert sum(counts.values()) == len(labels)
    assert set(counts) == {lb.kind for lb in labels}


def test_label_counts_on_empty_labels():
    assert label_counts([]) == {}


def test_label_counts_type_annotation_accepts_label_sequence():
    counts = label_counts(
        [Label(segment_id="s0", kind="number"), Label(segment_id="s1", kind="number")]
    )
    assert counts == {"number": 2}
