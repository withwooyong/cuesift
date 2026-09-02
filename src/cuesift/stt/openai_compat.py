"""OpenAI 호환 `/v1/audio/transcriptions` 어댑터 (요구사항정의서 FR-1.2 · 설계 D1·D2·D4).

**`translate/openai_compat.py`의 비공개 헬퍼 셋을 가져다 쓴다** (계획 P1).
`_`로 시작하는 이름을 모듈 밖에서 부르는 것은 규약 위반에 가깝지만, 대안 둘이
더 나쁘다 - 공용 모듈로 추출하면 `translate`를 수정해 기존 번역 테스트가
회귀 위험에 들어가고, 복제하면 상태 코드 분류가 두 벌이 되어 한쪽만 고쳐질 때
**조용히 갈라진다**. 그 의존을 `tests/test_stt_openai_compat.py`의
`test_translate의_비공개_헬퍼에_의존하는_사실을_고정한다`가 감시한다.

**예외 계층은 새로 세우지 않는다** (D2). 분류 축("호출자가 틀렸나 데이터가
틀렸나")이 같은데 따로 세우면 CLI가 `except`를 두 벌 갖고, 빠뜨린 쪽은
재시도도 폴백도 없이 스택 밖으로 샌다.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from cuesift.stt.provider import Transcript, TranscriptCue
from cuesift.translate.openai_compat import (
    _raise_for_status,
    _require_ascii_api_key,
    _require_http_url,
)
from cuesift.translate.provider import FatalProviderError, RetryableProviderError

DEFAULT_TIMEOUT_S = 300.0
"""번역보다 길다. 오디오 업로드와 전사는 초 단위가 아니라 분 단위다 -
`translate`의 60초를 그대로 쓰면 30분짜리 강연이 **정상 응답 전에** 타임아웃으로
분류되고, 그것은 재시도 대상이라 같은 실패를 `max_retries+1`회 반복한다."""

_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
"""응답 본문을 메모리에 올릴 **바이트 상한**. 넘으면 `FatalProviderError`다.

**이 상한이 없으면 원격 응답 한 번이 프로세스를 죽인다**(보안 리뷰 실측):
`response.json()`은 본문 N바이트에 대해 peak 3.00N을 쓴다 - 64MB 본문에서
peak 201.3MB가 실측됐다. 악의적·오작동 엔드포인트가 1GB를 흘리면 약 3GB에서
`MemoryError`가 나는데 **`MemoryError`는 `transcribe()`의 어떤 `except`에도
없다.** 미처리 트레이스백은 Typer에서 **종료 코드 1**이 되고 이 저장소에서 1은
"규격 위반 발견"이라, 백엔드 결함이 자막 결함으로 오보된다 - `RecursionError`·
`OverflowError`를 잡은 것과 같은 부류의 넷째다.

**너무 작게 잡으면 정상 응답이 죽는다.** `verbose_json`은 큐마다 텍스트와
타임코드를 실으므로 몇 시간짜리 오디오도 수 MB에 그친다(관측). 32MB는 그
정상 범위의 수십 배다.

**peak는 32MB가 아니고, 배수는 하나가 아니다.** 실측(2026-09-02,
`tracemalloc`, 전체 `transcribe` 경로, 페이로드는 **측정창 밖에서 준비**,
모양마다 새 프로세스):

| 응답 모양 | 본문 | 큐 | peak | 배수 |
| --- | --- | --- | --- | --- |
| 긴 텍스트 · 큐 1.4k | 31.31MB | 1.4k | 73.5MB | 2.35배 |
| 실사 모양 · 본문 16MB | 16.38MB | 84.7k | 61.8MB | 3.77배 |
| 3시간 강연 프로파일 | 2.07MB | 10.8k | 7.9MB | 3.84배 |
| 짧은 큐 132k | 16.15MB | 132k | 77.9MB | 4.82배 |
| **상한 직전** | 30.23MB | 245k | **144.8MB** | 4.79배 |
| 상한 초과 경로 | (상한 기준) | - | 68.2MB | 2.13배 |

**배수를 정하는 것은 본문 크기가 아니라 큐 개수다.** 같은 16MB라도 큐가
1.4k면 2.35배, 132k면 4.82배다 - `TranscriptCue` 객체 하나하나가 본문에는
없던 비용이기 때문이다. 그래서 **하나의 배수를 박아 두면 안 된다**: 1차
수정이 큐 10개짜리 비현실적 페이로드에서 나온 3.37배를 적었고, 그것은
실사 모양에서 재현되지 않았다.

**상한을 가득 채우면 peak는 145MB 부근이다**(위 표). 100MB를 넘는다는 뜻이고,
그것이 이 상수를 32MB로 잡은 대가다. **중요한 것은 작다는 것이 아니라
유계라는 것이다** - 상한이 없으면 1GB 본문이 5GB 가까이 가고, 그때의
`MemoryError`는 어떤 `except`에도 걸리지 않는다."""

_RESPONSE_READ_BUDGET_S = 600.0
"""본문 **수신 단계**에 허용하는 총 시간(초).

`DEFAULT_TIMEOUT_S`는 httpx에서 **연산 하나의 상한**이지 총 시간이 아니다
(실측 2026-09-01, httpx 0.28.1: `timeout=0.5`인 클라이언트가 0.2초마다
1바이트를 흘리는 서버에서 3.08초를 예외 없이 살아남았다). 이 상한이 없으면
`timeout`마다 1바이트씩 주는 서버에 대해 요청이 **무기한** 살아 있고,
배치 전사에서 파일 하나가 실행 전체를 멈춘다.

**`DEFAULT_TIMEOUT_S`보다 커야 한다.** 같거나 작으면 느린 백엔드의 정상
응답이 마감에 걸려 재시도 대상으로 분류되고, 같은 실패가 반복된다.
헤더가 오기까지의 대기는 이 예산 밖이다 - 그것은 httpx의 read 타임아웃이
이미 덮으므로, 여기서 재는 것은 **첫 바이트 이후의 흘림**뿐이다."""

_ERROR_KEYS_SHOWN = 10
"""`segments`가 없을 때 진단으로 보여 줄 응답 키의 **개수** 상한.

전부 실으면 백엔드가 낸 긴 본문이 오류 메시지를 통째로 삼켜 사람이 읽지 못한다.
**값이 아니라 키만 싣는 것**은 본문에 무엇이 들어올지 모르기 때문이다."""

_ERROR_KEY_CHARS = 40
"""키 **하나**의 길이 상한. 개수만 잘라서는 이 상수의 목적이 달성되지 않는다.

**개수 상한만 있으면 신뢰 경계 밖 입력이 그것을 그대로 우회한다** (리뷰 실측):
40만 자짜리 키 하나를 가진 응답이 40만 자 예외 메시지를 만들었다. 키가 10개
이하여서 개수 절단이 아예 발동하지 않았기 때문이다. 형제 `_raise_for_status`가
`response.text[:200]`으로 자르는 것과 같은 규약이라야 두 어댑터가 갈리지 않는다."""


class OpenAICompatibleSttProvider:
    """`SttProvider` 프로토콜의 구현. `/audio/transcriptions`를 친다 (D1)."""

    name = "openai-compatible-stt"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if client is not None and timeout is not None:
            # 함께 주면 timeout이 조용히 무시된다 - 주입한 클라이언트가 이미
            # 자기 것을 갖고 있다. 호출부는 설정했다고 믿는데 값은 다른 것이 쓰인다.
            raise ValueError("client를 주면 timeout은 그 클라이언트의 것이다. 함께 줄 수 없다")
        self._base_url = base_url.rstrip("/")
        _require_http_url(self._base_url)
        if httpx.URL(self._base_url).userinfo:
            # **userinfo가 있으면 `api_key`가 조용히 폐기된다**(실측):
            # httpx가 `user:pass@`로 `BasicAuth`를 만들어 우리가 넣은
            # `headers["Authorization"]`을 **덮는다**. 사용자는 키를 줬다고
            # 믿는데 서버는 다른 자격증명을 받고, 401이 나면 Fatal이라
            # "키가 틀렸다"로 오독한다. 거부하지 않으면 그 오독이 정상 경로다.
            #
            # **값을 메시지에 싣지 않는다** - 그것이 비밀이다
            # (`_require_ascii_api_key`가 키를 감추는 것과 같은 규약).
            raise ValueError(
                "base_url에 자격증명(user:pass@)을 넣을 수 없다. api_key로 준다. "
                "값은 표시하지 않는다"
            )
        _require_ascii_api_key(api_key)
        # 끝의 슬래시를 정리하지 않으면 `//audio/transcriptions`가 되고,
        # 경로를 정확히 매칭하는 게이트웨이가 404를 낸다 - 404는 Fatal이라
        # 실행 전체가 죽는데 원인은 슬래시 하나다.
        self._endpoint = f"{self._base_url}/audio/transcriptions"
        self._model = model
        # client 주입은 테스트가 MockTransport를 꽂는 통로다. 주입받은 것은
        # 우리 것이 아니므로 close()가 건드리지 않는다.
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=DEFAULT_TIMEOUT_S if timeout is None else timeout
        )

    def transcribe(self, audio: Path, *, language: str | None) -> Transcript:
        """한 번만 친다. **재시도하지 않는다** - 호출부가 한다 (P2)."""
        try:
            handle = audio.open("rb")
        except OSError as e:
            # 파일이 없거나 잠겨 있다. 재시도해도 같으므로 Fatal이다.
            # 잡지 않으면 `OSError`가 `ProviderError` 밖으로 새어 폴백을 우회한다.
            raise FatalProviderError(f"{audio}: 오디오를 읽을 수 없다 - {e}") from None

        # **핸들을 넘긴다. `read_bytes()`가 아니다** (P2 · 리뷰 실측).
        # `read_bytes()`면 P2가 `Path`를 고른 근거("`bytes`를 받으면 긴 오디오가
        # 전부 메모리에 올라온다")가 구현에서 그대로 무산된다 - 8MB 파일 1회
        # 전사에서 peak 16.1MB(파일의 2배)를 썼다. multipart 인코딩이 사본을
        # 하나 더 만들기 때문이다. D9로 오디오 분할을 넣지 않으므로 **큰 파일이
        # 그대로 들어오는 것이 정상 경로**이고, 100MB 강연이면 200MB가 된다.
        #
        # 핸들은 `transcribe()` 호출마다 새로 연다. 상위 계층이
        # `RetryableProviderError`를 받아 재시도하면 이 함수가 다시 불리므로
        # **읽기 포인터가 소진된 핸들이 재사용되는 일이 없다** - 프로바이더가
        # 핸들을 필드에 들고 있었다면 2회차 요청의 본문이 조용히 비었을 것이다.
        files = {"file": (audio.name, handle)}
        data: dict[str, str] = {
            "model": self._model,
            # **이 값이 D4의 전제 전부다.** 기본 `json`은 텍스트만 주고
            # 타임코드가 통째로 사라져 전 세그먼트가 `0ms~0ms`가 된다.
            "response_format": "verbose_json",
        }
        if language:
            # `language=""`를 보내면 400을 내는 서버가 있다. `is not None`으로
            # 검사하면 빈 문자열이 그대로 나가므로 진릿값으로 본다
            # (`translate`의 `api_key` 처리와 같은 판단).
            data["language"] = language

        headers: dict[str, str] = {}
        if self._api_key:
            # 로컬 STT는 키를 요구하지 않는다. `is not None`으로 검사하면 빈
            # 문자열에서 `Bearer `가 나가고 서버가 401을 내는데, 401은 Fatal이라
            # "키가 없다"가 "키가 틀렸다"로 둔갑한다 (`translate`와 같은 판단).
            #
            # **이 dict는 여기서만 산다.** 아래 어떤 예외 메시지에도 `headers`나
            # 요청 객체를 싣지 않는다 - 실으면 API 키가 로그와 실패 리포트에
            # 그대로 남는다(`_require_ascii_api_key`가 값을 감추는 이유와 같다).
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            # `with`가 성공·예외 어느 쪽으로 빠져나가도 핸들을 닫는다. 닫지
            # 않으면 배치 전사에서 파일 디스크립터가 쌓여 `OSError: Too many
            # open files`가 나는데, 그것은 `ProviderError` 밖이다.
            #
            # **`post()`가 아니라 `stream()`이다.** `post()`는 본문을 통째로
            # 실체화하므로 상한을 걸 자리가 없다 - 크기를 알았을 때는 이미
            # 메모리에 올라와 있다. `_MAX_RESPONSE_BYTES` 참고.
            # 업로드 쪽 스트리밍(P2)은 그대로다 - `files`에 넘기는 것이
            # 핸들이므로 `stream()`도 같은 방식으로 인코딩한다.
            with (
                handle,
                self._client.stream(
                    "POST", self._endpoint, data=data, files=files, headers=headers
                ) as streamed,
            ):
                response, overflowed = _read_capped(streamed)
        except httpx.TimeoutException as e:
            # TransportError의 자손이라 아래 절보다 **먼저** 와야 한다.
            # 순서가 뒤집히면 이 절이 죽은 코드가 되고 분류는 그대로라
            # 메시지를 보지 않는 테스트는 아무것도 눈치채지 못한다.
            raise RetryableProviderError(f"타임아웃: {e}") from None
        except httpx.TransportError as e:
            raise RetryableProviderError(f"연결 실패: {e}") from None
        except (httpx.HTTPError, httpx.InvalidURL, httpx.CookieConflict, httpx.StreamError) as e:
            # `TransportError`로 좁히면 `DecodingError`가 샌다 -
            # `translate/openai_compat.py`의 같은 절에 실측 기록이 있다.
            raise RetryableProviderError(f"응답 처리 실패: {e}") from None
        except OSError as e:
            # **전송 도중의 읽기 실패다.** 핸들을 넘기면서 새로 생긴 경로로,
            # `read_bytes()`일 때는 위쪽 절이 전부 잡았다(읽기가 post 전에
            # 끝났으므로). 네트워크 드라이브나 전사 중 삭제된 임시 파일에서
            # 실제로 도달한다. httpx 예외 중 `OSError` 자손은 하나도 없으므로
            # (실측) 이 절이 위 세 절의 분류를 가리지 않는다.
            raise FatalProviderError(f"{audio}: 오디오를 읽을 수 없다 - {e}") from None

        # **상태 코드를 상한 초과보다 먼저 본다.** 순서가 뒤집히면 본문이 큰
        # 503이 "본문이 너무 크다"(Fatal)로 둔갑해 재시도 분류가 통째로
        # 뒤집힌다. `_read_capped`가 오류 본문도 상한까지만 실어 주므로
        # `_raise_for_status`의 `response.text[:200]` 절단은 그대로 산다.
        _raise_for_status(response)
        if overflowed:
            raise FatalProviderError(
                f"응답 본문이 {_MAX_RESPONSE_BYTES}바이트를 넘는다. "
                "백엔드가 verbose_json이 아닌 것을 흘리고 있는지 확인한다"
            )
        return self._to_transcript(response)

    def _to_transcript(self, response: httpx.Response) -> Transcript:
        """`verbose_json` 본문을 `Transcript`로 바꾼다 (D4).

        **`segments`가 없거나 비면 성공이 아니다.** 조용히 통과시키면 전
        세그먼트가 `0ms~0ms`가 되어 CPS 검사가 통째로 무의미해진다 - 그것은
        "규격을 통과했다"로 보고되므로 **오류가 아니라 거짓 초록**이다.
        """
        try:
            body = response.json()
        except (ValueError, RecursionError) as e:
            # 게이트웨이가 HTML 오류 페이지를 200으로 주는 일이 있다.
            #
            # **`RecursionError`를 함께 잡는 것이 `ValueError`만큼 중요하다**
            # (리뷰 실측). `[[[...20만 겹...]]]`을 주면 `json` 스캐너가
            # `RecursionError`를 내는데 그것은 `ValueError`가 아니라
            # `ProviderError` 밖으로 샌다. Typer로 실행하면 미처리 트레이스백과
            # **종료 코드 1**이 되고, 이 저장소에서 1은 "규격 위반 발견"이다 -
            # **신뢰 경계 밖 입력이 종료 코드의 의미를 바꾼다.** Task 1이
            # `OverflowError`를 `ValueError`로 감싼 것과 정확히 같은 부류다.
            raise FatalProviderError(f"응답이 JSON이 아니다: {e}") from None
        if not isinstance(body, dict):
            raise FatalProviderError(f"응답이 객체가 아니다: {type(body).__name__}")

        raw = body.get("segments")
        if not isinstance(raw, list) or not raw:
            # 빈 배열도 여기서 막는다. `[]`는 "타임코드를 낼 수 없다"이지
            # "전사할 것이 없다"가 아니다 - 후자의 판정은 인제스트가
            # `IngestError("empty")`로 한다.
            raise FatalProviderError(
                "응답에 segments가 없다. 백엔드가 response_format=verbose_json을 "
                f"지원하지 않는 것으로 보인다 (받은 키: {_diagnostic_keys(body)})"
            )

        cues: list[TranscriptCue] = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise FatalProviderError(f"{i}번째 segment가 객체가 아니다")
            try:
                cues.append(
                    TranscriptCue(
                        start_s=item.get("start"),
                        end_s=item.get("end"),
                        text=_cue_text(item.get("text")),
                    )
                )
            except ValueError as e:
                # **`TranscriptCue`의 `ValueError`를 여기서 번역한다.**
                # 그대로 두면 `ProviderError` 밖이라 호출부의 폴백이 받지 못한다.
                # `10**400`처럼 `OverflowError`가 나던 경로도 `provider.py`가
                # `ValueError`로 바꿔 두어 이 한 절이 전부 덮는다.
                raise FatalProviderError(f"{i}번째 segment의 타임코드가 쓸 수 없다: {e}") from None

        language = body.get("language")
        return Transcript(
            # **`tuple`로 바꿔 넘긴다.** `Transcript.__post_init__`이 리스트를
            # 거부한다 - `frozen=True`가 얕아 밖에서 계속 바뀌기 때문이다.
            cues=tuple(cues),
            language=language if isinstance(language, str) else None,
            model=self._model,
        )

    def close(self) -> None:
        """직접 만든 클라이언트만 닫는다.

        주입받은 것을 닫으면 그것을 공유하는 다른 호출부가 다음 요청에서 죽는다.
        """
        if self._owns_client:
            self._client.close()


_STRIPPED_HEADERS = (b"content-encoding", b"content-length")
"""재조립 응답에 **옮기면 안 되는** 헤더.

`iter_bytes()`가 돌려주는 것은 **이미 압축이 풀린 본문**이다. 그것을
`Content-Encoding: gzip`과 함께 다시 실으면 httpx가 **한 번 더 풀려고 해서**
`DecodingError`가 난다(실측). 그러면 `_raise_for_status`에 **닿기도 전에**
터지므로 "상태 코드를 먼저 본다"는 보장이 통째로 무력화된다 - 실측으로
`401 + gzip`이 `RetryableProviderError`가 됐다. **Fatal이어야 할 인증 실패가
재시도 대상이 되어 같은 401을 `max_retries+1`회 반복한다.**
httpx는 요청에 `Accept-Encoding: gzip, deflate`를 기본으로 붙이므로
**압축 응답은 예외가 아니라 정상 경로다**(OpenAI·nginx·Cloudflare 뒤의 vLLM).
형제 모듈의 `test_정상_gzip_응답을_읽는다`가 같은 결함을 이미 한 번 잡았다.

`Content-Length`도 함께 거른다 - 우리가 실은 본문은 압축이 풀렸거나
상한에서 잘려 원래 길이와 다르다. 남기면 헤더가 본문에 대해 거짓말한다.

**나머지는 전부 옮긴다.** `_raise_for_status`가 `Retry-After`와 `Location`을
읽으므로, 빠뜨리면 429의 대기 시간이 조용히 "모름"이 되고 리다이렉트 진단이
사라진다."""


def _read_capped(response: httpx.Response) -> tuple[httpx.Response, bool]:
    """스트리밍 응답을 **상한까지만** 읽어 실체화된 응답으로 되돌린다.

    돌려주는 `bool`은 "상한을 넘어 잘렸다"이다. 여기서 바로 예외를 내지
    않는 것은 **상태 코드 검사가 먼저여야** 하기 때문이다 - 호출부 주석 참고.

    `httpx.Response`를 새로 만들어 돌려주는 이유는 `_raise_for_status`와
    `_to_transcript`가 둘 다 `response.text`/`.json()`을 쓰는데, 스트림을
    소비한 원본에서는 그것들이 `httpx.ResponseNotRead`를 내기 때문이다.

    **무엇을 옮기고 무엇을 거르는가**가 이 함수에서 가장 조용한 부분이다.
    본문은 `iter_bytes()`가 **압축을 푼 뒤**의 바이트이고, 헤더는
    `_STRIPPED_HEADERS`(`Content-Encoding`·`Content-Length`) 둘만 빼고 전부
    옮긴다. 그 둘을 빼지 않으면 압축 응답이 통째로 죽는다 - 상수 주석 참고.

    **상한은 `body`에 싣기 전에 본다.** 뒤에서 보면 큰 덩어리 하나의 초과분이
    그대로 실린다 - gzip 폭탄이면 64KB 청크가 66MB로 팽창하므로, 검사 위치가
    곧 메모리 상한이다.
    """
    deadline = time.monotonic() + _RESPONSE_READ_BUDGET_S
    body = bytearray()
    overflowed = False
    for chunk in response.iter_bytes():
        if time.monotonic() > deadline:
            # 재시도 가능이다. 서버가 느린 것과 죽은 것을 여기서 구분할 수
            # 없고, Fatal로 올리면 일시적 혼잡이 배치 전체를 죽인다.
            raise RetryableProviderError(f"응답 본문 수신이 {_RESPONSE_READ_BUDGET_S}초를 넘었다")
        room = _MAX_RESPONSE_BYTES - len(body)
        if len(chunk) > room:
            # **여유만큼만 싣고 끊는다.** 통째로 실은 뒤 길이를 재면
            # 상한이 사후 보고가 되고 메모리는 이미 청크 하나만큼 초과했다.
            body += chunk[:room]
            overflowed = True
            break
        body += chunk
    filtered = httpx.Headers(
        [(k, v) for k, v in response.headers.raw if k.lower() not in _STRIPPED_HEADERS]
    )
    return (
        httpx.Response(
            status_code=response.status_code,
            headers=filtered,
            content=bytes(body),
        ),
        overflowed,
    )


def _cue_text(raw: object) -> str:
    """세그먼트의 `text`를 원문 문자열로 바꾼다. **문자열이 아니면 버린다.**

    `str(raw)`로 강제 변환하면 **조용히 가짜 원문이 만들어진다** - D4가 막는
    것과 같은 부류이고, 여기는 트리아지 엔진이라 그 가짜를 사람이 읽고
    오염이 지표까지 간다. 실측으로 확인한 값들이다.

    | 응답의 `text` | `str()` 강제 변환 | 지금 |
    | --- | --- | --- |
    | `null` | `"None"` ← 가짜 원문 | `""` |
    | `123` | `"123"` | `""` |
    | `{"a": 1}` | `"{'a': 1}"` ← 가짜 원문 | `""` |
    | `""`·`0`·`False` | `""`·`"0"`·`"False"` | `""` |
    | `"0"` | `"0"` | `"0"` |

    **`or ""`로는 절반만 닫힌다.** 그것은 falsy만 거르므로 `123`과 `{"a": 1}`이
    통과한다 - 실제로 1차 수정이 그렇게 절반만 닫혔다.

    빈 문자열로 떨어뜨리는 것이 예외보다 나은 이유는, 큐 하나가 비는 것은
    인제스트가 표시 불가로 걸러 낼 수 있는 반면(전부 그렇게 되면
    `IngestError("empty")`) 예외는 전사 전체를 죽이기 때문이다.

    `strip()`은 **문자열일 때만** 건다. Whisper가 큐마다 선행 공백을 붙인다.

    **고립 서로게이트를 UTF-8로 표현 가능한 것으로 바꾼다**(실측).
    `{"text": "\\ud800 hello"}`는 `isinstance(str)`을 통과해 `Segment`까지
    가지만, `write_subtitle`이 파일로 쓸 때 `UnicodeEncodeError`가 난다 -
    그것은 `ValueError`라 `OSError`를 잡는 자리에 걸리지 않고, 라이브러리로
    직접 부르는 호출부(`tests/test_stt_live.py`가 그 형태다)에서는 예외
    계층 밖으로 그대로 샌다.

    **코덱이지 정규식이 아니라는 것이 채택 근거다.** `encode/decode`는
    UTF-8로 인코딩 가능한 코드포인트를 **한 글자도 바꾸지 않으므로**
    이 저장소의 언어 조건(ko→en/ja)에 영향이 없다. 한국어·일본어가 그대로
    통과하는 것을 테스트가 고정한다 - 여기서 문자 클래스나 `\\b` 같은
    경계 규칙을 쓰면 CJK가 통째로 깨진다(이 저장소의 폐기 전례).
    """
    if not isinstance(raw, str):
        return ""
    return raw.encode("utf-8", "replace").decode("utf-8").strip()


def _diagnostic_keys(body: dict) -> list[str]:
    """`segments`가 없을 때 진단에 실을 키 목록. **개수와 길이를 모두 자른다.**

    둘 중 하나만 자르면 신뢰 경계 밖 입력이 나머지 하나를 우회한다 - 리뷰
    실측에서 키 1개짜리 응답이 40만 자 예외 메시지를 만들었다.
    """
    return [str(k)[:_ERROR_KEY_CHARS] for k in sorted(body)[:_ERROR_KEYS_SHOWN]]
