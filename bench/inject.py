"""③ inject — 오류 주입과 정답 라벨 (설계 스펙 §5).

**세그먼트당 최대 1개 오류.** 유형별 Recall이 정의되려면 라벨이 배타적이어야 한다.

**어떤 유형이든 실주입이 0건이면 실패시킨다.** 링크 체커에서 얻은 교훈의
직접 적용이다 — "0 broken"이 통과로 읽혔던 것처럼 "용어 위반 Recall 100%"가
실은 "1건도 주입 못 했음"일 수 있다.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from cuesift.glossary import Glossary
from cuesift.segment import Segment
from cuesift.spec import SpecProfile, check_text

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 부정 표현. 삽입·삭제 양방향으로 쓴다.
_NEGATIONS_EN = (" not ", " never ")

Injector = Callable[[Segment, Glossary, SpecProfile, random.Random], "tuple[Segment, dict] | None"]


@dataclass(frozen=True, slots=True)
class Label:
    """정답 한 건. `detail`은 라운드트립 검증에 쓴다."""

    segment_id: str
    kind: str
    detail: dict = field(default_factory=dict)


def _untranslated(seg, glossary, profile, rng):
    """FR-3.1 — 번역문을 ko 원문으로 되돌린다."""
    return replace(seg, target_text=seg.source_text), {"replaced_with": "source"}


def _empty(seg, glossary, profile, rng):
    """FR-3.2 — 빈 값. 공백만 남기는 경우도 섞는다."""
    value = rng.choice(["", "   "])
    return replace(seg, target_text=value), {"value": value}


def _degeneration(seg, glossary, profile, rng):
    """FR-3.3 — 마지막 어절을 3~8회 반복한다."""
    tokens = (seg.target_text or "").split()
    if not tokens:
        return None
    times = rng.randint(3, 8)
    return replace(seg, target_text=" ".join(tokens + [tokens[-1]] * times)), {"repeats": times}


def _number(seg, glossary, profile, rng):
    """FR-3.4 — 숫자 1개를 변경하거나 삭제한다. **숫자가 없으면 자격 미달.**"""
    text = seg.target_text or ""
    matches = list(_NUMBER.finditer(text))
    if not matches:
        return None
    m = rng.choice(matches)
    original = m.group()
    if rng.random() < 0.5:
        digits = original.replace(",", "")
        try:
            changed = str(int(digits) + rng.randint(1, 9))
        except ValueError:
            changed = ""
    else:
        changed = ""
    mutated = text[: m.start()] + changed + text[m.end() :]
    return replace(seg, target_text=mutated), {"from": original, "to": changed}


def _glossary(seg, glossary, profile, rng):
    """FR-3.7 — 대응어를 비등재 표현으로 치환한다.

    **원문에 용어집 키가 있고 번역문에 대응어가 있는 세그먼트만 자격이 있다.**
    둘 중 하나만 있으면 치환할 대상이 없거나 이미 위반 상태다.
    """
    text = seg.target_text or ""
    for entry in glossary.entries:
        if entry.source not in seg.source_text:
            continue
        for target in entry.targets:
            idx = text.lower().find(target.lower())
            if idx < 0:
                continue
            bogus = "thingamajig"
            mutated = text[:idx] + bogus + text[idx + len(target) :]
            return replace(seg, target_text=mutated), {"term": entry.source, "from": target}
    return None


def _spec(seg, glossary, profile, rng):
    """FR-3.8 — duration을 줄여 CPS를 넘긴다.

    텍스트 확장이 아니라 duration 축소를 쓰는 이유는, 확장하면 길이비 신호가
    함께 발화해 **라벨의 배타성이 흐려지기** 때문이다.
    """
    shrunk = max(profile.min_duration_ms // 2, int(seg.duration_ms * 0.25))
    if shrunk >= seg.duration_ms:
        return None
    return replace(seg, end_ms=seg.start_ms + shrunk), {
        "from_ms": seg.duration_ms,
        "to_ms": shrunk,
    }


def _negation(seg, glossary, profile, rng):
    """의미 반전. **검출 담당이 없다** — 이 유형의 Recall 0이 Tier 1 투자 근거다.

    **주입 결과가 규격을 위반하면 주입하지 않는다.** 부정어 삽입은 텍스트를
    늘려 CPS를 넘기는데(실측: en 폭 +10%, ja +20%, 트랙 여유는 SAFETY=1.10이라
    10%뿐이다), 그러면 spec.violation이 발화해 **의미 반전이 아니라 길이 증가를
    잡은 것**이 Recall로 집계된다. 스펙 §5.5의 "세그먼트당 최대 1개 오류,
    라벨은 배타적"에도 어긋난다 — 두 오류를 동시에 넣은 셈이기 때문이다.
    """
    text = seg.target_text or ""

    def _clean(candidate: str) -> tuple[Segment, dict] | None:
        mutated = replace(seg, target_text=candidate)
        if check_text(candidate, mutated.duration_ms, profile):
            return None
        return mutated

    for neg in _NEGATIONS_EN:
        if neg in text:
            candidate = text.replace(neg, " ", 1)
            out = _clean(candidate)
            if out is None:
                return None
            return out, {"removed": neg.strip()}

    tokens = text.split()
    if len(tokens) < 2:
        return None
    tokens.insert(1, "not")
    out = _clean(" ".join(tokens))
    if out is None:
        return None
    return out, {"inserted": "not"}


INJECTORS: dict[str, Injector] = {
    "untranslated": _untranslated,
    "empty": _empty,
    "degeneration": _degeneration,
    "number": _number,
    "glossary": _glossary,
    "spec": _spec,
    "negation": _negation,
}


def inject(
    segments: Sequence[Segment],
    glossary: Glossary,
    profile: SpecProfile,
    *,
    rate: float = 0.10,
    seed: int = 20260729,
) -> tuple[list[Segment], list[Label], dict[str, int]]:
    """오류를 주입하고 정답 라벨을 만든다.

    **입력을 변형하지 않는다** — 원본이 오염되면 "깨끗한 트랙 대비" 비교가
    불가능해진다.
    """
    if not 0.0 < rate <= 1.0:
        raise ValueError(f"rate는 0보다 크고 1 이하여야 한다 (받은 값: {rate})")

    rng = random.Random(seed)
    kinds = sorted(INJECTORS)
    target_total = round(len(segments) * rate)

    order = list(range(len(segments)))
    rng.shuffle(order)

    mutated = list(segments)
    labels: list[Label] = []
    skipped: dict[str, int] = {k: 0 for k in kinds}
    achieved: dict[str, int] = {k: 0 for k in kinds}

    quota = {k: target_total // len(kinds) for k in kinds}
    for i in range(target_total - sum(quota.values())):
        quota[kinds[i % len(kinds)]] += 1

    # 유형별 자격 세그먼트 수를 먼저 세어 처리 순서를 정한다.
    # **알파벳순으로 처리하면 자격이 희소한 유형이 인덱스를 대량 소비해
    # 뒤 유형이 굶는다** — 실트랙 en-ko에서 glossary 자격률이 1.78%라
    # 그 차례에 5,000개 중 약 3,800개를 소비하고 spec·untranslated가
    # 0건이 되어 아래 "실주입 0건" 가드가 10시드 전부 발동했다.
    # 시드를 고정한 별도 Random으로 세므로 이 사전 스캔은 본 주입에 쓰는
    # rng의 상태를 건드리지 않는다(재현성 유지).
    scarcity = {
        k: sum(
            1 for s in segments if INJECTORS[k](s, glossary, profile, random.Random(0)) is not None
        )
        for k in kinds
    }

    assigned: set[int] = set()
    for kind in sorted(kinds, key=lambda k: scarcity[k]):
        for idx in order:
            if achieved[kind] >= quota[kind]:
                break
            if idx in assigned:
                continue
            result = INJECTORS[kind](mutated[idx], glossary, profile, rng)
            if result is None:
                skipped[kind] += 1
                continue
            mutated[idx], detail = result
            assigned.add(idx)
            labels.append(Label(segment_id=mutated[idx].id, kind=kind, detail=detail))
            achieved[kind] += 1

    empty_kinds = [k for k in kinds if achieved[k] == 0]
    if empty_kinds:
        raise ValueError(
            f"실주입 0건인 유형이 있다: {', '.join(empty_kinds)}. "
            f"그대로 두면 '해당 유형 Recall 100%'가 실은 '1건도 주입 못 했음'이 된다. "
            f"자격 미달 건수: { {k: skipped[k] for k in empty_kinds} }"
        )

    # 최종 리뷰 Minor(Task 6 이월) — 0건 가드는 71/72처럼 1건 미달을 못 잡는다.
    # 리포트의 "유형별 실주입 건수"에 72가 아니라 71이 찍혀도 독자는 그것이
    # 정상 quota인지 1건 미달인지 구분할 수단이 없다. 라벨 총계가 목표(표본 ×
    # rate)에 못 미치면 실제 오류율이 낮아져 오라클 상한·배수의 분모 관계가
    # 조용히 바뀐다 — 스펙 §5.5가 "0 broken이 통과로 읽혔던 것처럼"이라며
    # 세운 원칙과 같다. `empty_kinds`와 메시지를 분리하는 이유는 0건은 원인
    # 파악(자격 미달 전멸)이 다르기 때문이다.
    shortfall = [k for k in kinds if achieved[k] < quota[k]]
    if shortfall:
        raise ValueError(
            f"quota를 채우지 못한 유형이 있다: "
            f"{ {k: f'{achieved[k]}/{quota[k]}' for k in shortfall} }. "
            f"그대로 두면 라벨 총계가 목표에 미달해 오류율이 낮아지고 "
            f"오라클 상한·배수의 분모 관계가 조용히 바뀐다. "
            f"자격 미달 건수: { {k: skipped[k] for k in shortfall} }"
        )

    return mutated, labels, skipped
