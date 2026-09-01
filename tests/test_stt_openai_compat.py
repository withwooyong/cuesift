"""`stt/openai_compat.py`의 HTTP 왕복 (설계 D1·D2·D4).

`httpx.MockTransport`를 쓰는 것은 의존성을 늘리지 않기 위해서다
(`tests/test_translate_openai_compat.py`와 같은 방식).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from cuesift.stt.openai_compat import OpenAICompatibleSttProvider
from cuesift.translate.provider import FatalProviderError, RetryableProviderError

VERBOSE_BODY = {
    "text": "안녕하세요 반갑습니다",
    "language": "korean",
    "segments": [
        {"id": 0, "start": 0.0, "end": 1.2345, "text": "안녕하세요"},
        {"id": 1, "start": 1.2345, "end": 3.5, "text": " 반갑습니다"},
    ],
}


def _audio(tmp_path: Path) -> Path:
    p = tmp_path / "clip.mp3"
    p.write_bytes(b"ID3fake audio bytes")
    return p


def _provider(handler, **kwargs) -> OpenAICompatibleSttProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleSttProvider(
        base_url="http://localhost:8080/v1", model="whisper-1", client=client, **kwargs
    )


def test_verbose_json_응답을_큐로_바꾼다(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=VERBOSE_BODY)

    t = _provider(handler).transcribe(_audio(tmp_path), language="ko")
    assert len(t.cues) == 2
    assert t.cues[0].start_s == 0.0
    assert t.cues[0].end_s == 1.2345
    assert t.cues[0].text == "안녕하세요"
    # 앞뒤 공백은 벗긴다 - Whisper는 큐마다 선행 공백을 붙인다.
    assert t.cues[1].text == "반갑습니다"
    assert t.language == "korean"
    assert t.model == "whisper-1"


def test_엔드포인트와_필수_필드를_보낸다(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json=VERBOSE_BODY)

    _provider(handler).transcribe(_audio(tmp_path), language="ko")
    assert seen["url"] == "http://localhost:8080/v1/audio/transcriptions"
    body = bytes(seen["body"])  # type: ignore[arg-type]
    # **`verbose_json`이 없으면 D4의 전제가 무너진다.** 기본 `json`은
    # 텍스트만 주고 타임코드가 통째로 사라진다.
    assert b"verbose_json" in body
    assert b"whisper-1" in body
    assert b"clip.mp3" in body


def test_segments가_없으면_치명적_오류다(tmp_path: Path) -> None:
    # D4 - 조용히 통과시키면 전 세그먼트가 0ms~0ms가 된다.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "안녕하세요"})

    with pytest.raises(FatalProviderError, match="segments"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_segments가_빈_배열이어도_치명적_오류다(tmp_path: Path) -> None:
    # `[]`는 "타임코드를 낼 수 없다"이지 "전사할 것이 없다"가 아니다.
    # 빈 입력 판정은 인제스트가 `IngestError("empty")`로 한다.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "", "segments": []})

    with pytest.raises(FatalProviderError, match="segments"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_큐에_start가_없으면_치명적_오류다(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"segments": [{"end": 1.0, "text": "가"}]})

    with pytest.raises(FatalProviderError, match="타임코드"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_큐의_타임코드가_수가_아니면_치명적_오류다(tmp_path: Path) -> None:
    # `TranscriptCue`가 ValueError를 내는데 그것은 ProviderError 밖이다.
    # 여기서 번역하지 않으면 호출부의 폴백이 받지 못한다.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"segments": [{"start": "0", "end": 1.0, "text": "가"}]})

    with pytest.raises(FatalProviderError, match="타임코드"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_json이_아닌_응답은_치명적_오류다(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    with pytest.raises(FatalProviderError, match="JSON"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_401은_치명적_오류다(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(FatalProviderError, match="401"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_413은_치명적_오류다(tmp_path: Path) -> None:
    # D9 - 분할하지 않으므로 재시도해도 같다.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(413, text="payload too large")

    with pytest.raises(FatalProviderError, match="413"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_429는_재시도_가능_오류다(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    with pytest.raises(RetryableProviderError, match="429"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_503은_재시도_가능_오류다(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    with pytest.raises(RetryableProviderError, match="503"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_타임아웃은_재시도_가능_오류다(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(RetryableProviderError, match="타임아웃"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_연결_실패는_재시도_가능_오류다(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(RetryableProviderError, match="연결"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_없는_파일은_치명적_오류다(tmp_path: Path) -> None:
    # 재시도해도 파일이 생기지 않는다.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=VERBOSE_BODY)

    with pytest.raises(FatalProviderError, match="읽을 수 없다"):
        _provider(handler).transcribe(tmp_path / "없다.mp3", language="ko")


def test_스킴_없는_base_url을_생성_시점에_막는다() -> None:
    # 호출 시점의 `ValueError`는 ProviderError 밖이다.
    # `translate`의 `_require_http_url`을 그대로 쓴다 (P1).
    with pytest.raises(ValueError, match="http"):
        OpenAICompatibleSttProvider(base_url="localhost:8080/v1", model="m")


def test_client와_timeout을_함께_주면_거부한다() -> None:
    # 함께 주면 timeout이 조용히 무시된다 - translate 쪽과 같은 규약이다.
    with pytest.raises(ValueError, match="함께 줄 수 없다"):
        OpenAICompatibleSttProvider(
            base_url="http://h/v1", model="m", client=httpx.Client(), timeout=5.0
        )


def test_language를_보내고_None이면_생략한다(tmp_path: Path) -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json=VERBOSE_BODY)

    p = _provider(handler)
    p.transcribe(_audio(tmp_path), language="ko")
    p.transcribe(_audio(tmp_path), language=None)
    assert b'name="language"' in seen[0]
    # **`language: null`에 400을 내는 서버가 있어 None은 아예 뺀다**
    # (`translate`의 `max_tokens`와 같은 판단).
    assert b'name="language"' not in seen[1]


def test_주입한_클라이언트는_닫지_않는다(tmp_path: Path) -> None:
    # 소유하지 않은 자원은 정리하지 않는다 - 공유 클라이언트가 죽는다.
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=VERBOSE_BODY))
    )
    provider = OpenAICompatibleSttProvider(base_url="http://h/v1", model="m", client=client)
    provider.close()
    assert not client.is_closed


def test_translate의_비공개_헬퍼에_의존하는_사실을_고정한다() -> None:
    """P1의 대가를 테스트로 못 박는다.

    `translate/openai_compat.py`에서 이 세 이름을 지우거나 시그니처를 바꾸면
    **이 테스트가 먼저 빨개진다.** 없으면 `stt`가 import 시점에 죽는데,
    고친 사람은 자기가 무엇을 깼는지 모른 채 translate 테스트의 초록만 본다.
    """
    from cuesift.translate import openai_compat as tc

    for name in ("_require_http_url", "_require_ascii_api_key", "_raise_for_status"):
        assert hasattr(tc, name), f"{name}이 사라졌다 - stt/openai_compat.py가 이것에 의존한다"


def test_text가_null이면_빈_문자열이_된다(tmp_path: Path) -> None:
    """`str(None)`은 `"None"`이다 - D4와 같은 부류의 조용한 오류다.

    예외도 안 나고 개수도 타임코드도 정상이라 파이프라인이 초록으로 통과하는데,
    **가짜 원문 `"None"`이 검수 큐에 앉아 사람이 그것을 읽는다.**
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"segments": [{"start": 0.0, "end": 1.0, "text": None}]})

    t = _provider(handler).transcribe(_audio(tmp_path), language="ko")
    assert t.cues[0].text == ""


def test_api_key를_주면_Authorization_헤더를_붙인다(tmp_path: Path) -> None:
    # 이 작업 패키지가 보안 민감으로 분류된 근거가 이 한 줄이다.
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=VERBOSE_BODY)

    _provider(handler, api_key="sk-test").transcribe(_audio(tmp_path), language="ko")
    assert seen["auth"] == "Bearer sk-test"


def test_api_key가_없거나_비면_Authorization_헤더를_붙이지_않는다(tmp_path: Path) -> None:
    """붙는 쪽만 보면 "항상 붙인다"로 바꾸는 변이가 살아남는다.

    빈 문자열까지 보는 것은 `is not None` 검사를 막기 위해서다 - 그러면
    `Bearer `가 나가고 서버가 401을 내는데 401은 Fatal이라 **"키가 없다"가
    "키가 틀렸다"로 둔갑한다**(`translate` 181행의 실측).
    """
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json=VERBOSE_BODY)

    _provider(handler).transcribe(_audio(tmp_path), language="ko")
    _provider(handler, api_key="").transcribe(_audio(tmp_path), language="ko")
    assert seen == [None, None]
