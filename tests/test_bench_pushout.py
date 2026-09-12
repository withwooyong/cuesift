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

import pytest

from bench.pushout import (
    analyze_movement,
    breakdown_by_kind,
    rank_shift,
    render_pushout,
    summarize,
)

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


def test_순증은_큐_안_라벨_수의_실제_변화와_일치한다():
    """**이것이 분해의 검산이다.** 유입 − 유실이 실제 변화와 어긋나면 분해가
    틀린 것이고, 그러면 이월 22번의 결론 전체가 틀린다.

    B와 C가 둘 다 negation인 자리를 골랐다 — 큐의 negation 수는 1에서 1로
    **변하지 않는데** 유입 1건과 유실 1건이 동시에 일어난다. 순증만 보는
    리포트에는 아무 일도 없었던 것으로 보이는 상황이고, **결함 ②가 숨는
    자리가 정확히 여기다.**
    """
    scores0 = {"A": 0.9, "B": 0.8, "C": 0.3, "D": 0.2}
    tier0 = select_by_budget(_risks(scores0), 0.5)
    tier01 = select_by_budget(_risks({**scores0, "C": 0.85}), 0.5)
    label_kinds = {"B": "negation", "C": "negation"}

    moves = analyze_movement(
        tier0, tier01, candidate_ids={"C"}, priority_ids={"C"}, label_kinds=label_kinds
    )
    negation = breakdown_by_kind(moves)["negation"]

    def _count(risks):
        return sum(1 for r in risks if r.selected and label_kinds.get(r.segment_id) == "negation")

    assert negation.gained == 1
    assert negation.lost == 1
    assert negation.net == _count(tier01) - _count(tier0)
    # 순증 0 — 리포트에는 아무 일도 없었던 것으로 보인다.
    assert negation.net == 0


def test_후보_밖의_순위_이동만_따로_모은다():
    """**비대칭의 정의가 이것이다** — 후보 안은 점수가 오를 기회를 얻고 후보
    밖은 못 얻는다. 그래서 후보 밖의 Δrank 가 양(밀려남)으로 쏠린다.

    A는 1위를 지키고(Δ0) B는 밀리고(Δ+1) D는 제자리다(Δ0). 후보 밖 셋 중
    밀려난 것은 하나이고 올라간 것은 없다 — **후보 밖에서는 위로 갈 방법이
    구조적으로 없다.**
    """
    tier0 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.3, "D": 0.2}), 0.5)
    tier01 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.85, "D": 0.2}), 0.5)

    moves = analyze_movement(tier0, tier01, candidate_ids={"C"}, priority_ids={"C"}, label_kinds={})
    outside = rank_shift(moves, inside=False)
    inside = rank_shift(moves, inside=True)

    assert outside.n == 3
    assert outside.pushed_down == 1
    assert outside.pulled_up == 0
    assert inside.n == 1
    assert inside.pushed_down == 0
    assert inside.pulled_up == 1


def test_순위_이동의_분포_통계를_낸다():
    """리포트에 싣는 값이므로 게이트가 필요하다.

    후보 밖 셋의 Δrank 는 A=0 · B=+1 · D=0 이다. **중앙값이 0인데 최악이
    +1이라는 것**이 밀어냄의 성격을 말한다 — 전체가 조금씩 밀리는 것이
    아니라 컷라인 근처 몇 건만 크게 밀린다.
    """
    tier0 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.3, "D": 0.2}), 0.5)
    tier01 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.85, "D": 0.2}), 0.5)

    outside = rank_shift(
        analyze_movement(tier0, tier01, candidate_ids={"C"}, priority_ids={"C"}, label_kinds={}),
        inside=False,
    )

    assert outside.median == 0.0
    assert outside.worst == 1
    assert round(outside.mean, 4) == round(1 / 3, 4)


def test_부류가_둘이면_각각_따로_세고_라벨_없음은_빠진다():
    """**대칭 픽스처는 맞바꿈 변이를 못 잡는다.** 검산 테스트는 negation 의
    `gained` 와 `lost` 가 둘 다 1이라, 둘을 맞바꾸는 변이가 생존했다(실측).

    여기서는 B가 negation(밀려남) · C가 untranslated(올라옴)라 두 값이
    비대칭이다. A와 D는 라벨이 없으므로 이 표에 나타나지 않아야 한다 —
    Recall 의 분자가 아니기 때문이다.
    """
    tier0 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.3, "D": 0.2}), 0.5)
    tier01 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.85, "D": 0.2}), 0.5)

    bd = breakdown_by_kind(
        analyze_movement(
            tier0,
            tier01,
            candidate_ids={"C"},
            priority_ids={"C"},
            label_kinds={"B": "negation", "C": "untranslated"},
        )
    )

    assert set(bd) == {"negation", "untranslated"}
    assert (bd["negation"].gained, bd["negation"].lost, bd["negation"].net) == (0, 1, -1)
    assert (bd["untranslated"].gained, bd["untranslated"].lost, bd["untranslated"].net) == (1, 0, 1)


def _two_conditions():
    """재설계 전과 후를 **서로 다르게** 만든다.

    **같은 Tier 0+1 스냅샷을 두 조건에 쓰면 안 된다** - 그러면 표의 두 조건
    행이 동일해져, `after` 자리에 `before` 를 넣는 변이가 생존한다(실측).
    두 조건이 실제로 다른 이유를 그대로 합성한다.

    | 조건 | 후보 | 무슨 일이 일어나나 |
    | --- | --- | --- |
    | 전 | B (위험도 상위) | 이미 큐에 있던 B의 점수만 올라 순위가 그대로다 |
    | 후 | C (극성 표지) | 컷라인 아래 C가 올라와 B를 밀어낸다 |

    이것이 이월 21번의 재설계가 바꾼 것이다 - 후보를 위험도 순위에서
    극성 표지 보유로 옮겼다.
    """
    tier0 = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.3, "D": 0.2}), 0.5)
    pre = select_by_budget(_risks({"A": 0.9, "B": 0.85, "C": 0.3, "D": 0.2}), 0.5)
    post = select_by_budget(_risks({"A": 0.9, "B": 0.8, "C": 0.85, "D": 0.2}), 0.5)
    kinds = {"B": "negation", "C": "untranslated"}
    before = analyze_movement(
        tier0, pre, candidate_ids={"B"}, priority_ids=set(), label_kinds=kinds
    )
    after = analyze_movement(
        tier0, post, candidate_ids={"C"}, priority_ids={"C"}, label_kinds=kinds
    )
    return before, after


def test_전후를_뒤바꿔_넘기면_거부한다():
    """**뒤바뀌면 결론이 정반대가 되는데 숫자는 그럴듯하다.**

    전후 라벨은 호출부가 붙이는 것이라 실수하면 렌더러는 모른다. 그래서
    라벨을 믿지 않고 데이터로 검증한다 — 재설계 전 조건은 우선 집합이 비어
    있고(`priority_ids=frozenset()`을 주입한 실행), 후 조건은 비어 있지 않다.
    이 성질은 두 실행을 구조적으로 가르므로 뒤바뀜이 반드시 걸린다.
    """
    before, after = _two_conditions()

    with pytest.raises(ValueError, match="재설계 전"):
        render_pushout(budget=0.1, before=after, after=before)


def test_분해표가_두_조건의_숫자를_싣는다():
    """리포트에 실제로 들어가는 값이다. 표에 없으면 읽는 사람이 판단할 수 없다."""
    before, after = _two_conditions()

    out = render_pushout(budget=0.1, before=before, after=after)

    assert "예산 10%" in out
    # **행을 통째로 단언한다.** 문자열이 어딘가에 있는지만 보면 유실 열을
    # 유입으로 바꾸는 변이가 생존한다(실측).
    assert "| 재설계 전 | negation | 0 | 0 | +0 |" in out
    assert "| 재설계 후 | negation | 0 | 1 | -1 |" in out
    assert "| 재설계 후 | untranslated | 1 | 0 | +1 |" in out
    # 후보 밖 셋 중 하나가 밀려났고, 위로 간 것은 없다.
    assert "| 재설계 후 | 후보 밖 | 3 | 1 | 0 |" in out


def test_후_조건에_우선_집합이_없으면_거부한다():
    """`after` 쪽 검증이 없으면 **두 실행이 전부 재설계 전이어도 통과한다.**

    `priority_ids` 주입을 실수로 양쪽에 걸면 그런 상태가 되는데, 표는
    "전후가 같다"는 그럴듯한 숫자를 낸다 — 이월 22번이 가장 경계해야 할
    거짓 결론이다.
    """
    before, _ = _two_conditions()

    with pytest.raises(ValueError, match="재설계 후"):
        render_pushout(budget=0.1, before=before, after=before)
