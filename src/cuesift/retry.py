"""재시도 백오프 정책 (FR-1.2 · 설계 §4.1).

**번역과 STT가 같은 정책을 쓰는 것이 이 모듈의 존재 이유다.** 각자 두면
한쪽만 상한을 고쳤을 때 다른 쪽이 무한정 자라고, 그 갈림은 예외가 아니라
"CLI가 하루 동안 무출력으로 멈춰 있다"로만 드러난다.

**루프는 여기 없다.** `translate`는 `provider.complete(messages, temperature,
max_tokens)`를 부르고 STT는 `transcribe(audio, language=)`를 부른다 -
시그니처가 달라 루프 자체는 공유할 수 없다(설계 P3). 공유할 수 있는 것은
정책 함수 하나뿐이고, 그것이 이 모듈이다.
"""

from __future__ import annotations

BACKOFF_BASE_S = 1.0
"""지수 백오프의 첫 간격이다. `2**attempt`로 증폭되므로 이 값이 그대로
남지 않는다 - 크면 첫 재시도까지의 지연이 배수로 불어나 사용자가 그만큼 더
기다리고, 0에 가까우면 지수 백오프가 사실상 즉시 재시도가 되어 429를 유발한
부하를 그대로 유지한다."""

MAX_BACKOFF_S = 60.0
"""한 번의 대기 상한이다. **이 상한은 예외의 계약이 아니라 여기의 정책이다** -
`RetryableProviderError`는 도메인(0 이상의 유한한 초) 밖만 걸러내고 크기는
보지 않는다.

크면 `Retry-After: 86400`(일일 할당량 리셋을 알리는 실서비스의 흔한 값)을
그대로 자서 CLI가 하루 동안 무출력으로 멈춘다. `sleep`이 주입 가능해도
기본값이 `time.sleep`이라 실사용은 그대로 걸린다. 작으면 서버가 준 유효한
힌트를 무시해 제한이 풀리기 전에 다시 걸고 429가 재발한다 - 무시하지 않으려고
힌트를 존중한 의미가 사라진다.

번역의 기본 설정(`max_retries=3`)도 STT의 `STT_MAX_RETRIES=3`도 지수 백오프
최대가 4.0초라 이 상한에 닿지 않는다. 상한이 실제로 관여하는 것은 서버가 준
큰 힌트와 `max_retries`를 크게 잡은 설정뿐이다."""


def backoff_delay(attempt: int, retry_after_s: float | None) -> float:
    """대기 시간. 서버가 지정했으면 그것이 우선이고, 상한에서 잘린다.

    `is not None`이어야 한다. 참·거짓으로 보면 `retry_after_s=0`이 None과
    뭉뚱그려져 지수 백오프로 떨어진다 - 0은 "쓸 수 있는 힌트가 없음"이
    아니라 "지금 다시 걸어도 된다"는 유효한 힌트이고, 프로바이더의 정규화도
    0을 통과시킨다.

    상한을 두 경로 **모두**에 거는 것이 요점이다. 서버 힌트에만 걸면
    `max_retries`를 크게 잡은 설정에서 `2**attempt`가 그대로 자란다.
    """
    delay = retry_after_s if retry_after_s is not None else BACKOFF_BASE_S * (2**attempt)
    return min(delay, MAX_BACKOFF_S)
