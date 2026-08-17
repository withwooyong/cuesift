"""캐시를 끼운 프로바이더 (NFR-3 · FR-2.7 · 설계 §2.2).

**`Provider` 프로토콜을 구현하므로 엔진 입장에서는 그냥 또 하나의
프로바이더다.** 그래서 `translate/`를 한 줄도 고치지 않고 재개가 붙는다 -
재시도·백오프·배치 폴백·예외 분류가 전부 그대로 유효하고, 개별 폴백
호출도 각각 캐시된다.

**캐시는 engine이 분류할 수 없는 예외를 새로 만들지 않는다.** engine의
배치 폴백(FR-2.6)은 `RetryableProviderError`·`FatalProviderError` 두
자손만 잡는다 - 그 계약은 이 계층의 존재와 무관하게 이미 성립해야 하고,
캐시를 끼우는 행위 자체가 그 계약 밖의 새 실패 모드를 만들면 안 된다.
그래서 캐시 **자신의** 읽기·쓰기가 내는 예외(디스크 I/O·직렬화)는
`ProviderError` 계열이 아니어도 이 계층에서 흡수한다. `cache.CACHE_IO_ERRORS`
참고. **이 예외 집합은 `store()`의 tmp 정리 범위와 다르다** - 정리는
`write_text`~`os.replace` 사이의 어떤 중단이든 지워야 해서 **최대**여야
하고(`finally`, `KeyboardInterrupt`도 포함), 여기서 흡수하는 것은 캐시
자신의 잘못으로 **한정**돼야 한다(실측, WP7b Task 2 리뷰 라운드 3 - 둘을
같은 튜플로 묶었더니 `KeyboardInterrupt`에서 정리가 안 되는 결함이
나왔다). **inner가 던진 예외는 여기서 잡지 않는다** - 이미 분류돼 있고,
또 잡으면 재시도·폴백 분류가 이 계층에서 뭉개진다.

**예외를 캐시하지 않는 것은 구조적으로 보장된다.** 안쪽 `complete()`가
던지면 아래 저장 코드에 도달하지 못한다. 조건문으로 거르는 것이 아니라서
새 예외 종류가 생겨도 규칙이 깨지지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

# `CACHE_IO_ERRORS`의 정의는 `cache.py`에 있다 - 그쪽이 이 값을 "캐시
# 계층이 흡수해도 되는 예외"라는 계약으로 문서화하고, 여기서는 그대로
# 가져다 쓴다. 별도 상수를 두면 두 곳이 갈라질 위험이 생긴다(모듈
# 독스트링 참고). `cache.py`가 이 패키지 안에서 더 낮은 계층이라
# (`provider.py`가 `cache.py`를 이미 임포트한다) 반대 방향으로 임포트하면
# 순환 임포트가 된다.
from cuesift.store.cache import CACHE_IO_ERRORS, CacheRequest, load, store
from cuesift.translate.provider import ChatMessage, Completion, Provider


def _ignore(_message: str) -> None:
    """기본 경고 싱크. 라이브러리 사용자가 stderr를 강요받지 않게 한다."""


class CachingProvider:
    """`inner` 앞에 캐시를 끼운다."""

    name = "cached"

    def __init__(
        self,
        inner: Provider,
        *,
        identity: str,
        cache_dir: Path,
        warn: Callable[[str], None] = _ignore,
        attempt: int = 0,
    ) -> None:
        """`identity`는 **키워드 필수**다.

        `Provider` 프로토콜에 넣지 않은 이유는 `Protocol`이 런타임 검사를
        하지 않기 때문이다 - 서드파티 구현이 빠뜨려도 조용히 통과하므로
        강제한 것이 아니다. 필수 키워드 인자로 두면 **빠뜨릴 때
        `TypeError`로 즉시 죽는다.** 검사되는 계약이 검사되지 않는 선언보다 낫다.

        빈 문자열을 거부하는 이유는 `Provider.name`이 클래스 상수라
        (`"openai-compatible"`) 모델을 구분하지 못하기 때문이다. identity가
        비면 **`qwen2.5:3b`로 채운 캐시가 `gpt-4o` 실행에서 히트한다.**

        구분자로 `|`를 그대로 쓴 이유(`OpenAICompatibleProvider.cache_identity`가
        조립하는 값을 그대로 받는다)는 이 클래스가 identity를 **불투명한
        문자열**로만 다루기 때문이다 - 파싱하지 않고 캐시 파일에 그대로
        적어 사람이 읽는다. 안전한 이유는 세 가지다. (1) `base_url`에 `|`가
        섞이는 충돌 경로는 `openai_compat._require_http_url`이 이미
        닫았다. (2) identity는 캐시 파일에 저장되는 진단용 값이라 제어문자로
        이으면 오히려 읽을 수 없게 된다. (3) 남는 위험은 모델명에 `|`를 쓰는
        프로바이더인데 알려진 사례가 없다.
        """
        if not identity.strip():
            raise ValueError("identity가 비었다. 캐시 키가 모델을 구분하지 못한다")
        self._inner = inner
        self._identity = identity
        self._cache_dir = cache_dir
        self._warn = warn
        self._warned = False
        self.hits = 0
        self.misses = 0
        # 시도 번호는 **감싸는 시점에 고정된다.** complete()마다 받으면
        # Provider 프로토콜이 달라져 translate_segments를 고쳐야 한다.
        self._attempt = attempt

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> Completion:
        """캐시를 보고, 없으면 안쪽을 부르고 저장한다."""
        request = CacheRequest(
            identity=self._identity,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=tuple(messages),
            attempt=self._attempt,
        )
        cached = self._load_or_none(request)
        if cached is not None:
            self.hits += 1
            # **저장된 usage를 그대로 낸다** (설계 §3.5.1). 0으로 만들면
            # calls가 0이 되어 "호출당 토큰"을 영영 계산할 수 없다.
            # 실제 네트워크 호출 수는 self.misses가 따로 센다.
            return cached

        self.misses += 1
        completion = self._inner.complete(messages, temperature=temperature, max_tokens=max_tokens)
        self._store_or_warn(request, completion)
        return completion

    def _load_or_none(self, request: CacheRequest) -> Completion | None:
        """캐시 조회 실패를 미스로 떨어뜨린다. **경고 없이 조용히.**

        `load()` 자신도 손상된 파일을 미스로 다룬다(`cache.py`: "캐시는
        최적화이지 정확성의 근거가 아니다"). 여기서 새는 예외(비수치형
        `temperature`에서 `request.key` 계산이 `TypeError`/`ValueError`를
        내는 경로 등, `CACHE_IO_ERRORS` 참고)도 같은 성격이다 - 미스로
        떨어져도 바로 다음 줄에서 inner가 불려 **결과의 정확성은 그대로**다.
        잃는 것은 이번 호출의 캐시 적중 하나뿐이다.

        **트레이드오프를 적어 둔다.** 진짜 프로그래밍 오류(예: identity
        조립 실수로 인한 예외)도 같은 경로로 조용히 미스가 될 수 있어,
        성능 저하가 사용자에게 드러나지 않을 위험이 있다. `_store_or_warn`이
        경고하는 것과 비대칭으로 보일 수 있지만 의도적이다 - 저장 실패는
        "다음 실행에서 재개가 안 된다"는 사용자가 알아야 할 사실이고, 조회
        실패는 "이번 호출이 조금 더 느리다"로 끝나 매 호출 경고할 만큼
        사용자 행동을 바꾸지 않는다.
        """
        try:
            return load(self._cache_dir, request)
        except CACHE_IO_ERRORS:
            return None

    def _store_or_warn(self, request: CacheRequest, completion: Completion) -> None:
        try:
            store(self._cache_dir, request, completion)
        except CACHE_IO_ERRORS as exc:
            # 디스크가 차거나 읽기 전용이거나, 이번 응답이 UTF-8로
            # 직렬화되지 않아도(서러게이트 등) 번역이 실패할 이유는 없다.
            # **다만 조용히 삼키지는 않는다** - 사용자는 재개가 되는 줄 안다.
            # 한 번만 내는 것은 수백 번 반복하면 진짜 출력이 묻히기 때문이다.
            #
            # **트레이드오프를 적어 둔다.** 이 예외 집합은 `TypeError`도
            # 흡수한다 - inner가 `Provider` 프로토콜을 어겨(예:
            # `Completion.text`가 `str`이 아님) `json.dumps`가 직렬화할 수
            # 없는 값을 넘기면, 여기서는 경고 한 줄만 내고 그대로 진행한다.
            # 실패가 사라지는 것이 아니라 **원인이 훨씬 안 보이는 자리**로
            # 미뤄질 뿐이다 - 나중에 하류(예: `write_subtitle`)가 그 값을
            # 쓰다가 훨씬 덜 명확한 스택으로 죽는다. 참조 구현
            # (`openai_compat.py`)은 이 계약을 지키므로 정상 경로에서는
            # 도달하지 않지만, 서드파티 `Provider`가 어기는 경우까지 막는
            # 것은 이 계층의 책임 밖이라고 판단했다.
            self._warn_once(f"캐시를 쓰지 못했다(재개가 동작하지 않는다): {exc}")

    def _warn_once(self, message: str) -> None:
        if self._warned:
            return
        self._warned = True
        self._warn(message)
