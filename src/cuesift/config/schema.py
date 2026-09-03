"""`cuesift.yaml`의 키를 CLI 파라미터에 잇는 매핑표 (FR-8.4 · 설계 §5).

**이 표가 단일 출처다.** 허용 키 목록을 따로 두면 "허용은 되는데 아무 데도
가지 않는 키"가 생기고, 그것은 조용히 무시되는 설정이라 설계 D4가 막으려는
것과 같은 결함이다. `ALLOWED_PATHS`는 여기서 파생시킨다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


def join_targets(value: object) -> str:
    """`targets: [en, ja]`를 `--to`의 `"en,ja"`로 만든다 (설계 §5 2행)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple):
        for item in value:
            # **`str(item)`은 무엇이든 받는다.** `[en, null]`이 `"en,None"`이
            # 되고, `--to`는 검증 없이 `_output_path`를 거쳐 파일 이름 조각이
            # 되므로 언어 코드 `None`으로 파일이 나가고 종료 코드는 0이다.
            if not isinstance(item, str):
                raise ValueError(f"targets의 원소가 문자열이 아니다 ({item!r})")
        return ",".join(value)
    raise ValueError("targets는 목록이거나 쉼표로 구분한 문자열이어야 한다")


def negate(value: object) -> bool:
    """`cache.enabled`를 `--no-cache`로 뒤집는다 (설계 D12).

    YAML은 긍정형이다. `no_cache: false`는 이중부정이라 손으로 쓰고 오래
    남는 문서에서 매번 되짚게 된다.

    **입력이 `bool`인 것은 로더가 보장한다**(`_check_cache_enabled`).
    여기서 아무 값이나 받으면 `"false"`가 참이 되어 캐시가 켜지고, 그 값은
    이미 `bool`이라 click도 걸러 내지 못한다 - 로더도 click도 보지 않는
    유일한 값이 되는 것이다(설계 D5).
    """
    return not value


def require_int(value: object) -> int:
    """`triage.review_top_k`가 정수인지 본다 (FR-6.3 ① · FR-8.4 · 설계 D5).

    **click의 `IntRange`보다 먼저 도는 유일한 자리다.** `default_map`이 채운
    값도 옵션 타입 검증을 받지만, 그 검증은 `int(True) == 1`·`int(2.5) == 2`로
    **먼저 변환**해 버려 `policy.py`의 `bool` 거부에 값이 도달하지 못한다.
    그래서 `review_top_k: false`가 exit 0으로 돌면서 `k=0`이 되고, 사용자는
    "정책을 껐다"고 생각한 자리에서 **트리아지가 켜진 채 빈 검수 큐**를 받는다.

    **`bool`을 `int`보다 먼저 막는다.** `bool`은 `int`의 서브클래스라
    `isinstance(True, int)`가 참이다.
    """
    if isinstance(value, bool):
        raise ValueError(f"triage.review_top_k가 참·거짓이다 ({value!r}). 정수를 준다")
    if not isinstance(value, int):
        raise ValueError(f"triage.review_top_k가 정수가 아니다 ({value!r})")
    return value


def require_number(value: object) -> object:
    """`triage.review_threshold`가 숫자로 읽힐 값인지 본다 (FR-6.3 · FR-8.4).

    **`require_int`와 같은 자리, 다른 기준이다.** click의 `FloatRange`도
    `default_map` 값을 검사하지만 `float(True) == 1.0`·`float("0.5") == 0.5`로
    **먼저 변환**하므로, 변환이 성공하는 값은 무엇이든 임계값이 되어 버린다.
    그 결과가 두 증상이었다 - `review_threshold: true`가 exit 0으로 **임계값
    1.0**(사실상 아무것도 검수하지 않음)이 되고, 리스트를 주면 `float([])`의
    `TypeError`가 **raw traceback으로** 새어 exit 1이 됐다.

    **`bool`을 `int`보다 먼저 막는다.** `bool`은 `int`의 서브클래스라
    `isinstance(True, int)`가 참이다 - `signals.weights`가 같은 순서를 쓴다.

    **`str`을 통과시키는 것이 `require_int`와 갈리는 유일한 지점이고, 그것이
    의도다.** 따옴표 친 `'0.5'`는 click이 파싱해 값이 맞고 의도대로 돈다 -
    여기서 막으면 정상 동작하던 설정이 exit 2가 된다. `review_top_k`가 문자열
    까지 거부할 수 있었던 것은 그 키가 신설이라 **깨질 설정이 없었기** 때문이다.
    숫자가 아닌 문자열(`'abc'`)은 뒤에서 click이 거부한다.
    """
    if isinstance(value, bool):
        raise ValueError(
            f"triage.review_threshold가 참·거짓이다 ({value!r}). 0.0~1.0 사이 숫자를 준다"
        )
    if not isinstance(value, int | float | str):
        raise ValueError(f"triage.review_threshold가 숫자가 아니다 ({value!r})")
    return value


@dataclass(frozen=True, slots=True)
class Binding:
    """YAML 경로 하나를 CLI 파라미터들에 잇는다.

    `targets`가 튜플인 것은 `source_lang`이 `translate`와 `transcribe`
    **둘 다**에 뿌려지기 때문이다(설계 §5 1행). 하나로 좁히면 FR-8.3 배선
    시점에 "모든 옵션"이 조용히 거짓이 된다.
    """

    path: tuple[str, ...]
    targets: tuple[tuple[str, str], ...]
    transform: Callable[[object], object] | None = None


BINDINGS: tuple[Binding, ...] = (
    Binding(("source_lang",), (("translate", "source_lang"), ("transcribe", "source_lang"))),
    Binding(("targets",), (("translate", "to"),), join_targets),
    Binding(("llm", "base_url"), (("translate", "base_url"),)),
    Binding(("llm", "model"), (("translate", "model"),)),
    Binding(("llm", "context_window"), (("translate", "context_window"),)),
    Binding(("glossary",), (("translate", "glossary"),)),
    Binding(("work_context",), (("translate", "work_context"),)),
    Binding(("output", "dir"), (("translate", "out"), ("transcribe", "out"))),
    # **변환 함수가 없다.** `cache.enabled → --no-cache`가 `negate`를 거치는
    # 것과 달리 YAML의 `true`가 곧 `--progress`다 (FR-8.5).
    Binding(("output", "progress"), (("translate", "progress"),)),
    Binding(("cache", "dir"), (("translate", "cache_dir"),)),
    Binding(("cache", "enabled"), (("translate", "no_cache"),), negate),
    Binding(("dry_run",), (("translate", "dry_run"),)),
    Binding(("signals", "tier1", "enabled"), (("translate", "tier1"),)),
    Binding(("signals", "tier1", "max_ratio"), (("translate", "tier1_max_ratio"),)),
    Binding(("signals", "tier1", "samples"), (("translate", "tier1_samples"),)),
    Binding(("signals", "tier1", "temperature"), (("translate", "tier1_temperature"),)),
    # **`review_budget`에만 변환 함수가 없다.** 옵션 타입이 `str | None`이라
    # click이 `str(True)`를 그대로 넘기고 `_parse_review_budget`이 숫자로 읽지
    # 못해 이미 exit 2를 낸다(실측). 여기에 검사를 더하면 같은 값을 두 자리에서
    # 거부하게 되고, 뒤엣것이 죽어도 앞엣것이 가려 아무도 모른다.
    Binding(("triage", "review_budget"), (("translate", "review_budget"),)),
    # 아래 둘은 **기준이 다르다.** `review_threshold`는 문자열을 통과시키고
    # `review_top_k`는 거부한다 - 옛 키에는 `'0.5'`로 적어 두고 정상 동작하던
    # 설정이 있을 수 있고, 신설 키에는 없기 때문이다. 근거는 각 함수에 있다.
    Binding(("triage", "review_threshold"), (("translate", "review_threshold"),), require_number),
    Binding(("triage", "review_top_k"), (("translate", "review_top_k"),), require_int),
    Binding(("review", "out"), (("translate", "review_out"),)),
    Binding(("review", "format"), (("translate", "review_format"),)),
    Binding(("spec", "profile"), (("check", "spec"),)),
    Binding(("spec", "fail_on"), (("check", "fail_on"),)),
    Binding(("spec", "limit"), (("check", "limit"),)),
    # **번역과 분리한 엔드포인트다**(설계 D7). Ollama는
    # `/v1/audio/transcriptions`를 제공하지 않아(WP9 실측) `llm.base_url`과
    # 하나로 묶으면 사용자가 번역과 전사 중 하나를 반드시 못 쓴다.
    Binding(
        ("stt", "base_url"),
        (("transcribe", "stt_base_url"), ("translate", "stt_base_url")),
    ),
    Binding(("stt", "model"), (("transcribe", "stt_model"), ("translate", "stt_model"))),
    # `translate`에만 간다 - `transcribe`는 영상이 위치 인자다.
    Binding(("input", "media"), (("translate", "media"),)),
)

# 파라미터로 가지 않지만 허용해야 하는 키 (설계 §5 3행·17행).
SPECIAL_PATHS: tuple[tuple[str, ...], ...] = (
    ("llm", "provider"),
    ("signals", "weights"),
)

ALLOWED_PATHS: frozenset[tuple[str, ...]] = frozenset(b.path for b in BINDINGS) | frozenset(
    SPECIAL_PATHS
)

# 하위 키가 신호 이름이라 미지 키 검사가 내려가면 안 된다.
LEAF_PATHS: frozenset[tuple[str, ...]] = frozenset({("signals", "weights")})

# 허용 경로들의 진접두사. 평탄화가 어디까지 내려갈지를 정한다.
BRANCH_PATHS: frozenset[tuple[str, ...]] = frozenset(
    path[:i] for path in ALLOWED_PATHS for i in range(1, len(path))
)
