"""`stt/provider.py`의 계약 방어 (설계 D3·D5)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from cuesift.stt.provider import SttProvider, Transcript, TranscriptCue


def test_유한하지_않은_시작_시각을_거부한다() -> None:
    # `round(nan * 1000)`은 ValueError이고 그것은 ProviderError 밖이라
    # 호출부의 폴백이 받지 못한다 (실측).
    with pytest.raises(ValueError, match="유한한 수"):
        TranscriptCue(start_s=math.nan, end_s=1.0, text="가")


def test_무한대_종료_시각을_거부한다() -> None:
    # inf는 OverflowError를 내는데 그것도 ProviderError 밖이다 (실측).
    with pytest.raises(ValueError, match="유한한 수"):
        TranscriptCue(start_s=0.0, end_s=math.inf, text="가")


def test_수가_아닌_타임코드를_거부한다() -> None:
    # `math.isfinite("1.5")`가 TypeError를 내므로 타입 검사가 먼저 와야 한다.
    with pytest.raises(ValueError, match="유한한 수"):
        TranscriptCue(start_s="0.0", end_s=1.0, text="가")  # type: ignore[arg-type]


def test_불리언_타임코드를_거부한다() -> None:
    # `isinstance(True, int | float)`가 True라 타입 검사만으로는 통과한다.
    # `round(True * 1000)`은 1000이 되어 **1초짜리 큐가 조용히 생긴다**.
    with pytest.raises(ValueError, match="유한한 수"):
        TranscriptCue(start_s=True, end_s=1.0, text="가")  # type: ignore[arg-type]


def test_음수_시작_시각을_거부한다() -> None:
    with pytest.raises(ValueError, match="음수"):
        TranscriptCue(start_s=-0.5, end_s=1.0, text="가")


def test_역전된_타임코드를_거부한다() -> None:
    with pytest.raises(ValueError, match="작다"):
        TranscriptCue(start_s=2.0, end_s=1.0, text="가")


def test_같은_시각은_허용한다() -> None:
    # 길이 0 큐는 STT가 실제로 낸다. 역전이 아니므로 통과시키고,
    # 표시 가치 판정은 인제스트가 한다.
    cue = TranscriptCue(start_s=1.0, end_s=1.0, text="가")
    assert cue.end_s == cue.start_s


def test_텍스트가_문자열이_아니면_거부한다() -> None:
    # None이면 `Segment.source_text`가 None이 되고 Tier 0 신호가
    # 전부 AttributeError로 죽는다.
    with pytest.raises(ValueError, match="str"):
        TranscriptCue(start_s=0.0, end_s=1.0, text=None)  # type: ignore[arg-type]


def test_transcript는_큐를_튜플로_동결한다() -> None:
    t = Transcript(
        cues=(TranscriptCue(start_s=0.0, end_s=1.0, text="가"),),
        language="ko",
        model="whisper-1",
    )
    assert isinstance(t.cues, tuple)
    with pytest.raises(AttributeError):
        t.language = "en"  # type: ignore[misc]


def test_protocol이_기대하는_시그니처를_고정한다() -> None:
    # 구현체가 인자 이름을 바꾸면 호출부가 키워드로 부르다 TypeError를 낸다.
    # 그 실패는 실행 한참 뒤에 드러나므로 여기서 고정한다
    # (`translate/provider.py`의 Provider가 같은 이유로 같은 단언을 갖는다).
    import inspect

    sig = inspect.signature(SttProvider.transcribe)
    assert list(sig.parameters) == ["self", "audio", "language"]
    assert sig.parameters["audio"].annotation == "Path"
    assert sig.parameters["language"].kind is inspect.Parameter.KEYWORD_ONLY
    assert Path  # import가 쓰였음을 표시한다
