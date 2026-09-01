"""테스트용 STT 프로바이더 (설계 §8).

`tests/fakes/provider.py`의 형제다. 실제 HTTP를 치지 않으므로 결정론적이다.

**`transcribe`의 시그니처는 `SttProvider` 프로토콜과 글자 그대로 같아야 한다.**
`SttProvider`가 `@runtime_checkable`이 아니고 CI에 타입 검사기도 없어서,
어긋난 가짜도 `load_media`의 키워드 호출에서는 정상 동작해 조용히 통과한다.
"""

from __future__ import annotations

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

    def transcribe(self, audio: Path, *, language: str | None) -> Transcript:
        self.calls.append(audio)
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
