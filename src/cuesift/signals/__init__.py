"""신호 수집기 - tier 0 구현체와 tier 0/1 공용 인터페이스 (요구사항정의서 §5.3, 설계 §4.1).

`Tier1Context`·`Tier1Collector`·`collect_tier1`은 여기서 함께 내보내지만
tier 1 **구현체**는 이 모듈이 아니라 별도 모듈(Task 5, `llm.self_consistency`)이
등록한다 - 여기 있는 것은 계약뿐이다.
"""

from __future__ import annotations

from cuesift.signals.base import (
    BatchCollector,
    SegmentCollector,
    SignalContext,
    Tier1Collector,
    Tier1Context,
    collect_all,
    collect_tier1,
    register,
    registry,
)

__all__ = [
    "BatchCollector",
    "SegmentCollector",
    "SignalContext",
    "Tier1Collector",
    "Tier1Context",
    "collect_all",
    "collect_tier1",
    "register",
    "registry",
]

# 임포트만으로 레지스트리에 등록된다. ruff가 미사용으로 지우지 않도록 아래에 지시자를 붙인다.
from cuesift.signals import derived, structural  # noqa: E402,F401
