"""코퍼스 로더와 필터 (설계 스펙 §4.1, §4.3).

**제거 건수 자체가 결과다**(§4.4). 몇 %가 왜 빠졌는지를 리포트에 실어야
표본이 편향됐는지 독자가 판단할 수 있다.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SentencePair:
    """줄 정렬된 문장 한 쌍. 원문은 항상 ko다 (Q2: ko→en/ja)."""

    source: str
    target: str


@dataclass(frozen=True, slots=True)
class FilterStats:
    """무엇을 왜 뺐는지. 합이 맞지 않으면 조용히 사라진 표본이 있다."""

    total: int
    kept: int
    dropped: dict[str, int] = field(default_factory=dict)


def load_pairs(ko_path: Path, other_path: Path) -> list[SentencePair]:
    """moses 평문 두 파일을 줄 단위로 짝짓는다.

    **줄 수가 다르면 실패시킨다.** 한 줄이라도 밀리면 그 뒤 전부가 오정렬이
    되는데, 그 상태의 측정은 "전부 오역"이라 Recall이 100%처럼 보인다 —
    가장 그럴듯해 보이는 틀린 숫자다.
    """
    ko_lines = ko_path.read_text(encoding="utf-8").splitlines()
    other_lines = other_path.read_text(encoding="utf-8").splitlines()
    if len(ko_lines) != len(other_lines):
        raise ValueError(
            f"줄 수가 다르다: {ko_path.name}={len(ko_lines):,} "
            f"{other_path.name}={len(other_lines):,}"
        )
    return [SentencePair(k, o) for k, o in zip(ko_lines, other_lines, strict=True)]


def filter_pairs(
    pairs: Sequence[SentencePair],
    *,
    max_ratio: float = 6.0,
    min_ratio: float = 0.15,
) -> tuple[list[SentencePair], FilterStats]:
    """빈 문자열·중복·극단 길이비를 제거한다.

    길이비 한도는 **주입 전에 이미 망가진 쌍**을 빼기 위한 것이다. 깨끗한
    트랙이 아니면 `length.ratio` 신호의 오탐과 주입분을 구분할 수 없다.
    6.0/0.15는 ko→en에서 관용적으로 나올 수 있는 범위 밖이다 — 이보다
    좁히면 정상 번역이 표본에서 빠져 코퍼스가 인위적으로 균질해진다.
    """
    dropped = {"empty": 0, "duplicate": 0, "ratio": 0}
    seen: set[tuple[str, str]] = set()
    kept: list[SentencePair] = []

    for pair in pairs:
        src, tgt = pair.source.strip(), pair.target.strip()
        if not src or not tgt:
            dropped["empty"] += 1
            continue
        key = (src, tgt)
        if key in seen:
            dropped["duplicate"] += 1
            continue
        ratio = len(tgt) / len(src)
        if ratio > max_ratio or ratio < min_ratio:
            dropped["ratio"] += 1
            continue
        seen.add(key)
        kept.append(SentencePair(src, tgt))

    return kept, FilterStats(total=len(pairs), kept=len(kept), dropped=dropped)


def sample(pairs: Sequence[SentencePair], n: int, seed: int) -> list[SentencePair]:
    """시드 고정 표본. **입력을 변형하지 않는다.**

    `random.shuffle`을 원본에 걸면 같은 목록으로 두 번째 표본을 뽑을 때
    결과가 달라져 NFR-3(재현성)이 깨진다.
    """
    if n >= len(pairs):
        return list(pairs)
    return random.Random(seed).sample(list(pairs), n)
