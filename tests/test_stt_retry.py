"""STT 재시도 루프 (FR-1.2 · 설계 §6).

**이 루프가 없으면 사용자가 몇 분을 기다린 뒤 429 하나로 전부 잃는다.**
어댑터는 `Retry-After`까지 실어 `RetryableProviderError`를 던지는데 배선
이전에는 그것을 받는 코드가 리포 전체에 0건이었다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fakes.stt import SequenceSttProvider

from cuesift.ingest import IngestError
from cuesift.retry import MAX_BACKOFF_S
from cuesift.stt.provider import Transcript, TranscriptCue
from cuesift.stt.retry import STT_MAX_RETRIES, transcribe_with_retry
from cuesift.translate.provider import FatalProviderError, RetryableProviderError


def _transcript() -> Transcript:
    return Transcript(
        cues=(TranscriptCue(start_s=0.0, end_s=1.0, text="안녕"),),
        language="ko",
        model="fake",
    )


@pytest.fixture
def media(tmp_path: Path) -> Path:
    # `load_media`가 `path.is_file()`을 먼저 본다. 내용은 프로바이더가 가짜라
    # 읽히지 않으므로 존재하기만 하면 된다.
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"not really a video")
    return path


def test_429_뒤_성공은_두_번_부른다(media: Path) -> None:
    """G5. 재시도 루프가 없으면 첫 호출에서 예외가 그대로 샌다."""
    provider = SequenceSttProvider(
        [RetryableProviderError("429 rate limited", retry_after_s=5.0), _transcript()]
    )
    waited: list[float] = []

    result = transcribe_with_retry(provider, media, language="ko", sleep=waited.append)

    assert len(provider.calls) == 2
    assert len(result.segments) == 1
    # 서버가 준 힌트를 그대로 쓴다. 지수 백오프로 떨어지면 1.0이 된다.
    assert waited == [5.0]


def test_힌트가_없으면_지수로_잔다(media: Path) -> None:
    provider = SequenceSttProvider([RetryableProviderError("503"), _transcript()])
    waited: list[float] = []

    transcribe_with_retry(provider, media, language="ko", sleep=waited.append)

    assert waited == [1.0]


def test_Fatal은_재시도되지_않는다(media: Path) -> None:
    """G6. 두 예외를 한 절로 잡는 구현에서 호출이 4회가 된다.

    **두 예외를 형제로 두는 계약이 여기에도 걸린다.** `FatalProviderError`를
    `RetryableProviderError`의 하위로 옮기면 이 루프가 인증 실패를 네 번
    재시도하고, 사용자는 틀린 키로 네 번을 기다린다.
    """
    provider = SequenceSttProvider([FatalProviderError("401 unauthorized")])
    waited: list[float] = []

    with pytest.raises(FatalProviderError, match="401"):
        transcribe_with_retry(provider, media, language="ko", sleep=waited.append)

    assert len(provider.calls) == 1
    assert waited == []


def test_재시도_소진은_마지막_예외를_전파한다(media: Path) -> None:
    provider = SequenceSttProvider([RetryableProviderError("429")])
    waited: list[float] = []

    with pytest.raises(RetryableProviderError, match="429"):
        transcribe_with_retry(provider, media, language="ko", sleep=waited.append)

    # 재시도 3회면 호출은 4회다.
    assert len(provider.calls) == STT_MAX_RETRIES + 1
    # **마지막 시도 뒤에는 자지 않는다** - 호출 N+1회에 대기는 N회다.
    # 거기서 자면 아무도 기다릴 이유가 없는 시간을 CLI가 쓴다.
    assert len(waited) == STT_MAX_RETRIES


def test_on_retry가_재시도마다_불린다(media: Path) -> None:
    """라이브러리가 문구를 알지 않는 통로다 (설계 §6)."""
    provider = SequenceSttProvider(
        [RetryableProviderError("429", retry_after_s=2.0), _transcript()]
    )
    seen: list[tuple[int, float, str]] = []

    transcribe_with_retry(
        provider,
        media,
        language="ko",
        on_retry=lambda attempt, delay, exc: seen.append((attempt, delay, str(exc))),
        sleep=lambda _: None,
    )

    assert seen == [(0, 2.0, "429")]


def test_상한이_STT_경로에도_걸린다(media: Path) -> None:
    provider = SequenceSttProvider(
        [RetryableProviderError("429", retry_after_s=86400.0), _transcript()]
    )
    waited: list[float] = []

    transcribe_with_retry(provider, media, language="ko", sleep=waited.append)

    assert waited == [MAX_BACKOFF_S]


def test_IngestError는_재시도되지_않는다(tmp_path: Path) -> None:
    """합성 실패는 다시 걸어도 같다. 파일이 없는 것도 마찬가지다."""
    provider = SequenceSttProvider([_transcript()])

    with pytest.raises(IngestError) as caught:
        transcribe_with_retry(provider, tmp_path / "없다.mp4", language="ko", sleep=lambda _: None)

    assert caught.value.reason == "not_found"
    assert provider.calls == []


def test_언어_힌트가_프로바이더까지_간다(media: Path) -> None:
    # 값이 틀린 것은 시그니처 검사로 잡히지 않는다. Whisper 계열에서 언어
    # 힌트는 전사 결과를 실질적으로 바꾼다.
    provider = SequenceSttProvider([_transcript()])

    transcribe_with_retry(provider, media, language="ja", sleep=lambda _: None)

    assert provider.languages == ["ja"]


def test_stt_패키지가_이_모듈을_export하지_않는다() -> None:
    """**export하면 순환 임포트가 된다** (실측).

    `cuesift.ingest.loader` → `cuesift.stt.provider` → `cuesift.stt.__init__`
    → 여기 → `cuesift.ingest.loader`(초기화 중)로 돌아
    `ImportError: cannot import name 'load_media' from partially initialized
    module`이 난다. export하지 않으면 두 임포트 순서 모두 정상이다.

    이 게이트가 없으면 다음 사람이 "왜 이것만 `__all__`에 없지"라며 더하고,
    실패는 임포트 시점이라 **스위트 전체가 한꺼번에 죽는다.**
    """
    import cuesift.stt

    assert not hasattr(cuesift.stt, "transcribe_with_retry")
