"""신호 수집기 인터페이스와 레지스트리 (요구사항정의서 FR-6.5, NFR-5).

**수집기 인터페이스가 둘인 이유**: FR-3.6(길이비 이상치)은 "언어쌍
분포에서 이상치"를 판정하므로 트랙 전체를 봐야 한다. 세그먼트 단위
프로토콜에 억지로 끼우면 수집기가 세그먼트마다 전체를 다시 훑게 된다.

레지스트리는 v0.2에서 QE 모델(Tier 2)을 코드 수정 없이 꽂기 위한 자리다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from cuesift.glossary import Glossary
from cuesift.segment import Segment, Signal
from cuesift.spec import SpecProfile


@dataclass(frozen=True, slots=True)
class SignalContext:
    """수집기가 판정에 쓰는 주변 정보."""

    profile: SpecProfile
    glossary: Glossary | None
    source_lang: str
    target_lang: str


@runtime_checkable
class SegmentCollector(Protocol):
    """세그먼트 하나만 보고 판정하는 수집기."""

    name: str
    tier: int

    def collect(self, seg: Segment, ctx: SignalContext) -> Signal | None:
        """신호를 내거나, 해당 없으면 None을 낸다.

        **None과 score=0.0은 다르다.** None은 "이 신호의 판정 대상이
        아니다"이고, 0.0은 "판정했고 안전하다"다. 해당 없음에 0점 신호를
        넣으면 §8.4 review.json이 무의미한 항목으로 채워진다.
        """
        ...


@runtime_checkable
class BatchCollector(Protocol):
    """트랙 전체를 봐야 판정되는 수집기 (분포 기반 신호)."""

    name: str
    tier: int

    def collect_batch(self, segments: Sequence[Segment], ctx: SignalContext) -> dict[str, Signal]:
        """신호가 있는 세그먼트 ID만 담아 반환한다."""
        ...


_REGISTRY: dict[str, SegmentCollector | BatchCollector] = {}


def registry() -> dict[str, SegmentCollector | BatchCollector]:
    """등록된 수집기 사전. 테스트가 저장·복원할 수 있도록 노출한다."""
    return _REGISTRY


def register(collector: SegmentCollector | BatchCollector) -> None:
    """수집기를 등록한다."""
    if collector.name in _REGISTRY:
        # 조용히 덮어쓰면 앞선 신호가 사라지고, 그 신호가 잡던 오류가
        # 리포트에서 통째로 빠진다. 원인을 역추적하기 매우 어렵다.
        raise ValueError(f"신호 이름이 중복됐다: {collector.name}")
    _REGISTRY[collector.name] = collector


def collect_all(
    segments: Sequence[Segment],
    ctx: SignalContext,
    enabled: Iterable[str] | None = None,
) -> dict[str, list[Signal]]:
    """모든 수집기를 돌려 세그먼트별 신호 목록을 만든다.

    `enabled`를 주면 그 이름들만 실행한다 — ablation 측정에 쓴다.
    """
    if enabled is None:
        names = list(_REGISTRY)
    else:
        names = list(enabled)
        # 오타로 신호를 껐는데 "기여도 0"으로 읽히면 잘못된 결론이 나온다.
        unknown = [n for n in names if n not in _REGISTRY]
        if unknown:
            raise ValueError(f"등록되지 않은 신호: {', '.join(sorted(unknown))}")

    # 신호가 하나도 없는 세그먼트도 키를 갖는다. 빠진 키는 KeyError를 부른다.
    result: dict[str, list[Signal]] = {seg.id: [] for seg in segments}

    for name in names:
        collector = _REGISTRY[name]
        # 프로토콜 isinstance가 아니라 hasattr로 가른다. runtime_checkable
        # 프로토콜의 isinstance는 데이터 멤버(name·tier)까지 hasattr로 확인해
        # 두 프로토콜이 동시에 참이 될 수 있고, 판정이 미묘해진다.
        if hasattr(collector, "collect_batch"):
            for seg_id, signal in collector.collect_batch(segments, ctx).items():
                result[seg_id].append(signal)
        else:
            for seg in segments:
                signal = collector.collect(seg, ctx)
                if signal is not None:
                    result[seg.id].append(signal)

    return result
