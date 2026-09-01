"""실제 STT 엔드포인트를 친다 (설계 D10 · FR-1.2).

**기본 제외다.** `pyproject.toml`의 `addopts`가 `-m "not live"`를 갖고 있어
`-m live`를 명시해야 돈다. 실행에는 환경변수 셋이 다 필요하다 —
`CUESIFT_LIVE_STT_BASE_URL` · `CUESIFT_LIVE_STT_MODEL` · `CUESIFT_LIVE_AUDIO`.
`CUESIFT_LIVE_STT_API_KEY`는 선택이다(로컬 백엔드는 키를 요구하지 않는다).

```powershell
$env:CUESIFT_LIVE_STT_BASE_URL = "http://localhost:8080/v1"
$env:CUESIFT_LIVE_STT_MODEL = "whisper-1"
$env:CUESIFT_LIVE_AUDIO = "C:/path/to/clip.mp3"
.venv/Scripts/python.exe -m pytest -m live tests/test_stt_live.py -v -s
```

**오디오를 리포에 넣지 않는다** (D10). 링크 체커도 markdownlint도 바이너리를
보지 않아 **어떤 게이트의 대상도 아닌 파일**이 된다. 그래서 경로를 환경변수로
받고, 그 경로는 리포 밖을 가리켜야 한다.

**환경변수가 없으면 skip이지 실패가 아니다.** `-m live`를 준 사람이 백엔드를
아직 안 띄운 상태와 백엔드가 고장난 상태를 갈라야 하기 때문이다 — 전자를
실패로 만들면 아무도 이 파일을 돌리지 않는다.

**Ollama는 `/v1/audio/transcriptions`를 아예 제공하지 않는다** (설계 §3).
백엔드를 고를 때 `verbose_json` 지원 여부부터 확인한다 (D4 · R1).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cuesift.ingest import load_media, write_subtitle
from cuesift.stt import OpenAICompatibleSttProvider

pytestmark = pytest.mark.live


@pytest.fixture
def live_provider() -> OpenAICompatibleSttProvider:
    """환경변수에서 엔드포인트를 읽는다. 없으면 skip."""
    base_url = os.environ.get("CUESIFT_LIVE_STT_BASE_URL")
    model = os.environ.get("CUESIFT_LIVE_STT_MODEL")
    if not base_url or not model:
        pytest.skip("CUESIFT_LIVE_STT_BASE_URL과 CUESIFT_LIVE_STT_MODEL이 필요하다")
    return OpenAICompatibleSttProvider(
        base_url=base_url,
        model=model,
        api_key=os.environ.get("CUESIFT_LIVE_STT_API_KEY"),
    )


@pytest.fixture
def live_audio() -> Path:
    """오디오 경로도 환경변수다 — 리포에 넣지 않는다 (D10)."""
    raw = os.environ.get("CUESIFT_LIVE_AUDIO")
    if not raw:
        pytest.skip("CUESIFT_LIVE_AUDIO에 오디오 경로가 필요하다")
    path = Path(raw)
    if not path.is_file():
        pytest.skip(f"CUESIFT_LIVE_AUDIO가 가리키는 파일이 없다: {path}")
    return path


def test_실제_백엔드가_타임코드를_낸다(
    live_provider: OpenAICompatibleSttProvider, live_audio: Path
) -> None:
    """**R1의 관문이다.** 백엔드가 `verbose_json`을 지원하는지 여기서 갈린다.

    지원하지 않으면 `FatalProviderError`가 나는데, 그것이 D4가 의도한
    **명시적 실패**다 — 조용히 통과하면 전 세그먼트가 `0ms~0ms`가 된다.
    """
    transcript = live_provider.transcribe(live_audio, language="ko")
    assert len(transcript.cues) > 0
    assert transcript.cues[0].end_s > transcript.cues[0].start_s
    print(
        f"\n큐 {len(transcript.cues)}개 · language={transcript.language!r} "
        f"· model={transcript.model}"
    )
    for cue in transcript.cues[:3]:
        print(f"  {cue.start_s:7.3f} ~ {cue.end_s:7.3f}  {cue.text[:40]}")


def test_전사에서_자막을_써_낸다(
    live_provider: OpenAICompatibleSttProvider, live_audio: Path, tmp_path: Path
) -> None:
    """전사 → `IngestResult` → 자막 파일까지 실제로 왕복한다 (R3).

    `load_media`가 채우는 `subs`·`format`·`event_index` 셋 중 하나라도 비면
    여기서 죽는다 — 셋을 각각 재는 단위 테스트가 있지만, 실제 백엔드가 낸
    큐로 왕복이 성립하는지는 이 자리에서만 확인된다.
    """
    result = load_media(live_audio, live_provider)
    # **공허한 통과를 먼저 막는다.** 백엔드가 0큐를 내면 아래 `all(())`은 `True`이고
    # `0 == 0`이라 이 테스트가 전부 초록으로 통과한다 - 아무것도 재지 않은 채로.
    assert result.segments, "세그먼트가 0개다 - 백엔드가 큐를 하나도 내지 않았다"
    assert all(segment.source_from_stt for segment in result.segments)

    out = tmp_path / "live.srt"
    write_subtitle(result, result.segments, out)
    body = out.read_text(encoding="utf-8")
    assert body.count("-->") == len(result.segments)
    print(f"\n세그먼트 {len(result.segments)}개 · {out.stat().st_size} bytes")
