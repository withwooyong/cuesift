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

_ERROR_KEYS_SHOWN = 10
"""`segments`가 없을 때 진단으로 보여 줄 응답 키의 개수 상한.

전부 실으면 백엔드가 낸 긴 본문이 오류 메시지를 통째로 삼켜 사람이 읽지 못한다.
**값이 아니라 키만 싣는 것**은 본문에 무엇이 들어올지 모르기 때문이다."""


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
            payload = audio.read_bytes()
        except OSError as e:
            # 파일이 없거나 잠겨 있다. 재시도해도 같으므로 Fatal이다.
            # 잡지 않으면 `OSError`가 `ProviderError` 밖으로 새어 폴백을 우회한다.
            raise FatalProviderError(f"{audio}: 오디오를 읽을 수 없다 - {e}") from None

        files = {"file": (audio.name, payload)}
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
            response = self._client.post(self._endpoint, data=data, files=files, headers=headers)
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

        _raise_for_status(response)
        return self._to_transcript(response)

    def _to_transcript(self, response: httpx.Response) -> Transcript:
        """`verbose_json` 본문을 `Transcript`로 바꾼다 (D4).

        **`segments`가 없거나 비면 성공이 아니다.** 조용히 통과시키면 전
        세그먼트가 `0ms~0ms`가 되어 CPS 검사가 통째로 무의미해진다 - 그것은
        "규격을 통과했다"로 보고되므로 **오류가 아니라 거짓 초록**이다.
        """
        try:
            body = response.json()
        except ValueError as e:
            # 게이트웨이가 HTML 오류 페이지를 200으로 주는 일이 있다.
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
                f"지원하지 않는 것으로 보인다 (받은 키: {sorted(body)[:_ERROR_KEYS_SHOWN]})"
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
                        text=str(item.get("text", "")).strip(),
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
