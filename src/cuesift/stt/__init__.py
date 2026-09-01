"""STT 어댑터 (요구사항정의서 FR-1.2 · 설계 D1~D4)."""

from __future__ import annotations

from cuesift.stt.openai_compat import OpenAICompatibleSttProvider
from cuesift.stt.provider import SttProvider, Transcript, TranscriptCue

__all__ = [
    "OpenAICompatibleSttProvider",
    "SttProvider",
    "Transcript",
    "TranscriptCue",
]
