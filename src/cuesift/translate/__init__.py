"""번역 계층 (요구사항정의서 §5.2 FR-2.1~2.8).

**대상 언어를 하나만 받는다.** FR-2.1의 "복수 대상 언어 동시 번역"은
"한 호출에 여러 언어"가 아니라 **"한 실행에 여러 언어"** 로 읽는다 -
`Segment.target_text`가 단수이고 `Glossary`와 spec이 한 언어 계약이라,
다르게 읽으면 세 모듈을 동시에 깨야 한다 (설계 §3.1).

재개(FR-2.7)와 캐시(NFR-3)는 이 계층에 없다. WP7b가 감싼다.

## 무엇을 공개하는가

**하위 모듈의 밑줄 없는 최상위 이름은 전부 여기 있다.** 감추는 수단은
이름 앞의 `_` 하나뿐이고, `tests/test_translate_api.py`가 그 규약을
강제한다 - 공개 심볼을 새로 만들고 재수출하지 않으면 게이트가 운다.

기준을 "호출자가 지금 쓰는 것"이 아니라 "밑줄이 없는 것"으로 잡은 이유는,
전자가 검사할 수 없는 기준이기 때문이다. 목록을 손으로 관리하면 새 심볼이
조용히 빠지고 호출자는 `cuesift.translate.engine`을 직접 파고들어 결국
파사드가 무의미해진다.

순수 함수 셋(`build_messages`·`iter_batches`·`parse_translations`)은 특히
의도적으로 공개한다 - `build_messages`는 WP8 자가일관성이 직접 부르고,
나머지 둘은 I/O가 없어 재사용과 단위 테스트의 가치가 크다.

## 예외를 잡는 쪽이 알아야 할 것

**`OpenAICompatibleProvider` 생성자가 던지는 `ValueError`는 `ProviderError`가
아니다.** `except ProviderError`로는 잡히지 않는다.

`ProviderError`는 "프로바이더 **호출** 실패의 최상위"이고 생성자의 실패는
호출이 아니라 **설정**이다 - 이 저장소가 exit 2("명령줄이 틀림")와
exit 66("파일 내용이 틀림")을 가른 것과 같은 축이다 (설계 §4.2).
설정 오류는 재시도해도 소용없고 세그먼트 단위로 강등할 대상도 아니다.

따라서 `except ProviderError`만 다는 호출부에서는 `base_url` 오타 하나가
트레이스백으로 새어 나간다. 다음 **여섯**이 맨 `ValueError`다.

| 자리 | 조건 |
| --- | --- |
| `base_url` | `httpx`가 URL로 읽지 못함 (`http://[::1` 같은 깨진 포트·괄호) |
| `base_url` | 스킴이 http/https가 아니거나 없음 |
| `base_url` | 호스트가 없음 |
| `base_url` | 쿼리(`?`)나 프래그먼트(`#`)를 포함 |
| `api_key` | 비-ASCII 문자를 포함 |
| `timeout`+`client` | 둘을 동시에 지정 |

첫 행은 `httpx.InvalidURL`을 감싼 것이다. `InvalidURL`은 `ValueError`도
`ProviderError`도 **아니라서** 그대로 두면 이 표의 계약을 깨뜨린다.

마지막 항은 `timeout`의 기본값이 `None` 센티널이라 성립한다. 센티널이
없으면 "60.0을 명시했다"와 "안 줬다"를 구분할 수 없어, 주입한 클라이언트가
있을 때 `timeout`이 조용히 무시되는 것을 막지 못한다. `timeout`을 주지
않으면 여전히 `DEFAULT_TIMEOUT_S`(60초)다.
"""

from __future__ import annotations

from cuesift.translate.batch import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONTEXT_WINDOW,
    BatchWindow,
    InvalidResponseError,
    iter_batches,
    parse_translations,
)
from cuesift.translate.engine import (
    DEFAULT_MAX_RETRIES,
    SegmentFailure,
    TranslationResult,
    translate_segments,
)
from cuesift.translate.openai_compat import DEFAULT_TIMEOUT_S, OpenAICompatibleProvider
from cuesift.translate.prompt import build_messages
from cuesift.translate.provider import (
    ChatMessage,
    Completion,
    FatalProviderError,
    Provider,
    ProviderError,
    RetryableProviderError,
    Role,
    TokenUsage,
)

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT_S",
    "BatchWindow",
    "ChatMessage",
    "Completion",
    "FatalProviderError",
    "InvalidResponseError",
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderError",
    "RetryableProviderError",
    "Role",
    "SegmentFailure",
    "TokenUsage",
    "TranslationResult",
    "build_messages",
    "iter_batches",
    "parse_translations",
    "translate_segments",
]
