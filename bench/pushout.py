"""Tier 0 -> Tier 0+1 의 큐 이동을 세그먼트 단위로 분해한다 (이월 22번 · 결함 ②).

**순증만으로는 갈리지 않는 두 항을 나누는 것이 이 모듈의 존재 이유다.**
`bench/report.py`의 `render_tier1_comparison`은 negation Recall이 1.41%에서
4.23%로 올랐다는 것까지만 싣는데, 그 상승은 두 항의 차다.

| 항 | 정의 | 무엇을 뜻하나 |
| --- | --- | --- |
| 유입 | Tier 0 컷라인 **아래** -> Tier 0+1 컷라인 **위** | Tier 1이 건져 올렸다 |
| 유실 | Tier 0 컷라인 **위** -> Tier 0+1 컷라인 **아래** | **이것이 밀어냄이다** |

밀어냄이 생기는 자리는 `cuesift/tier1.py`의 마지막 두 줄이다 — 후보에만
Tier 1 신호를 더해 `rescored`를 만든 뒤 **같은 예산으로 다시 자른다.**
noisy-or는 점수를 올리기만 하고 예산은 정원을 고정하므로, 후보가 올라간
자리만큼 후보 **밖**이 정확히 같은 수로 빠진다.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from cuesift.segment import SegmentRisk


@dataclass(frozen=True)
class Movement:
    """세그먼트 하나가 Tier 0에서 Tier 0+1로 가며 겪은 이동."""

    segment_id: str
    rank_tier0: int
    rank_tier01: int
    score_tier0: float
    score_tier01: float
    selected_tier0: bool
    selected_tier01: bool
    is_candidate: bool
    is_priority: bool
    hard_fail: bool
    label_kind: str | None

    @property
    def delta_rank(self) -> int:
        """순위 이동. **양수가 밀려남이다.**

        뺄셈의 방향이 뒤집히면 이 작업의 결론이 정반대가 된다 — 순위는
        1위가 가장 위라 "나중 순위 - 먼저 순위"가 양수일 때 아래로 간 것이다.
        순위는 보존량이므로 전체 합은 항상 0이고, 그것이 결함 ②가 구조적인
        이유다: **누가 오르면 반드시 누가 내린다.**
        """
        return self.rank_tier01 - self.rank_tier0


def analyze_movement(
    tier0: Sequence[SegmentRisk],
    tier01: Sequence[SegmentRisk],
    *,
    candidate_ids: Collection[str],
    priority_ids: Collection[str],
    label_kinds: Mapping[str, str],
) -> list[Movement]:
    """두 스냅샷을 세그먼트 단위로 맞대어 이동을 낸다.

    **순위는 입력 순서에서 읽는다.** `select_by_budget`이 "반환 순서는 위험도
    내림차순이며 동점은 세그먼트 ID로 깨뜨린다"를 계약으로 갖고 있으므로
    (`triage/policy.py`), 여기서 다시 정렬하면 그 술어를 복제하는 것이 된다 -
    `gray_zone()`을 공유 함수로 뽑은 것과 같은 이유다. 복제하면 동점 처리가
    한쪽에서만 바뀌어도 순위가 조용히 갈린다.
    """
    candidates = set(candidate_ids)
    priority = set(priority_ids)
    rank0 = {r.segment_id: i for i, r in enumerate(tier0, 1)}
    by_id0 = {r.segment_id: r for r in tier0}

    moves: list[Movement] = []
    for rank, risk in enumerate(tier01, 1):
        before = by_id0[risk.segment_id]
        moves.append(
            Movement(
                segment_id=risk.segment_id,
                rank_tier0=rank0[risk.segment_id],
                rank_tier01=rank,
                score_tier0=before.risk_score,
                score_tier01=risk.risk_score,
                selected_tier0=before.selected,
                selected_tier01=risk.selected,
                is_candidate=risk.segment_id in candidates,
                is_priority=risk.segment_id in priority,
                hard_fail=risk.hard_fail,
                label_kind=label_kinds.get(risk.segment_id),
            )
        )
    # **Tier 0 순위로 정렬해 돌려준다** - 입력 순서(Tier 0+1 순위)를 그대로
    # 두면 같은 세그먼트 집합이 조건마다 다른 순서로 나와 조건 간 비교가
    # 눈으로 안 된다. NFR-3(재현성)도 결정적 순서를 요구한다.
    return sorted(moves, key=lambda m: m.rank_tier0)


@dataclass(frozen=True)
class PushoutSummary:
    """유입과 유실."""

    gained: tuple[str, ...] = ()
    lost: tuple[str, ...] = ()


def summarize(movements: Sequence[Movement]) -> PushoutSummary:
    """유입·유실을 가른다."""
    return PushoutSummary(
        gained=tuple(m.segment_id for m in movements if m.selected_tier01 and not m.selected_tier0),
        lost=tuple(m.segment_id for m in movements if m.selected_tier0 and not m.selected_tier01),
    )
