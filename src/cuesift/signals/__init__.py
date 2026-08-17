"""Tier 0 신호 수집기 (요구사항정의서 §5.3)."""

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
