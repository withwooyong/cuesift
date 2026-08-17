"""신호 수집기 인터페이스와 레지스트리 (요구사항정의서 FR-6.5, NFR-5).

**수집기 인터페이스가 둘인 이유**: FR-3.6(길이비 이상치)은 "언어쌍
분포에서 이상치"를 판정하므로 트랙 전체를 봐야 한다. 세그먼트 단위
프로토콜에 억지로 끼우면 수집기가 세그먼트마다 전체를 다시 훑게 된다.

레지스트리는 v0.2에서 QE 모델(Tier 2)을 코드 수정 없이 꽂기 위한 자리다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from cuesift.glossary import Glossary
from cuesift.segment import Segment, Signal
from cuesift.spec import SpecProfile

if TYPE_CHECKING:
    # 런타임 import를 피한다. `from __future__ import annotations`가 있어
    # 애노테이션이 문자열이므로 실행에 필요 없고, signals -> translate 방향
    # 의존을 실제로 만들지 않는다.
    from cuesift.translate.provider import Provider


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


@dataclass(frozen=True, slots=True)
class Tier1Context:
    """Tier 1 수집기가 LLM을 부르는 데 필요한 것 (설계 §4.1).

    `SignalContext`를 상속하지 않고 **담는다** - 상속하면 Tier 0 수집기가
    `Tier1Context`를 받아도 타입 검사를 통과해, 이 분리가 노리는 격리가
    사라진다.

    **프로바이더를 직접 담지 않고 팩토리로 받는다.** 자가일관성은 시도마다
    다른 `attempt`로 캐시를 갈라야 하는데(설계 §8), 프로바이더를 그대로
    담으면 수집기가 `identity`·`cache_dir`을 알아야 한다 - 신호 수집기가
    캐시 구조에 결합된다.
    """

    signal: SignalContext
    provider_for: Callable[[int], Provider]
    samples: int
    temperature: float

    def __post_init__(self) -> None:
        # 0이면 재번역이 전부 동일해 자가일관성 점수가 **항상 0.0**이 된다.
        # 신호가 죽었는데 "안전"으로 보고되는 무음 열화다(Q3).
        if not self.temperature > 0.0:
            raise ValueError(f"temperature는 0보다 커야 한다 (받은 값: {self.temperature})")
        # float(예: 2.7)이 여기를 통과하면 Task 5의 `range(ctx.samples)`에서
        # TypeError로 늦게 터진다 - 생성 시점 검증의 취지("나중에 안 터지게")를
        # 비켜 간다. bool은 int의 서브클래스라 samples=True는 아래 `< 2`에서
        # 이미 걸린다 - 별도로 막을 필요가 없다.
        if not isinstance(self.samples, int):
            raise ValueError(f"samples는 int여야 한다 (받은 타입: {type(self.samples).__name__})")
        # 2개 미만이면 비교할 쌍이 만들어지지 않는다.
        if self.samples < 2:
            raise ValueError(f"samples는 2 이상이어야 한다 (받은 값: {self.samples})")


@runtime_checkable
class Tier1Collector(Protocol):
    """LLM을 불러 판정하는 수집기. **후보 세그먼트에만** 실행된다."""

    name: str
    tier: int

    def collect_tier1(self, seg: Segment, ctx: Tier1Context) -> Signal | None:
        """신호를 내거나, 해당 없으면 None을 낸다.

        `SegmentCollector.collect`와 같은 계약이다 - None과 score=0.0은
        다르다.
        """
        ...


_Collector = SegmentCollector | BatchCollector | Tier1Collector

_REGISTRY: dict[str, _Collector] = {}


def registry() -> dict[str, _Collector]:
    """등록된 수집기 사전. 테스트가 저장·복원할 수 있도록 노출한다."""
    return _REGISTRY


def register(collector: _Collector) -> None:
    """수집기를 등록한다."""
    if collector.name in _REGISTRY:
        # 조용히 덮어쓰면 앞선 신호가 사라지고, 그 신호가 잡던 오류가
        # 리포트에서 통째로 빠진다. 원인을 역추적하기 매우 어렵다.
        raise ValueError(f"신호 이름이 중복됐다: {collector.name}")
    # 이 태스크(Tier 1 실행 격리)가 `tier` 값을 실행 경로 결정에 처음 쓰기
    # 시작했으므로 이제 그것이 필수 계약이다. 없으면 여기가 아니라 나중
    # collect_all·collect_tier1의 수집 루프에서 AttributeError로 죽어,
    # 원인이 등록이 아니라 수집처럼 보인다.
    if not hasattr(collector, "tier"):
        raise ValueError(f"수집기 '{collector.name}'에 tier 속성이 없다")
    _REGISTRY[collector.name] = collector


def collect_all(
    segments: Sequence[Segment],
    ctx: SignalContext,
    enabled: Iterable[str] | None = None,
) -> dict[str, list[Signal]]:
    """**tier 0 수집기만** 돌려 세그먼트별 신호 목록을 만든다 (설계 §4.1 D6).

    Tier 1은 여기서 돌지 않는다 — 전량 LLM 호출이 되기 때문이다.
    `collect_tier1`을 쓴다. `enabled`를 주면 그 이름들만 실행하되
    **tier 0이 아닌 이름은 거부한다** — ablation 측정에 쓴다.
    """
    if enabled is None:
        # **tier 0만 돈다.** Tier 1이 여기서 실행되면 전량 LLM 호출이
        # 일어난다 - 요구사항정의서 §4가 "감당 불가"라고 적은 사고다.
        names = [n for n, c in _REGISTRY.items() if c.tier == 0]
    else:
        names = list(enabled)
        # 오타로 신호를 껐는데 "기여도 0"으로 읽히면 잘못된 결론이 나온다.
        unknown = [n for n in names if n not in _REGISTRY]
        if unknown:
            raise ValueError(f"등록되지 않은 신호: {', '.join(sorted(unknown))}")
        # 조용히 건너뛰지 않는 것도 같은 이유다. tier 0이 아닌 이름을 넣었는데
        # 말없이 빠지면 ablation이 그 신호를 "기여 0"으로 집계한다.
        # 변수명은 `higher`가 아니라 `non_tier0`다 - tier 2가 생기면 "높다"는
        # 뜻이 어긋난다. 메시지도 각 이름의 실제 tier를 실어 자기설명적으로
        # 만든다 - "collect_tier1을 쓸 것"이라고만 적으면 tier 2 이름이
        # 섞였을 때 사용자를 또 다른 ValueError로 보낸다.
        non_tier0 = [n for n in names if _REGISTRY[n].tier != 0]
        if non_tier0:
            detail = ", ".join(f"{n}(tier={_REGISTRY[n].tier})" for n in sorted(non_tier0))
            raise ValueError(f"collect_all은 tier 0만 실행한다: {detail}")

    # 신호가 하나도 없는 세그먼트도 키를 갖는다. 빠진 키는 KeyError를 부른다.
    result: dict[str, list[Signal]] = {seg.id: [] for seg in segments}

    for name in names:
        collector = _REGISTRY[name]
        # 프로토콜 isinstance가 아니라 hasattr로 가른다. runtime_checkable
        # 프로토콜의 isinstance는 데이터 멤버(name·tier)까지 hasattr로 확인해
        # 두 프로토콜이 동시에 참이 될 수 있고, 판정이 미묘해진다.
        #
        # **`_REGISTRY`는 이제 `collect`가 없는 tier 1 수집기도 담는다.**
        # 이 `else` 가지가 안전한 것은 오직 위의 tier 필터 덕분이다 -
        # 필터가 사라지면(예: `== 0`이 `!= 1`로 느슨해지면) 여기서
        # `AttributeError`로 죽는다.
        if hasattr(collector, "collect_batch"):
            for seg_id, signal in collector.collect_batch(segments, ctx).items():
                result[seg_id].append(signal)
        else:
            for seg in segments:
                signal = collector.collect(seg, ctx)
                if signal is not None:
                    result[seg.id].append(signal)

    return result


def collect_tier1(
    segments: Sequence[Segment],
    ctx: Tier1Context,
    enabled: Iterable[str] | None = None,
) -> dict[str, list[Signal]]:
    """tier 1 수집기를 **주어진 세그먼트에만** 돌린다 (FR-4.1 · 설계 §4.1).

    **호출자가 후보를 이미 좁혀서 넘긴다.** 이 함수는 상한(FR-4.3)을
    강제하지 않는다 - 상한은 `select_tier1_candidates`의 일이고, 두 곳이
    같은 정책을 나눠 가지면 어긋난다. 다만 그 함수가 자르는 것은
    **세그먼트 수뿐**이다 - 총 호출 수는 세그먼트 수 × 수집기 수 × `samples`고,
    `samples`는 여기서도 `Tier1Context`에서도 상한이 없다(실제 상한은 값이
    입력되는 자리, 즉 CLI가 근거와 함께 둔다).

    **수집기가 자기 실패를 삼킨다고 전제한다.** 여기서는 예외를 잡지 않으므로
    후반 세그먼트에서 난 예외가 앞서 성공한 세그먼트의 신호를 통째로 날린다
    (이미 쓴 LLM 비용까지 소실된다). 설계 §6.2가 부분 실패 처리를 수집기 쪽에
    배정했으므로 이 전파 자체는 위반이 아니지만, `Tier1Collector.collect_tier1`
    구현이 예외를 올려 보내면 조용한 사고가 된다.
    """
    if enabled is None:
        names = [n for n, c in _REGISTRY.items() if c.tier == 1]
    else:
        names = list(enabled)
        unknown = [n for n in names if n not in _REGISTRY]
        if unknown:
            raise ValueError(f"등록되지 않은 신호: {', '.join(sorted(unknown))}")
        # collect_all의 non_tier0 분기와 대칭이다 - 변수명·메시지 모두
        # 실제 tier를 실어 자기설명적으로 만든다.
        non_tier1 = [n for n in names if _REGISTRY[n].tier != 1]
        if non_tier1:
            detail = ", ".join(f"{n}(tier={_REGISTRY[n].tier})" for n in sorted(non_tier1))
            raise ValueError(f"collect_tier1은 tier 1만 실행한다: {detail}")

    # 신호가 하나도 없는 세그먼트도 키를 갖는다. 빠진 키는 KeyError를 부른다.
    result: dict[str, list[Signal]] = {seg.id: [] for seg in segments}

    for name in names:
        collector = _REGISTRY[name]
        # collect_all의 hasattr 분기와 같은 이유다 - `_REGISTRY`에는
        # `collect_tier1`이 없는 tier 0 수집기도 있다. 위 tier 필터가
        # 사라지면 이 루프가 `AttributeError`로 죽는다.
        for seg in segments:
            signal = collector.collect_tier1(seg, ctx)
            if signal is not None:
                result[seg.id].append(signal)

    return result
