"""STT 호출의 재시도 루프 (FR-1.2 · 설계 §4.1·§6).

**`SttProvider` 프로토콜의 구현이 아니라 그 호출부다.** 계약 3번("재시도하지
않는다. 호출부가 한다")은 그대로 남고, 이 모듈이 그 호출부를 라이브러리에
제공한다. `cli.py`에 두면 파이썬 호출자는 재시도를 못 얻는다 - 어댑터가
`Retry-After`까지 실어 재시도 가능이라고 말해도 **받는 코드가 다시 0건이 된다.**

**`cuesift.stt.__init__`에서 이 모듈을 export하면 안 된다** (실측). 아래
`load_media` 임포트가 `cuesift.ingest.loader` → `cuesift.stt.provider` →
`cuesift.stt.__init__` → 여기 → `cuesift.ingest.loader`(초기화 중)로 돌아
`ImportError: cannot import name 'load_media' from partially initialized
module`이 난다. export하지 않으면 두 임포트 순서 모두 정상이다 -
`tests/test_stt_retry.py::test_stt_패키지가_이_모듈을_export하지_않는다`가
그 제약을 건다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from cuesift.ingest.loader import IngestResult, load_media
from cuesift.retry import backoff_delay
from cuesift.stt.provider import SttProvider
from cuesift.translate.provider import FatalProviderError, RetryableProviderError

STT_MAX_RETRIES = 3
"""재시도 횟수라 총 호출은 4회다 (`translate`의 `max_retries`와 같은 뜻).

**모듈 상수이고 CLI에 노출하지 않는다**(설계 D3). `translate`의 LLM 재시도가
`--max-retries`를 노출하지 않는데 STT만 바꿀 수 있으면, 같은 성격의 값에
통로가 하나만 열린 비대칭이 된다 - 사용자는 왜 한쪽만 되는지 알 방법이 없다."""


def transcribe_with_retry(
    provider: SttProvider,
    media: Path,
    *,
    language: str,
    on_retry: Callable[[int, float, RetryableProviderError], None] | None = None,
    max_retries: int = STT_MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> IngestResult:
    """전사하고 재시도 가능한 실패만 다시 건다.

    **반환이 `Transcript`가 아니라 `IngestResult`인 것은 재시도의 단위가
    전사 한 번 전체이기 때문이다.** `load_media`는 프로바이더 호출과 세그먼트
    합성을 함께 하는데, 합성 실패(`IngestError` - 큐 0개·파일 없음)는 다시
    걸어도 같으므로 재시도 대상이 아니다. 그대로 전파된다.

    `FatalProviderError`를 **즉시 전파한다.** 401과 `verbose_json` 미지원은
    다시 걸어도 같은 답이 온다. **두 예외를 형제로 두는 계약이 여기에도
    걸린다** - `FatalProviderError`를 `RetryableProviderError`의 하위로
    옮기면 이 루프가 인증 실패를 네 번 재시도하고, 사용자는 틀린 키로 네 번을
    기다린다. `translate/engine.py::_call_with_retry`가 같은 사고를 기록하고
    있고, 상속 관계를 바꾸면
    `tests/test_translate_provider.py::test_재시도_가능_실패는_서로_구분된다`가
    함께 죽는다.

    **마지막 시도 뒤에는 자지 않는다** - 호출 N+1회에 대기는 N회다. 거기서
    자면 아무도 기다릴 이유가 없는 시간을 CLI가 쓴다.

    `on_retry`는 **다시 걸기 직전**에 불린다. 라이브러리가 문구를 알지 않게
    하는 통로다 - `ProgressUpdate`가 단계 이름을 싣지 않는 것(FR-8.5 설계 D2)과
    같은 이유다. 인자는 `(방금 실패한 시도의 0-based 번호, 잘 초, 그 예외)`다.

    `sleep`은 테스트가 실제로 기다리지 않게 하려고 주입 가능하다.
    """
    if max_retries < 0:
        raise ValueError(f"max_retries({max_retries})는 0 이상이어야 한다")

    last: RetryableProviderError | None = None
    for attempt in range(max_retries + 1):
        try:
            return load_media(media, provider, source_lang=language)
        except FatalProviderError:
            # 오늘은 아무것도 바꾸지 않는다 - 두 예외가 형제라 아래 절이
            # 애초에 Fatal을 잡지 않는다. 그래도 남기는 것은 "프로바이더
            # 실패를 한 번에 잡자"며 아래를 `except ProviderError`로 넓히는
            # 리팩터를 막기 위해서다. 그때 이 절이 없으면 401이 재시도
            # 대상이 되고, 있으면 순서가 앞서 그대로 전파된다.
            raise
        except RetryableProviderError as exc:
            last = exc
            if attempt < max_retries:
                delay = backoff_delay(attempt, exc.retry_after_s)
                if on_retry is not None:
                    on_retry(attempt, delay, exc)
                sleep(delay)

    # 루프가 한 번은 돌고(위에서 max_retries >= 0을 보장한다) 끝까지 온 것은
    # 매 회 재시도 가능 실패였다는 뜻이므로 last는 반드시 채워져 있다.
    assert last is not None
    raise last
