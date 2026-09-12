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
from statistics import fmean, median

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


@dataclass(frozen=True)
class KindBreakdown:
    kind: str
    gained: int = 0
    lost: int = 0

    @property
    def net(self) -> int:
        return self.gained - self.lost


@dataclass(frozen=True)
class RankShift:
    """한 집합의 순위 이동 요약. **양수가 밀려남이다** (`Movement.delta_rank`)."""

    n: int = 0
    pushed_down: int = 0
    pulled_up: int = 0
    median: float = 0.0
    mean: float = 0.0
    worst: int = 0


def breakdown_by_kind(movements: Sequence[Movement]) -> dict[str, KindBreakdown]:
    """오류 부류별로 유입·유실을 센다.

    **라벨 없는 세그먼트는 제외한다.** 이 표가 답하는 질문은 "어떤 오류가
    큐에 들고 났나"이고, 라벨 없는 것은 애초에 Recall 의 분자가 아니다.
    그쪽의 이동은 `rank_shift` 가 따로 본다 - 정원이 보존되므로 오류가
    들어온 자리는 반드시 무언가가 비운 자리다.
    """
    kinds = sorted({m.label_kind for m in movements if m.label_kind})
    out: dict[str, KindBreakdown] = {}
    for kind in kinds:
        part = summarize([m for m in movements if m.label_kind == kind])
        out[kind] = KindBreakdown(kind=kind, gained=len(part.gained), lost=len(part.lost))
    return out


def rank_shift(movements: Sequence[Movement], *, inside: bool) -> RankShift:
    """후보 안(`inside=True`) 또는 밖의 순위 이동을 요약한다.

    **이 둘을 갈라 놓는 것이 "비대칭"의 정의다.** 후보 안은 Tier 1 신호로
    점수가 오를 기회를 얻고 후보 밖은 못 얻는다 - 그래서 후보 밖에서는
    위로 갈 방법이 구조적으로 없고, `pulled_up` 이 0 이어야 정상이다.
    0 이 아니면 후보 안의 누군가가 점수를 **잃었다**는 뜻인데, noisy-or 는
    점수를 올리기만 하므로 그런 일은 일어나지 않아야 한다.
    """
    part = [m for m in movements if m.is_candidate is inside]
    deltas = [m.delta_rank for m in part]
    return RankShift(
        n=len(part),
        pushed_down=sum(1 for d in deltas if d > 0),
        pulled_up=sum(1 for d in deltas if d < 0),
        median=median(deltas) if deltas else 0.0,
        mean=fmean(deltas) if deltas else 0.0,
        worst=max(deltas) if deltas else 0,
    )


_PRIORITY_MIXED_UP = (
    "재설계 전 조건에 우선 집합이 실려 있다 - 전후를 뒤바꿔 넘겼다. "
    "전 조건은 `priority_ids=frozenset()` 을 주입한 실행이라 우선 집합이 비어야 한다"
)
_PRIORITY_MISSING = (
    "재설계 후 조건에 우선 집합이 없다 - 전후를 뒤바꿔 넘겼거나, "
    "극성 판정이 빈 집합을 냈다(미지원 언어 경고를 확인할 것)"
)


def render_pushout(*, budget: float, before: Sequence[Movement], after: Sequence[Movement]) -> str:
    """재설계 전후의 밀어냄을 나란히 싣는다 (이월 22번).

    **전후 라벨을 믿지 않고 데이터로 검증한다.** 뒤바꿔 넘겨도 숫자는
    그럴듯하게 나오는데 결론은 정반대가 된다 - 라벨은 호출부가 붙이는
    것이라 실수하면 렌더러가 알 방법이 없다. 두 실행을 구조적으로 가르는
    성질이 우선 집합의 유무이므로 그것으로 판정한다.
    """
    if any(m.is_priority for m in before):
        raise ValueError(_PRIORITY_MIXED_UP)
    if not any(m.is_priority for m in after):
        raise ValueError(_PRIORITY_MISSING)

    lines = [f"### 밀어냄 분해 (예산 {budget:.0%})", ""]
    lines += ["| 조건 | 부류 | 유입 | 유실 | 순증 |", "| --- | --- | ---: | ---: | ---: |"]
    for label, moves in (("재설계 전", before), ("재설계 후", after)):
        for kind, bd in sorted(breakdown_by_kind(moves).items()):
            lines.append(f"| {label} | {kind} | {bd.gained} | {bd.lost} | {bd.net:+d} |")
    lines += [
        "",
        "**순증만으로는 갈리지 않는 두 항이다.** 유입은 Tier 1 이 건져 올린 것이고,"
        " 유실은 후보 밖에서 밀려난 것이다 - 둘이 같은 수면 리포트에는 아무 일도"
        " 없었던 것으로 보인다.",
        "",
        "| 조건 | 집합 | n | 밀려남 | 올라감 | Δrank 중앙값 | 평균 | 최악 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, moves in (("재설계 전", before), ("재설계 후", after)):
        for name, inside in (("후보 안", True), ("후보 밖", False)):
            sh = rank_shift(moves, inside=inside)
            lines.append(
                f"| {label} | {name} | {sh.n} | {sh.pushed_down} | {sh.pulled_up} |"
                f" {sh.median:+.1f} | {sh.mean:+.2f} | {sh.worst:+d} |"
            )
    lines += [
        "",
        "**후보 밖의 `올라감` 이 0 인 것이 비대칭의 정의다.** noisy-or 는 점수를"
        " 올리기만 하고 Tier 1 은 후보에만 신호를 더하므로, 후보 밖에서는 위로 갈"
        " 방법이 구조적으로 없다.",
    ]
    return "\n".join(lines)
