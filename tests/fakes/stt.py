"""테스트용 STT 프로바이더 (설계 §8).

`tests/fakes/provider.py`의 형제다. 실제 HTTP를 치지 않으므로 결정론적이다.

**`transcribe`의 시그니처는 `SttProvider` 프로토콜과 글자 그대로 같아야 한다.**
`SttProvider`가 `@runtime_checkable`이 아니고 CI에 타입 검사기도 없어서,
어긋난 가짜도 `load_media`의 키워드 호출에서는 정상 동작해 조용히 통과한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from cuesift.stt.provider import Transcript, TranscriptCue
from cuesift.translate.provider import ProviderError


class FakeSttProvider:
    """정해진 큐를 그대로 돌려준다. `SttProvider` 프로토콜의 구현이다."""

    name = "fake-stt"

    def __init__(
        self,
        cues: list[tuple[float, float, str]],
        *,
        language: str | None = "ko",
        model: str = "fake",
        error: ProviderError | None = None,
    ) -> None:
        self._cues = cues
        self._language = language
        self._model = model
        self._error = error
        self.calls: list[Path] = []
        # **`language`를 버리면 안 된다.** 버리면 `provider.transcribe(path,
        # language=None)` 변이가 전 스위트에서 생존한다 - 키워드 자체를 지우면
        # `TypeError`로 죽지만 **값이 틀린 것**은 아무도 못 잡는다.
        # Whisper 계열에서 언어 힌트는 전사 결과를 실질적으로 바꾼다.
        self.languages: list[str | None] = []

    def transcribe(self, audio: Path, *, language: str | None) -> Transcript:
        self.calls.append(audio)
        self.languages.append(language)
        if self._error is not None:
            raise self._error
        # **`tuple`로 감싸는 것이 필수다.** `Transcript.__post_init__`이 리스트를
        # `ValueError`로 거절한다 - `frozen=True`가 얕아서 밖에서 큐 목록이
        # 바뀌면 인제스트가 두 번 읽을 때 다른 결과를 내기 때문이다.
        return Transcript(
            cues=tuple(TranscriptCue(start_s=s, end_s=e, text=t) for s, e, t in self._cues),
            language=self._language,
            model=self._model,
        )


class SequenceSttProvider:
    """호출 순서대로 예외를 던지거나 전사를 낸다. `SttProvider`의 구현이다.

    **`FakeSttProvider`로는 재시도 루프를 잴 수 없다.** 그쪽의 `error`는
    매번 같은 예외를 던지므로 "429 한 번 뒤 성공"이라는 전이가 만들어지지
    않고, 재시도 루프가 없는 구현과 있는 구현이 같은 결과를 낸다.

    목록이 소진되면 **마지막 원소를 반복한다** - 재시도 소진 테스트가
    `max_retries + 1`개를 손으로 세어 적지 않아도 된다.
    """

    name = "sequence-stt"

    def __init__(self, outcomes: Sequence[Transcript | ProviderError]) -> None:
        if not outcomes:
            raise ValueError("outcomes가 비었다. 0개 수집은 통과가 아니라 설정 오류다")
        self._outcomes = list(outcomes)
        self.calls: list[Path] = []
        self.languages: list[str | None] = []

    def transcribe(self, audio: Path, *, language: str | None) -> Transcript:
        self.calls.append(audio)
        self.languages.append(language)
        outcome = self._outcomes[min(len(self.calls) - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, ProviderError):
            raise outcome
        return outcome
