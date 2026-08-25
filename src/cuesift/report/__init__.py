"""검수 리포트 산출물 (요구사항정의서 §7 · FR-7.2)."""

from __future__ import annotations

from cuesift.report.json_report import build_review, write_review
from cuesift.report.models import COST_BASIS, COST_INCLUDES_TRANSLATION, TriageOutcome

# `COST_INCLUDES_TRANSLATION`을 여기서 내보내는 것은 CLI가 Tier 1을 켤 때
# `(*COST_INCLUDES_TRANSLATION, "tier1")`로 넓히기 때문이다 - 호출부가
# `("translation", "tier1")`을 손으로 적으면 기본 범위의 정의가 두 곳이 된다.
__all__ = [
    "COST_BASIS",
    "COST_INCLUDES_TRANSLATION",
    "TriageOutcome",
    "build_review",
    "write_review",
]
