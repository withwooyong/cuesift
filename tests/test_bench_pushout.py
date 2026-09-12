"""`bench/pushout.py`의 밀어냄 분해 테스트 (이월 22번 · 결함 ②).

**이 파일이 검사하는 것은 결론의 재료다.** 이월 22번은 "negation Recall이
올랐는데 그것이 밀어냄 완화 때문인가"를 묻고, 답은 ΔRecall을 유입과 유실로
나눈 값이다. 그 분해가 틀리면 실측 전체가 틀린 결론을 낸다 — 그런데 실측
데이터로는 옳은지 확인할 방법이 없다(정답을 모르므로). **밀려날 것을 미리
아는 합성 데이터**로만 이 계산을 검사할 수 있다.

`data/`가 `.gitignore`라 CI에서 skip되는 `test_bench_run.py`의 벤치 테스트와
달리, 여기 있는 것은 전부 순수 함수라 CI에서도 돈다.
"""

from __future__ import annotations

from bench.pushout import analyze_movement, summarize

from cuesift.segment import SegmentRisk
from cuesift.triage import select_by_budget


def _risks(
    scores: dict[str, float], *, hard_fail: frozenset[str] = frozenset()
) -> list[SegmentRisk]:
    """점수만 다른 `SegmentRisk` 목록. 신호는 이 계산에 쓰이지 않는다."""
    return [
        SegmentRisk(segment_id=sid, signals=[], risk_score=score, hard_fail=sid in hard_fail)
        for sid, score in scores.items()
    ]


def test_점수가_그대로인데_큐에서_빠진_세그먼트를_유실로_잡는다():
    """**이것이 결함 ②의 정의다** — Tier 1은 후보에만 점수를 더하는데,
    예산이 정원을 고정하므로 후보가 올라간 자리만큼 후보 **밖**이 빠진다.

    B는 Tier 0와 Tier 0+1에서 점수가 0.8로 같다. 그런데도 큐에서 빠지는 것은
    후보였던 C가 0.3에서 0.85로 올라 B를 넘었기 때문이다. **점수가 변하지
    않은 세그먼트가 큐에서 빠지는 것**, 그것만이 밀어냄이다.
    """
    tier0 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.3, "D": 0.2}), 0.5)
    tier01 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.85, "D": 0.2}), 0.5)

    moves = analyze_movement(
        tier0,
        tier01,
        candidate_ids={"C"},
        priority_ids={"C"},
        label_kinds={"B": "negation"},
    )
    summary = summarize(moves)

    assert summary.lost == ("B",)
    assert summary.gained == ("C",)


def test_밀려난_쪽은_delta_rank가_양수_올라간_쪽은_음수다():
    """**부호가 뒤집히면 결론이 정반대가 된다.** 밀어냄의 크기를 Δrank 분포로
    보고할 것이므로, "밀려남 = 양수"를 여기서 못 박는다.

    B는 2위에서 3위로 밀리고(+1) C는 3위에서 2위로 오른다(-1). 자리를 맞바꾼
    것이라 두 값의 합은 0이다 — 순위는 보존량이므로 **누가 오르면 누가 내린다**,
    그것이 결함 ②가 구조적인 이유다.
    """
    tier0 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.3, "D": 0.2}), 0.5)
    tier01 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.85, "D": 0.2}), 0.5)

    moves = {
        m.segment_id: m
        for m in analyze_movement(
            tier0, tier01, candidate_ids={"C"}, priority_ids={"C"}, label_kinds={}
        )
    }

    assert moves["B"].delta_rank == 1
    assert moves["C"].delta_rank == -1
    assert sum(m.delta_rank for m in moves.values()) == 0

    # **점수는 두 스냅샷에서 각각 읽어야 한다.** B로만 단언하면 B는 점수가
    # 변하지 않아 `score_tier0`에 Tier 0+1 값을 넣는 변이가 생존한다(실측).
    # 올라간 쪽 C로 단언해야 그 변이가 잡힌다 - "얼마나 올랐나"는 실측
    # 원자료의 핵심 값이라 틀리면 결론이 조용히 거짓이 된다.
    assert (moves["B"].score_tier0, moves["B"].score_tier01) == (0.8, 0.8)
    assert (moves["C"].score_tier0, moves["C"].score_tier01) == (0.3, 0.85)


def test_hard_fail은_후보가_점수를_올려도_밀려나지_않는다():
    """hard fail은 예산을 우회한다 (FR-6.2). **섞이면 밀어냄이 부풀려진다.**

    A는 hard fail이라 정원과 무관하게 큐에 남고, 밀려나는 것은 A가 먹고 남은
    한 자리를 놓고 다투는 B다. 이 구분이 무너지면 "Tier 1이 hard fail까지
    밀어냈다"는 틀린 결론이 나온다 — 실제로는 `backtranslation`·`llm` 두
    Tier 1 신호가 전부 `hard_fail=False` 고정이라 그 경로가 없다.
    """
    scores = {"A": 1.0, "B": 0.8, "C": 0.3, "D": 0.2}
    tier0 = select_by_budget(_risks(scores, hard_fail=frozenset({"A"})), 0.5)
    tier01 = select_by_budget(_risks({**scores, "C": 0.85}, hard_fail=frozenset({"A"})), 0.5)

    summary = summarize(
        analyze_movement(tier0, tier01, candidate_ids={"C"}, priority_ids={"C"}, label_kinds={})
    )

    assert summary.lost == ("B",)
    assert "A" not in summary.lost


def test_반환은_tier0_순위_오름차순이다():
    """**조건 간 비교가 눈으로 되려면 기준 순서가 하나여야 한다.**

    재설계 전(b)과 후(c)는 Tier 0+1 순위가 서로 다르다 — 그 순서로 내보내면
    같은 세그먼트가 두 파일에서 다른 줄에 놓여 대조가 안 된다. 두 조건이
    공유하는 유일한 순서가 Tier 0 순위다.

    이 테스트는 변이 실험에서 **유일하게 생존한 변이**(정렬 키를
    `rank_tier01`로 바꾸기)를 잡으려고 추가했다 — 앞의 세 테스트가 전부
    `dict`나 `summarize`로 받아 순서를 보지 않았다.
    """
    tier0 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.3, "D": 0.2}), 0.5)
    tier01 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.85, "D": 0.2}), 0.5)

    moves = analyze_movement(tier0, tier01, candidate_ids={"C"}, priority_ids={"C"}, label_kinds={})

    # Tier 0+1 순위였다면 ["A", "C", "B", "D"]가 된다.
    assert [m.segment_id for m in moves] == ["A", "B", "C", "D"]
