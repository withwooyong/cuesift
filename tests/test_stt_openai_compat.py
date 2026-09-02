"""`stt/openai_compat.py`의 HTTP 왕복 (설계 D1·D2·D4).

`httpx.MockTransport`를 쓰는 것은 의존성을 늘리지 않기 위해서다
(`tests/test_translate_openai_compat.py`와 같은 방식).
"""

from __future__ import annotations

import gc
import gzip
import inspect
import json
import tracemalloc
import zlib
from pathlib import Path

import httpx
import pytest

from cuesift.stt import openai_compat as stt_mod
from cuesift.stt.openai_compat import _MAX_RESPONSE_BYTES, OpenAICompatibleSttProvider
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
    """`stream()`에 넘어간 `files`의 두 번째 원소를 붙잡아 둔다.

    I-4를 테스트하는 유일한 방법이다. **메모리로는 측정할 수 없다** -
    `MockTransport`가 본문을 통째로 실체화하므로 스트리밍의 이득이 목에서는
    나타나지 않는다. 그래서 "무엇이 넘어갔나"와 "닫혔나"를 본다.

    **`post`가 아니라 `stream`을 가로챈다.** 응답 크기 상한을 넣으면서
    어댑터가 `stream()`을 쓰게 됐다 - `post`를 계속 가로채면 이 훅이 죽은
    코드가 되고 `sent`가 `None`인 채로 단언이 통과할 뻔했다.
    """

    sent: object = None

    def stream(self, *args: object, **kwargs: object):  # type: ignore[override,no-untyped-def]
        files = kwargs["files"]
        self.sent = files["file"][1]  # type: ignore[index]
        return super().stream(*args, **kwargs)  # type: ignore[arg-type,misc]


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
        def stream(self, *args: object, **kwargs: object):  # type: ignore[override,no-untyped-def]
            raise OSError("device not ready")

    provider = OpenAICompatibleSttProvider(
        base_url="http://h/v1", model="m", client=_ReadFailsClient()
    )
    with pytest.raises(FatalProviderError, match="읽을 수 없다"):
        provider.transcribe(_audio(tmp_path), language="ko")


# --- 응답 크기·시간 상한 (최종 픽스 F1·F4) ---------------------------------


def _oversized_response(request: httpx.Request) -> httpx.Response:
    """상한을 **한 덩어리 넘기는** 본문을 흘린다.

    `content=`에 이터레이터를 주면 httpx가 즉시 실체화하지 않으므로, 목에서도
    "끊지 않으면 계속 들어온다"가 재현된다.
    """
    block = b"x" * (1024 * 1024)

    def chunks():
        for _ in range(_MAX_RESPONSE_BYTES // len(block) + 2):
            yield block

    return httpx.Response(200, headers={"Content-Type": "application/json"}, content=chunks())


def test_상한을_넘는_본문은_치명적_오류다(tmp_path: Path) -> None:
    """상한이 없으면 원격 응답 **한 번**이 프로세스를 죽인다.

    `response.json()`은 본문 N바이트에 peak 3.00N을 쓴다(보안 리뷰 실측
    64MB→201.3MB). 1GB를 흘리면 `MemoryError`인데 그것은 `transcribe()`의
    어떤 `except`에도 없어 `ProviderError` 밖으로 샌다 - Typer에서 종료 코드
    1이 되고, 이 저장소에서 1은 "규격 위반 발견"이다.
    """
    with pytest.raises(FatalProviderError, match="너무|넘는다"):
        _provider(_oversized_response).transcribe(_audio(tmp_path), language="ko")


def test_상한을_넘어도_본문_전체를_메모리에_올리지_않는다(tmp_path: Path) -> None:
    """**끊는 것**이 상한의 전부다. 다 읽고 나서 재는 것은 사후 보고다.

    **이 테스트가 재는 것은 스트림에서 꺼낸 양이지 메모리가 아니다.**
    초과를 알려면 그 청크를 꺼내 봐야 하므로 `cap + 청크 하나`를 허용한다.
    꺼낸 청크가 `body`에 **실리지** 않는 것은
    `test_상한을_넘긴_청크가_메모리에_실리지_않는다`가 본다.
    """
    received: list[int] = []
    block = b"x" * (1024 * 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        def chunks():
            # 상한의 4배를 흘린다. 끊지 않으면 전부 소비된다.
            for i in range(_MAX_RESPONSE_BYTES // len(block) * 4):
                received.append(i)
                yield block

        return httpx.Response(200, content=chunks())

    with pytest.raises(FatalProviderError):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")
    consumed = len(received) * len(block)
    assert consumed <= _MAX_RESPONSE_BYTES + len(block), f"{consumed}바이트나 읽었다"


def test_상한을_넘는_오류_본문은_상태_코드로_먼저_갈린다(tmp_path: Path) -> None:
    """순서가 뒤집히면 본문이 큰 503이 Fatal로 둔갑해 재시도 분류가 뒤집힌다."""

    def handler(request: httpx.Request) -> httpx.Response:
        block = b"x" * (1024 * 1024)

        def chunks():
            for _ in range(_MAX_RESPONSE_BYTES // len(block) + 2):
                yield block

        return httpx.Response(503, content=chunks())

    with pytest.raises(RetryableProviderError):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


def test_정상_크기_응답은_그대로_통과한다(tmp_path: Path) -> None:
    """상한이 정상 경로를 막으면 어댑터 전체가 죽는다 - 반대 방향의 게이트다."""
    t = _provider(lambda r: httpx.Response(200, json=VERBOSE_BODY)).transcribe(
        _audio(tmp_path), language="ko"
    )
    assert len(t.cues) == 2


def test_오류_본문_절단_규약은_상한_뒤에도_그대로다(tmp_path: Path) -> None:
    """`_raise_for_status`의 `response.text[:200]`이 살아 있어야 한다.

    스트리밍으로 바꾸면서 응답을 새로 만들어 넘기므로, 헤더와 본문이 함께
    옮겨지지 않으면 이 절단도 `Retry-After` 파싱도 조용히 죽는다.
    """
    body = "가" * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text=body, headers={"Retry-After": "7"})

    with pytest.raises(RetryableProviderError) as excinfo:
        _provider(handler).transcribe(_audio(tmp_path), language="ko")
    assert len(str(excinfo.value)) < 400, "본문 절단이 살아 있지 않다"
    assert excinfo.value.retry_after_s == 7.0, "헤더가 함께 옮겨지지 않았다"


def test_수신_시간_예산을_넘기면_재시도_가능_오류다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """httpx의 `timeout`은 **연산 간 간격**이지 총 시간이 아니다.

    실측(2026-09-01, httpx 0.28.1): `timeout=0.5`인 클라이언트가 0.2초마다
    1바이트를 흘리는 서버에서 3.08초를 예외 없이 살아남았다. 그런 서버에
    대해 요청은 무기한 살아 있고, 배치에서 파일 하나가 전체를 멈춘다.

    예산을 음수로 바꿔 첫 덩어리에서 마감을 넘게 만든다 - 실제 대기 없이
    같은 분기를 지난다.
    """
    monkeypatch.setattr(stt_mod, "_RESPONSE_READ_BUDGET_S", -1.0)
    with pytest.raises(RetryableProviderError, match="수신"):
        _provider(lambda r: httpx.Response(200, json=VERBOSE_BODY)).transcribe(
            _audio(tmp_path), language="ko"
        )


# --- base_url userinfo (최종 픽스 F3) --------------------------------------


def test_base_url에_자격증명이_있으면_생성_시점에_거부한다() -> None:
    """실측: httpx가 `user:pass@`로 `BasicAuth`를 만들어 우리 헤더를 **덮는다**.

    `api_key="KEYKEYKEY"`를 줘도 전송 헤더는
    `Authorization: Basic YWxpY2U6czNjcjN0`였다. 사용자는 키를 줬다고 믿는데
    서버는 다른 자격증명을 받고, 401은 Fatal이라 "키가 틀렸다"로 오독한다.
    """
    with pytest.raises(ValueError, match="자격증명"):
        OpenAICompatibleSttProvider(
            base_url="http://alice:s3cr3t@h/v1", model="m", api_key="KEYKEYKEY"
        )


def test_거부_메시지에_자격증명_값이_실리지_않는다() -> None:
    """메시지에 실으면 로그와 실패 리포트에 비밀이 그대로 남는다."""
    with pytest.raises(ValueError) as excinfo:
        OpenAICompatibleSttProvider(base_url="http://alice:s3cr3t@h/v1", model="m")
    msg = str(excinfo.value)
    assert "s3cr3t" not in msg
    assert "alice" not in msg


def test_자격증명이_없는_base_url은_그대로_통과한다() -> None:
    """반대 방향의 게이트 - 검사가 정상 URL을 막으면 어댑터가 못 쓰인다."""
    OpenAICompatibleSttProvider(base_url="http://h:8080/v1", model="m").close()


# --- 고립 서로게이트 (최종 픽스 F5) ----------------------------------------


def test_고립_서로게이트가_예외_계층_밖으로_새지_않는다(tmp_path: Path) -> None:
    """`\\ud800`은 `isinstance(str)`을 통과해 `write_subtitle`에서 터진다.

    그것은 `UnicodeEncodeError`(=`ValueError`)라 `OSError` 그물에 걸리지 않고,
    라이브러리로 직접 부르는 호출부에서는 예외 계층 밖으로 샌다.

    **본문을 원시 바이트로 준다.** `json=`으로 주면 httpx가 직렬화하는
    자리에서 먼저 터져(실측) 실제 경로를 재현하지 못한다 - 원격 백엔드는
    JSON 이스케이프로 보내고 `json.loads`가 서로게이트를 만들어 낸다.
    """
    raw = b'{"segments":[{"start":0.0,"end":1.0,"text":"\\ud800 hello"}]}'
    t = _provider(lambda r: httpx.Response(200, content=raw)).transcribe(
        _audio(tmp_path), language="ko"
    )
    # 인코딩 가능해야 한다는 것이 이 테스트의 전부다.
    t.cues[0].text.encode("utf-8")


def test_한국어와_일본어는_한_글자도_바뀌지_않는다(tmp_path: Path) -> None:
    """**채택 근거가 코덱이라는 것**이다 - 정규식이면 CJK가 통째로 깨진다.

    이 저장소에서 제안된 `\b` 단어 경계가 CJK를 전부 깨뜨려 폐기된 전례가
    있다. `encode/decode`는 UTF-8로 표현 가능한 코드포인트를 바꾸지 않는다.
    """
    ko = "안녕하세요 반갑습니다 — 「따옴표」 ①②③ 𝄞 🙂"
    ja = "こんにちは、世界。ｱｲｳ 漢字 ひらがな カタカナ 😀"
    body = {
        "segments": [
            {"start": 0.0, "end": 1.0, "text": ko},
            {"start": 1.0, "end": 2.0, "text": ja},
        ],
    }
    t = _provider(lambda r: httpx.Response(200, json=body)).transcribe(
        _audio(tmp_path), language="ko"
    )
    assert t.cues[0].text == ko
    assert t.cues[1].text == ja


# --- 검사받지 않던 방어 3줄 (최종 픽스 F2) ---------------------------------


def test_응답이_객체가_아니면_치명적_오류다(tmp_path: Path) -> None:
    """지우면 `body.get`이 `AttributeError` - 계층 밖으로 새어 종료 코드 1이 된다.

    이 저장소에서 1은 "규격 위반 발견"이라, 백엔드 결함이 자막 결함으로
    **오보**된다.
    """
    with pytest.raises(FatalProviderError, match="객체가 아니다"):
        _provider(lambda r: httpx.Response(200, json=[1, 2])).transcribe(
            _audio(tmp_path), language="ko"
        )


def test_segment_원소가_객체가_아니면_치명적_오류다(tmp_path: Path) -> None:
    """지우면 `item.get`이 `AttributeError`로 같은 결과가 된다."""
    with pytest.raises(FatalProviderError, match="객체가 아니다"):
        _provider(lambda r: httpx.Response(200, json={"segments": [1]})).transcribe(
            _audio(tmp_path), language="ko"
        )


def test_DecodingError는_재시도_가능_오류다(tmp_path: Path) -> None:
    """`TransportError`로 좁히면 이것이 샌다 - 실측으로 `RequestError`지만
    `TransportError`는 **아니다**. 그 절이 없으면 `httpx.DecodingError`가
    `ProviderError` 밖으로 나간다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.DecodingError("gzip이 깨졌다", request=request)

    with pytest.raises(RetryableProviderError, match="응답 처리 실패"):
        _provider(handler).transcribe(_audio(tmp_path), language="ko")


# --- 압축 응답 (재리뷰 N1) -------------------------------------------------


def test_정상_gzip_응답을_읽는다(tmp_path: Path) -> None:
    """**목이 광고한 능력을 한 번은 행사해야 한다.**

    이 파일의 46개 테스트가 한 번도 `Content-Encoding`을 붙이지 않아서,
    스트리밍 전환이 압축 응답을 통째로 죽인 것이 1693 passed를 그대로
    통과했다. httpx는 요청에 `Accept-Encoding: gzip, deflate`를 기본으로
    붙이므로 **압축 응답은 예외가 아니라 정상 경로다** - 진짜 서버(OpenAI,
    nginx·Cloudflare 뒤의 vLLM)는 대부분 gzip으로 돌려준다.

    형제 `tests/test_translate_openai_compat.py`의 같은 이름 테스트와 짝이다.
    거기서 한 번 잡힌 결함이 여기서 재발했다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = gzip.compress(json.dumps(VERBOSE_BODY).encode())
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=body)

    t = _provider(handler).transcribe(_audio(tmp_path), language="ko")
    assert len(t.cues) == 2
    assert t.cues[0].text == "안녕하세요"


def test_정상_deflate_응답을_읽는다(tmp_path: Path) -> None:
    """`Accept-Encoding`이 광고하는 둘째 코덱이다. gzip만 고치면 절반만 닫힌다."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = zlib.compress(json.dumps(VERBOSE_BODY).encode())
        return httpx.Response(200, headers={"content-encoding": "deflate"}, content=body)

    t = _provider(handler).transcribe(_audio(tmp_path), language="ko")
    assert len(t.cues) == 2


def test_gzip_401은_치명적_오류로_남는다(tmp_path: Path) -> None:
    """**이 결함의 진짜 피해가 여기다.**

    재조립 응답에 `Content-Encoding`을 그대로 옮기면 httpx가 이미 풀린
    본문을 다시 풀려다 `DecodingError`를 내고, 그것은 `_raise_for_status`에
    **닿기 전에** 터진다. 실측으로 401이 `RetryableProviderError`가 됐다 -
    인증 실패가 재시도 대상이 되어 같은 401을 `max_retries+1`회 반복한다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            headers={"content-encoding": "gzip"},
            content=gzip.compress(b'{"error":"bad key"}'),
        )

    with pytest.raises(FatalProviderError) as excinfo:
        _provider(handler).transcribe(_audio(tmp_path), language="ko")
    msg = str(excinfo.value)
    assert msg.startswith("401"), msg
    # 본문이 읽히는 것까지 본다 - 헤더만 걷어내고 본문을 못 실으면
    # 사용자는 "401"만 보고 원인을 모른다.
    assert "bad key" in msg


def test_gzip_429의_Retry_After가_보존된다(tmp_path: Path) -> None:
    """헤더를 **둘만** 거른다는 것이 이 테스트다.

    전부 거르면 429의 대기 시간이 조용히 "모름"이 되고 리다이렉트 진단이
    사라진다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"content-encoding": "gzip", "Retry-After": "7"},
            content=gzip.compress(b"slow down"),
        )

    with pytest.raises(RetryableProviderError) as excinfo:
        _provider(handler).transcribe(_audio(tmp_path), language="ko")
    assert excinfo.value.retry_after_s == 7.0


# --- 큰 덩어리 하나의 초과분 (재리뷰 N3) -----------------------------------


def test_팽창한_큰_청크에서도_상한이_지켜진다(monkeypatch: pytest.MonkeyPatch) -> None:
    """상한 검사가 누적 **뒤에** 있으면 청크 하나만큼 초과분이 그대로 실린다.

    gzip 폭탄이면 64KB 청크가 최대 66MB로 팽창하므로, 검사 **위치**가 곧
    메모리 상한이다. 상한을 작게 바꿔 같은 산술을 실제 32MB 할당 없이 본다 -
    비율이 그대로이므로 검사 위치가 뒤로 돌아가면 이 단언이 깨진다.
    """
    cap = 1024
    monkeypatch.setattr(stt_mod, "_MAX_RESPONSE_BYTES", cap)

    def chunks():
        yield b"y" * (cap * 64)  # 팽창한 한 덩어리
        raise AssertionError("상한을 넘긴 뒤에도 스트림을 계속 읽었다")

    response, overflowed = stt_mod._read_capped(httpx.Response(200, content=chunks()))
    assert overflowed
    assert len(response.content) == cap, f"{len(response.content)}바이트를 실었다"


def test_재조립_응답이_본문_길이를_거짓말하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Content-Length`도 함께 걸러야 하는 이유다.

    우리가 싣는 본문은 압축이 풀렸거나 상한에서 **잘려** 원래 길이와 다르다.
    원래 헤더를 그대로 옮기면 헤더가 본문에 대해 거짓말을 하고, 그 응답을
    읽는 다음 사람은 **본문이 잘린 것을 헤더로는 알 수 없다.**

    **`Content-Encoding` 하나만 거르면 이 단언이 깨진다** - 변이로 확인했다.
    나머지 헤더가 그대로 오는 것(`X-Trace`)을 함께 본다: 둘만 거른다는 것이
    이 함수의 계약이고, 전부 거르면 429의 `Retry-After`가 사라진다.
    """
    monkeypatch.setattr(stt_mod, "_MAX_RESPONSE_BYTES", 1024)
    payload = b"z" * 4096
    source = httpx.Response(
        200,
        headers={"Content-Length": str(len(payload)), "X-Trace": "keep"},
        content=iter([payload]),
    )
    out, overflowed = stt_mod._read_capped(source)
    assert overflowed
    declared = out.headers.get("Content-Length")
    assert declared is not None and int(declared) == len(out.content), (
        f"헤더가 {declared}바이트라는데 본문은 {len(out.content)}바이트다"
    )
    assert out.headers["X-Trace"] == "keep", "거르지 않아야 할 헤더까지 사라졌다"


def test_상한을_넘긴_청크가_메모리에_실리지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """**`len(content)`만 보는 단언으로는 이 회귀를 못 잡는다.**

    N3 이전 원형(누적한 **뒤에** 길이를 재고, 반환할 때 `bytes(body[:상한])`으로
    자르기)은 결과 길이가 지금과 **똑같다** - 실측으로 둘 다 65536바이트다.
    다른 것은 그 사이에 쓴 메모리뿐이므로, 그것을 재지 않으면 게이트가 아니다.
    gzip 폭탄이면 64KB 청크가 66MB로 팽창하니 이 차이가 곧 위협의 크기다.

    실측(2026-09-02, `tracemalloc`, 32MB 청크 하나 · 상한 64KB):

    | 코드 | peak |
    | --- | --- |
    | 지금 (싣기 전에 검사) | **0.14MB** |
    | N3 이전 원형 (싣고 나서 검사) | **32.14MB** |

    임계값 8MB는 지금 코드의 **57배**이고 원형의 **1/4**이다. 여유를 이만큼
    두는 이유는 빡빡한 임계값이 CI에서 간헐 실패하고 **무시되는 게이트는 없는
    게이트와 같기** 때문이다 - 인터프리터·플랫폼 차이로 수 MB가 흔들려도
    양쪽 판정은 바뀌지 않는다.

    **청크는 측정창 밖에서 만든다.** 안에서 만들면 32MB 할당이 그대로 peak에
    실려 두 코드가 구분되지 않는다.
    """
    cap = 64 * 1024
    chunk_bytes = 32 * 1024 * 1024
    monkeypatch.setattr(stt_mod, "_MAX_RESPONSE_BYTES", cap)
    blob = b"y" * chunk_bytes

    def chunks():
        yield blob

    source = httpx.Response(200, content=chunks())
    gc.collect()
    tracemalloc.start()
    try:
        response, overflowed = stt_mod._read_capped(source)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert overflowed
    # 길이는 원형도 통과한다 - 아래 한 줄이 이 테스트의 전부다.
    assert len(response.content) == cap
    assert peak < chunk_bytes // 4, f"peak {peak / 1048576:.1f}MB - 청크가 통째로 실렸다"
