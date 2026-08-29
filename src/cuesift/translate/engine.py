"""번역 실행 엔진 - 배치, 검증, 개별 폴백, 재시도 (FR-2.1, FR-2.6).

**흐름의 세 가지 불변식** (설계 §6):

1. FatalProviderError는 재시도하지 않고 그대로 전파한다. 401을 세그먼트
   실패로 삼키면 사용자가 800건 실패 리포트를 받고 원인이 키 하나였다는
   것을 모른다.
2. 재시도 소진 후에는 개별 폴백을 하지 않는다. 폴백은 "모델이 지시를
   어김"의 처방이지 "서버가 죽음"의 처방이 아니다. 네트워크가 끊긴
   상태에서 강등하면 실패 1회가 실패 10회로 늘어날 뿐이다.
3. 원본 Segment를 변형하지 않는다. 제자리 수정하면 en 파이프라인이 채운
   target_text를 ja 파이프라인이 덮어써 두 언어를 동시에 들 수 없다.

**실패 분류가 이 모듈의 산출물이다.** `SegmentFailure.reason` 세 값은
서로 다른 처방으로 이어진다 - `provider_error`는 다시 돌리면 되고,
`invalid_response`는 모델을 바꿔야 하고, `empty_translation`은 그 자막이
원래 번역할 것이 없었을 수 있다. 뭉뚱그리면 "실패 800건"에서 무엇을 해야
할지 알 수 없다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from cuesift.glossary import Glossary
from cuesift.progress import ProgressCallback, ProgressUpdate
from cuesift.segment.models import Segment
from cuesift.translate.batch import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONTEXT_WINDOW,
    BatchWindow,
    InvalidResponseError,
    iter_batches,
    parse_translations,
)
from cuesift.translate.prompt import build_messages
from cuesift.translate.provider import (
    ChatMessage,
    Completion,
    FatalProviderError,
    Provider,
    RetryableProviderError,
    TokenUsage,
)

# 크면 429가 지속될 때 실패 확정이 한없이 미뤄지고, 작으면 일시적 5xx에
# 취약해진다.
DEFAULT_MAX_RETRIES = 3

# 지수 백오프의 첫 간격이다. `2**attempt`로 증폭되므로 이 값이 그대로
# 남지 않는다 - 크면 첫 재시도까지의 지연이 배수로 불어나 사용자가 그만큼
# 더 기다리고, 0에 가까우면 지수 백오프가 사실상 즉시 재시도가 되어 429를
# 유발한 부하를 그대로 유지한다.
_BACKOFF_BASE_S = 1.0

# 한 번의 대기 상한이다. **이 상한은 Task 1의 계약이 아니라 여기의 정책이다** -
# `RetryableProviderError`는 도메인(0 이상의 유한한 초) 밖만 걸러내고 크기는
# 보지 않는다.
#
# 크면 `Retry-After: 86400`(일일 할당량 리셋을 알리는 실서비스의 흔한 값)을
# 그대로 자서 CLI가 하루 동안 무출력으로 멈춘다. `sleep`이 주입 가능해도
# 기본값이 `time.sleep`이라 실사용은 그대로 걸린다.
# 작으면 서버가 준 유효한 힌트를 무시해 제한이 풀리기 전에 다시 걸고 429가
# 재발한다 - 무시하지 않으려고 힌트를 존중한 의미가 사라진다.
#
# 기본 설정(`max_retries=3`)의 지수 백오프는 최대 4.0초라 이 상한에 닿지
# 않는다. 상한이 실제로 관여하는 것은 서버가 준 큰 힌트와 `max_retries`를
# 크게 잡은 설정뿐이다.
_MAX_BACKOFF_S = 60.0


@dataclass(frozen=True, slots=True)
class SegmentFailure:
    """번역하지 못한 세그먼트 하나 (FR-2.6).

    `reason`을 남기지 않으면 "실패 800건"에서 원인이 서버인지 모델인지
    구분할 수 없다. `attempts`는 실제 호출 횟수이고, 없으면 한 번 만에
    죽었는지 네 번 버텼는지가 리포트에서 사라진다.
    """

    segment_id: str
    reason: str  # "provider_error" | "invalid_response" | "empty_translation"
    attempts: int


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """대상 언어 하나에 대한 번역 결과 (설계 §3.2).

    ## 계약: `failures`의 세그먼트를 triage에 넣지 마라

    **실패분은 `segments`에 `target_text=None`으로 들어 있다.** 그것을 그대로
    `collect_all`에 넘기면 `struct.empty`가 **`hard_fail=True`로 판정하고**,
    hard fail은 검수 예산을 우회해 `select_by_budget`의 quota를 소진한다
    (FR-6.2). 즉 **프로바이더 장애 하나가 진짜 오류를 검수 큐에서 밀어낸다.**

    실측(200큐 · 진짜 오류 20건 · 요청 예산 10%):

    | 번역 실패 | 실제 검수 비율 | Recall@10% |
    | --- | --- | --- |
    | 0건 | 10.0% | 100% |
    | 10건 | 10.0% | 50% |
    | 20건 | 10.0% | **0%** (quota 전량 소진) |
    | 30건 | **15.0%** | 0% (요청 예산까지 넘어 §9.1 배수의 분모가 부푼다) |

    **오염이 오류에서 오지 않고 "번역이 실패했다"는 사실 자체에서 온다** -
    번역 안 된 자막은 **검수 대상이 아니라 재실행 대상**이다.

    따라서 호출자는 **`failures`에 있는 `segment_id`를 triage 입력에서
    제외하거나 별도 경로로 보고해야 한다.** 정보는 이미 여기 다 있고,
    없는 것은 그것을 써야 한다는 말뿐이었다 - `result.segments`를 그대로
    넘기는 것이 지금 구조에서 **가장 자연스러운(그리고 틀린) 배선**이다.
    """

    target_lang: str
    segments: tuple[Segment, ...]
    failures: tuple[SegmentFailure, ...]
    usage: TokenUsage


def _backoff_delay(attempt: int, retry_after_s: float | None) -> float:
    """대기 시간. 서버가 지정했으면 그것이 우선이고, 상한에서 잘린다.

    `is not None`이어야 한다. 참·거짓으로 보면 `retry_after_s=0`이 None과
    뭉뚱그려져 지수 백오프로 떨어진다 - 0은 "쓸 수 있는 힌트가 없음"이
    아니라 "지금 다시 걸어도 된다"는 유효한 힌트이고, Task 1의 정규화도
    0을 통과시킨다.

    상한을 두 경로 **모두**에 거는 것이 요점이다. 서버 힌트에만 걸면
    `max_retries`를 크게 잡은 설정에서 `2**attempt`가 그대로 자란다.
    """
    delay = retry_after_s if retry_after_s is not None else _BACKOFF_BASE_S * (2**attempt)
    return min(delay, _MAX_BACKOFF_S)


def translate_segments(
    segments: Sequence[Segment],
    *,
    provider: Provider,
    source_lang: str,
    target_lang: str,
    glossary: Glossary | None = None,
    work_context: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    context_window: int = DEFAULT_CONTEXT_WINDOW,
    temperature: float = 0.0,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
    on_progress: ProgressCallback | None = None,
) -> TranslationResult:
    """세그먼트를 대상 언어 하나로 번역한다.

    대상 언어를 **하나만** 받는 것이 FR-2.1의 해석이다 - 복수 언어는
    호출자가 루프를 돈다. Segment.target_text가 단수이고 Glossary와 spec이
    한 언어 계약이라, 다르게 읽으면 세 모듈을 동시에 깨야 한다 (설계 §3.1).

    `segments`는 **Sequence여야 한다.** 배치 분할이 `len()`을 쓰고 결과
    조립이 한 번 더 순회하므로, 제너레이터를 넘기면 두 자리에서 깨진다.

    `sleep`은 테스트가 실제로 기다리지 않게 하려고 주입 가능하다.

    `on_progress`는 배치가 끝날 때마다 `ProgressUpdate(done, total)`를 받는다
    (FR-8.5 · 설계 D1). **기본값이 `None`이면 한 번도 호출되지 않는다**(D3) -
    기존 호출부가 0줄도 바뀌지 않는 것이 이 기본값의 산물이다.

    대안이던 "CLI가 `iter_batches`를 직접 돌기"를 버린 이유는 재시도·맥락
    윈도우·`TokenUsage` 합산 계약을 CLI가 복제하게 되기 때문이다 - 위
    독스트링이 명시한 계약(실패분 `target_text=None`, `TokenUsage`에
    `__radd__` 없음)이 두 곳에 생기고 반드시 갈라진다.
    """
    # `iter_batches`가 size·context_window를 호출 즉시 검사하는 것과 같은
    # 자리다. 음수를 통과시키면 재시도 루프가 한 번도 돌지 않은 채 끝나
    # "잡은 예외가 없는데 던질 것을 찾는" 자리에 떨어지고, 그 실패는
    # 설정 오타에서 멀리 떨어진 호출 스택 안쪽에서 드러난다.
    if max_retries < 0:
        raise ValueError(f"max_retries({max_retries})는 0 이상이어야 한다")

    translated: dict[str, str] = {}
    failures: list[SegmentFailure] = []
    usage = TokenUsage()
    # 총량은 **번역 대상 수**다. 맥락(before/after)은 대상이 아니다.
    total = len(segments)
    done = 0

    for window in iter_batches(segments, size=batch_size, context_window=context_window):
        batch_usage, batch_texts, batch_failures = _run_window(
            window,
            provider=provider,
            source_lang=source_lang,
            target_lang=target_lang,
            glossary=glossary,
            work_context=work_context,
            context_window=context_window,
            temperature=temperature,
            max_retries=max_retries,
            sleep=sleep,
        )
        # `sum()`을 쓰면 안 된다. TokenUsage에 `__radd__`가 없어 시드 0과의
        # 덧셈에서 TypeError가 난다.
        usage = usage + batch_usage
        translated.update(batch_texts)
        failures.extend(batch_failures)
        # **`window.batch`만 센다.** `before`/`after`를 더하면 `done`이
        # `total`을 넘고, 다음 배치에서 줄어든 것처럼 보인다.
        done += len(window.batch)
        if on_progress is not None:
            on_progress(ProgressUpdate(done, total))

    return TranslationResult(
        target_lang=target_lang,
        # 실패분에 `None`을 **넣는다.** 들어온 값을 그대로 두면 en으로 채운
        # 세그먼트를 ja로 다시 넣었을 때 실패분에 영어가 남고, 그것이 ja
        # 결과로 보고된다 - failures와 target_text가 서로 다른 말을 한다.
        segments=tuple(replace(s, target_text=translated.get(s.id)) for s in segments),
        failures=tuple(failures),
        usage=usage,
    )


def _run_window(
    window: BatchWindow,
    *,
    provider: Provider,
    source_lang: str,
    target_lang: str,
    glossary: Glossary | None,
    work_context: str | None,
    context_window: int,
    temperature: float,
    max_retries: int,
    sleep: Callable[[float], None],
) -> tuple[TokenUsage, dict[str, str], list[SegmentFailure]]:
    """배치 하나를 처리한다. 검증에 실패하면 개별 폴백으로 강등한다."""
    messages = build_messages(
        window.batch,
        source_lang=source_lang,
        target_lang=target_lang,
        before=window.before,
        after=window.after,
        glossary=glossary,
        work_context=work_context,
    )
    # 기대 id는 **배치만**이다. 맥락까지 넣으면 모델이 지시대로 맥락을
    # 빼고 답할 때마다 누락으로 판정돼 폴백이 상시 발동한다.
    expected = [s.index for s in window.batch]

    try:
        completion, usage, attempts = _call_with_retry(
            provider,
            messages,
            temperature=temperature,
            max_retries=max_retries,
            sleep=sleep,
        )
    except RetryableProviderError:
        # 재시도 소진. 개별 폴백을 타지 않는다 - 서버가 죽은 상태에서
        # 강등하면 실패 1회가 실패 N회로 늘어날 뿐이다 (설계 §6.3).
        # 여기서 사용량이 빈 것은 응답을 한 번도 받지 못했기 때문이다.
        return (
            TokenUsage(),
            {},
            [
                SegmentFailure(segment_id=s.id, reason="provider_error", attempts=max_retries + 1)
                for s in window.batch
            ],
        )

    try:
        mapping = parse_translations(completion.text, expected)
    except InvalidResponseError:
        fallback_usage, texts, fallback_failures = _fallback_individually(
            window,
            provider=provider,
            source_lang=source_lang,
            target_lang=target_lang,
            glossary=glossary,
            work_context=work_context,
            context_window=context_window,
            temperature=temperature,
            max_retries=max_retries,
            sleep=sleep,
        )
        # 깨진 배치 호출의 토큰도 더한다. 응답을 받았으므로 요금이 나갔고,
        # 빼면 폴백이 잦을수록 NFR-2 비용 리포트가 더 크게 어긋난다.
        return usage + fallback_usage, texts, fallback_failures

    texts, failures = _collect(window.batch, mapping, attempts=attempts)
    return usage, texts, failures


def _fallback_individually(
    window: BatchWindow,
    *,
    provider: Provider,
    source_lang: str,
    target_lang: str,
    glossary: Glossary | None,
    work_context: str | None,
    context_window: int,
    temperature: float,
    max_retries: int,
    sleep: Callable[[float], None],
) -> tuple[TokenUsage, dict[str, str], list[SegmentFailure]]:
    """배치를 세그먼트 1개짜리 호출들로 강등한다 (설계 §6.2).

    인자를 **kwargs로 뭉뚱그리지 않는다. 이 함수는 호출 비용이 배치 크기만큼
    늘어나는 자리라, 어떤 설정으로 강등됐는지가 인자 목록에 보여야 한다.

    **맥락은 세그먼트마다 다시 잡는다.** 원래 배치의 `before`/`after`를 그대로
    물려주면 배치 `[10,11,12]`에서 11을 개별 처리할 때 앞 맥락이 `[7][8][9]`가
    된다 - 가장 가까운 이웃 10·12가 빠질 뿐 아니라 **엉뚱한 세그먼트가 인접
    맥락 자리에 들어간다.** 하필 모델이 이미 형식을 어긴 자리에서 맥락이
    가장 나빠지는 셈이다.

    `[*before, *batch, *after]`가 원본의 **연속 구간**이라 여기서 슬라이스만
    하면 된다 - `segments` 전체를 넘길 필요가 없다.

    `context_window`를 인자로 받는 것은 `max(len(before), len(after))`로
    되짚으면 **파일 전체가 배치 하나보다 짧을 때** 양쪽이 다 비어 0으로
    떨어지기 때문이다. 그러면 세그먼트 2개짜리 파일에서 s0이 s1을 맥락으로
    받지 못한다 - 짧은 클립에서 늘 일어난다.
    """
    usage = TokenUsage()
    texts: dict[str, str] = {}
    failures: list[SegmentFailure] = []
    local = [*window.before, *window.batch, *window.after]

    for offset, segment in enumerate(window.batch):
        # max(0, ...)가 없으면 음수 시작이 슬라이스를 뒤에서부터 세게 한다
        # (`_iter_batches`가 같은 자리에서 같은 이유로 막는다).
        pos = len(window.before) + offset
        single = BatchWindow(
            batch=(segment,),
            before=tuple(local[max(0, pos - context_window) : pos]),
            after=tuple(local[pos + 1 : pos + 1 + context_window]),
        )
        single_usage, single_texts, single_failures = _run_single(
            single,
            provider=provider,
            source_lang=source_lang,
            target_lang=target_lang,
            glossary=glossary,
            work_context=work_context,
            temperature=temperature,
            max_retries=max_retries,
            sleep=sleep,
        )
        usage = usage + single_usage
        texts.update(single_texts)
        failures.extend(single_failures)

    return usage, texts, failures


def _run_single(
    window: BatchWindow,
    *,
    provider: Provider,
    source_lang: str,
    target_lang: str,
    glossary: Glossary | None,
    work_context: str | None,
    temperature: float,
    max_retries: int,
    sleep: Callable[[float], None],
) -> tuple[TokenUsage, dict[str, str], list[SegmentFailure]]:
    """세그먼트 하나를 번역한다. 여기서 실패하면 그 세그먼트만 실패다."""
    segment = window.batch[0]
    messages = build_messages(
        window.batch,
        source_lang=source_lang,
        target_lang=target_lang,
        before=window.before,
        after=window.after,
        glossary=glossary,
        work_context=work_context,
    )

    try:
        completion, usage, attempts = _call_with_retry(
            provider,
            messages,
            temperature=temperature,
            max_retries=max_retries,
            sleep=sleep,
        )
    except RetryableProviderError:
        return (
            TokenUsage(),
            {},
            [
                SegmentFailure(
                    segment_id=segment.id, reason="provider_error", attempts=max_retries + 1
                )
            ],
        )

    # 기대 id는 **원본 전역 인덱스**다. 0부터 다시 세면 첫 배치에서만
    # 우연히 맞고 두 번째 배치의 폴백이 전부 누락 판정을 받는다.
    try:
        mapping = parse_translations(completion.text, [segment.index])
    except InvalidResponseError:
        return (
            usage,
            {},
            [SegmentFailure(segment_id=segment.id, reason="invalid_response", attempts=attempts)],
        )

    texts, failures = _collect(window.batch, mapping, attempts=attempts)
    return usage, texts, failures


def _collect(
    batch: Sequence[Segment],
    mapping: dict[int, str],
    *,
    attempts: int,
) -> tuple[dict[str, str], list[SegmentFailure]]:
    """검증을 통과한 응답에서 빈 번역만 걸러낸다.

    빈 번역은 배치 폐기 사유가 아니다 - 개수도 번호도 맞았으므로 계약은
    지켜졌고, 그 세그먼트만 쓸모없는 것이다 (설계 §7.1). 배치로 버리면
    같은 응답에서 성공한 나머지 9개까지 함께 잃는다.

    `strip()`이 없으면 공백만 있는 번역("   ")이 성공으로 남아 규격 검사에
    빈 자막으로 흘러간다. `mapping[...]`을 `.get`으로 눅이지 않는 것은
    `parse_translations`가 이미 id 집합의 일치를 보장하기 때문이다 - 여기서
    눅이면 그 보장이 깨졌을 때 조용히 넘어간다.
    """
    texts: dict[str, str] = {}
    failures: list[SegmentFailure] = []
    for segment in batch:
        text = mapping[segment.index]
        if not text.strip():
            failures.append(
                SegmentFailure(segment_id=segment.id, reason="empty_translation", attempts=attempts)
            )
            continue
        texts[segment.id] = text
    return texts, failures


def _call_with_retry(
    provider: Provider,
    messages: Sequence[ChatMessage],
    *,
    temperature: float,
    max_retries: int,
    sleep: Callable[[float], None],
) -> tuple[Completion, TokenUsage, int]:
    """재시도 가능한 실패만 다시 건다. 반환은 (응답, 사용량, 시도 횟수)다.

    `max_retries`는 **재시도** 횟수라 총 호출은 `max_retries + 1`회다.
    0이면 한 번만 걸고 재시도하지 않는다.

    `except FatalProviderError: raise`는 **오늘은 아무것도 바꾸지 않는다.**
    Fatal과 Retryable은 형제라 아래 `except RetryableProviderError`가 애초에
    Fatal을 잡지 않는다 - 지우고 변이를 돌려도 죽는 테스트가 0개다.

    그래도 남기는 것은 "프로바이더 실패를 한 번에 잡자"며 아래를
    `except ProviderError`로 넓히는 리팩터를 막기 위해서다. 그때 이 절이
    없으면 401이 재시도 대상이 되고, 있으면 순서가 앞서 그대로 전파된다.
    실측(2026-08-16): 절을 지우고 넓히면 2개가 죽고, 남기고 넓히면 0개다.

    **이 절이 막지 못하는 것도 적어 둔다.** Fatal을 Retryable의 하위로
    옮기면 여기서 다시 던진 Fatal을 `_run_window`의 바깥
    `except RetryableProviderError`가 잡아 버린다 - 절이 있어도 2개가
    죽었다. 즉 **두 예외를 형제로 두는 것이 계약의 일부다.** 그 계약을
    실제로 지키는 것은 이 주석이 아니라
    `tests/test_translate_provider.py::test_재시도_가능_실패는_서로_구분된다`이고,
    상속 관계를 바꾸면 그쪽이 죽는다. 주석이 지워져도 계약은 남는다.

    마지막 시도 뒤에는 자지 않는다. 거기서 자면 아무도 기다릴 이유가 없는
    시간을 CLI가 쓴다 - 호출 N+1회에 대기는 N회다.

    **실패한 호출의 토큰 사용량은 세지 않는다.** 예외에는 응답 본문이 없어
    알 방법이 없다. NFR-2 비용 보고가 실패 호출분만큼 과소 계상된다는 뜻이고,
    그 한계를 여기 적어 둔다 - 나중에 "왜 청구서가 더 나왔나"를 여기서 찾게 된다.
    """
    last: RetryableProviderError | None = None

    for attempt in range(max_retries + 1):
        try:
            completion = provider.complete(messages, temperature=temperature, max_tokens=None)
        except FatalProviderError:
            raise
        except RetryableProviderError as e:
            last = e
            if attempt < max_retries:
                sleep(_backoff_delay(attempt, e.retry_after_s))
            continue
        return completion, completion.usage, attempt + 1

    # 루프가 한 번은 돌고(호출부가 max_retries >= 0을 보장한다) 끝까지 온
    # 것은 매 회 재시도 가능 실패였다는 뜻이므로 last는 반드시 채워져 있다.
    assert last is not None
    raise last
