"""`stt/openai_compat.py`의 HTTP 왕복 (설계 D1·D2·D4).

`httpx.MockTransport`를 쓰는 것은 의존성을 늘리지 않기 위해서다
(`tests/test_translate_openai_compat.py`와 같은 방식).
"""

from __future__ import annotations

import inspect
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


def test_주입한_클라이언트는_닫지_않는다() -> None:
    # 소유하지 않은 자원은 정리하지 않는다 - 공유 클라이언트가 죽는다.
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=VERBOSE_BODY))
    )
    provider = OpenAICompatibleSttProvider(base_url="http://h/v1", model="m", client=client)
    provider.close()
    assert not client.is_closed


def test_translate의_비공개_헬퍼_시그니처를_고정한다() -> None:
    """P1의 대가를 테스트로 못 박는다. **`hasattr`로는 못 박히지 않는다.**

    이전 판은 `hasattr`만 보면서 "이름을 지우거나 시그니처를 바꾸면 이 테스트가
    먼저 빨개진다"고 주장했는데 둘 다 사실이 아니었다(리뷰 지적).

    | 주장 | 사실 |
    | --- | --- |
    | 시그니처 변경을 잡는다 | `hasattr`은 시그니처를 보지 않는다 |
    | **먼저** 빨개진다 | 이름이 사라지면 최상단 import가 먼저 죽어 전 건이 수집 오류다 |

    즉 `hasattr` 단언이 추가로 잡는 것은 **0이었다.** 이름 소멸은 수집 오류가
    이미 알려 주므로, 이 테스트가 실제로 지켜야 하는 것은 **이름은 남았는데
    인자가 바뀐 경우** 하나뿐이다 - 그때는 `stt`가 import에 성공한 뒤
    호출 시점에 `TypeError`로 죽고, 고친 사람은 translate 테스트의 초록만 본다.
    """
    from cuesift.translate import openai_compat as tc

    expected = {
        "_require_http_url": ["base_url"],
        "_require_ascii_api_key": ["api_key"],
        "_raise_for_status": ["response"],
    }
    for name, params in expected.items():
        fn = getattr(tc, name, None)
        assert fn is not None, f"{name}이 사라졌다 - stt/openai_compat.py가 이것에 의존한다"
        got = list(inspect.signature(fn).parameters)
        assert got == params, f"{name}의 인자가 바뀌었다: {params} -> {got}"


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


class _RecordingClient(httpx.Client):
    """`post()`에 넘어간 `files`의 두 번째 원소를 붙잡아 둔다.

    I-4를 테스트하는 유일한 방법이다. **메모리로는 측정할 수 없다** -
    `MockTransport`가 본문을 통째로 실체화하므로 스트리밍의 이득이 목에서는
    나타나지 않는다. 그래서 "무엇이 넘어갔나"와 "닫혔나"를 본다.
    """

    sent: object = None

    def post(self, *args: object, **kwargs: object) -> httpx.Response:
        files = kwargs["files"]
        self.sent = files["file"][1]  # type: ignore[index]
        return super().post(*args, **kwargs)  # type: ignore[arg-type,misc]


def _recording_provider(handler) -> OpenAICompatibleSttProvider:
    client = _RecordingClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleSttProvider(base_url="http://h/v1", model="m", client=client)


def test_바이트가_아니라_파일_객체를_넘긴다(tmp_path: Path) -> None:
    """I-4 - `read_bytes()`면 P2가 `Path`를 고른 근거가 무산된다.

    실측으로 8MB 파일에서 peak 16.1MB(파일의 2배)를 썼다. D9로 분할이 없어
    **큰 파일이 그대로 들어오는 것이 정상 경로**다.
    """
    p = _recording_provider(lambda r: httpx.Response(200, json=VERBOSE_BODY))
    p.transcribe(_audio(tmp_path), language="ko")
    sent = p._client.sent  # type: ignore[attr-defined]
    assert hasattr(sent, "read"), f"파일 객체가 아니라 {type(sent).__name__}이 넘어갔다"
    assert not isinstance(sent, bytes)


def test_전송_뒤_파일_핸들을_닫는다(tmp_path: Path) -> None:
    p = _recording_provider(lambda r: httpx.Response(200, json=VERBOSE_BODY))
    p.transcribe(_audio(tmp_path), language="ko")
    assert p._client.sent.closed  # type: ignore[attr-defined]


def test_예외_경로에서도_파일_핸들을_닫는다(tmp_path: Path) -> None:
    """닫지 않으면 배치 전사에서 fd가 쌓여 `OSError: Too many open files`가 난다.

    그것은 `ProviderError` 밖이라 폴백이 받지 못한다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    p = _recording_provider(handler)
    with pytest.raises(RetryableProviderError):
        p.transcribe(_audio(tmp_path), language="ko")
    assert p._client.sent.closed  # type: ignore[attr-defined]


def test_재시도로_다시_불려도_본문이_비지_않는다(tmp_path: Path) -> None:
    """핸들을 필드에 들고 있으면 2회차 본문이 **조용히** 빈다.

    상위 계층이 `RetryableProviderError`로 재시도하면 `transcribe`가 다시
    불린다. 읽기 포인터가 소진된 핸들을 재사용하면 예외 없이 빈 오디오가
    올라가고, 백엔드는 그것을 "전사할 것이 없다"로 답한다.
    """
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json=VERBOSE_BODY)

    audio = _audio(tmp_path)
    p = _provider(handler)
    p.transcribe(audio, language="ko")
    p.transcribe(audio, language="ko")
    assert len(seen) == 2
    assert all(b"ID3fake audio bytes" in body for body in seen)


def test_과도하게_중첩된_JSON은_치명적_오류다(tmp_path: Path) -> None:
    """I-1 - `RecursionError`는 `ValueError`가 아니라 `ProviderError` 밖으로 샌다.

    Typer로 실행하면 미처리 트레이스백과 **종료 코드 1**이 되는데, 이 저장소에서
    1은 "규격 위반 발견"이다 - **신뢰 경계 밖 입력이 종료 코드의 의미를 바꾼다.**
    """
    deep = "[" * 200_000 + "]" * 200_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=deep)

    with pytest.raises(FatalProviderError, match="JSON"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_긴_키는_오류_메시지에서_잘린다(tmp_path: Path) -> None:
    """I-2 - 개수만 자르면 키 1개짜리 응답이 40만 자 메시지를 만든다(실측).

    형제 `_raise_for_status`가 `response.text[:200]`으로 자르는 것과 같은 규약이다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"x" * 400_000: 1})

    with pytest.raises(FatalProviderError, match="segments") as excinfo:
        _provider(handler).transcribe(_audio(tmp_path), language="ko")
    assert len(str(excinfo.value)) < 500, "긴 키가 오류 메시지를 통째로 삼켰다"


def test_직접_만든_클라이언트는_닫는다() -> None:
    """`translate`에는 있고 여기에는 없던 방향이다(리뷰 실측).

    이것이 없으면 `def close(self): pass` 변이가 **전 스위트에서 생존**하고
    소켓 누수를 게이트가 못 잡는다.
    """
    provider = OpenAICompatibleSttProvider(base_url="http://h/v1", model="m")
    client = provider._client
    provider.close()
    assert client.is_closed


def test_text가_문자열이_아니면_빈_문자열이_된다(tmp_path: Path) -> None:
    """M-2 - `or ""`는 falsy만 걸러 dict/list가 통과했다.

    `str({"a": 1})`이 `"{'a': 1}"`가 되어 **가짜 원문이 검수 큐에 앉는다** -
    방금 닫은 `"None"` 오염과 정확히 같은 부류다.
    """
    for value in ({"a": 1}, [1, 2], 123, True):

        def handler(request: httpx.Request, v: object = value) -> httpx.Response:
            return httpx.Response(200, json={"segments": [{"start": 0.0, "end": 1.0, "text": v}]})

        t = _provider(handler).transcribe(_audio(tmp_path), language="ko")
        assert t.cues[0].text == "", f"{value!r}가 원문으로 살아남았다: {t.cues[0].text!r}"


def test_repr에_api_key가_실리지_않는다() -> None:
    """방어가 "기본 `repr`이 필드를 안 찍는다"에 기대고 있는데 그것은 고정돼 있지 않다.

    `@dataclass` 부착이나 `__repr__` 추가 **한 번이면 조용히 깨진다** - 그때
    키가 로그와 실패 리포트에 그대로 남는다.
    """
    secret = "sk-live-DO-NOT-LOG"  # noqa: S105 - 테스트용 가짜 값
    provider = OpenAICompatibleSttProvider(base_url="http://h/v1", model="m", api_key=secret)
    try:
        assert secret not in repr(provider)
        assert secret not in str(provider)
    finally:
        provider.close()


def test_키가_많으면_오류_메시지에_일부만_싣는다(tmp_path: Path) -> None:
    """개수 절단(`_ERROR_KEYS_SHOWN`)을 지키는 테스트가 **하나도 없었다**(변이 N3 생존).

    길이 절단만 테스트하면 개수 절단은 검사받지 않는 게이트가 된다 - 짧은 키
    수천 개짜리 응답이 같은 자리를 그대로 우회한다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={f"k{i:02d}": 1 for i in range(50)})

    with pytest.raises(FatalProviderError, match="segments") as excinfo:
        _provider(handler).transcribe(_audio(tmp_path), language="ko")
    msg = str(excinfo.value)
    assert "k00" in msg
    assert "k49" not in msg, "키 개수 절단이 걸리지 않았다"


def test_전송_도중_읽기_실패는_치명적_오류다(tmp_path: Path) -> None:
    """핸들을 넘기면서 **새로 생긴** 예외 경로다 (I-4의 대가).

    `read_bytes()`일 때는 읽기가 post 전에 끝나 위쪽 `except OSError`가 전부
    잡았다. 이제는 전송 도중에도 날 수 있고, 잡지 않으면 `OSError`가
    `ProviderError` 밖으로 새어 폴백을 우회한다. **내가 새로 넣은 방어이므로
    테스트가 없으면 검사받지 않는 게이트가 된다**(변이 N3의 교훈).
    """

    class _ReadFailsClient(httpx.Client):
        def post(self, *args: object, **kwargs: object) -> httpx.Response:
            raise OSError("device not ready")

    provider = OpenAICompatibleSttProvider(
        base_url="http://h/v1", model="m", client=_ReadFailsClient()
    )
    with pytest.raises(FatalProviderError, match="읽을 수 없다"):
        provider.transcribe(_audio(tmp_path), language="ko")
