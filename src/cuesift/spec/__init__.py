"""자막 규격 프로파일과 검사 (요구사항정의서 §5.5, §8.3)."""

from __future__ import annotations

from cuesift.spec.check import (
    SpecViolation,
    TrackViolation,
    check_empty_cues,
    check_overlaps,
    check_text,
    check_track,
)
from cuesift.spec.counting import CharCounting, text_width
from cuesift.spec.profile import SpecProfile, available_builtins, load_builtin, load_profile

__all__ = [
    "CharCounting",
    "SpecProfile",
    "SpecViolation",
    "TrackViolation",
    "available_builtins",
    "check_empty_cues",
    "check_overlaps",
    "check_text",
    "check_track",
    "load_builtin",
    "load_profile",
    "text_width",
]
