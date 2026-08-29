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
    Binding(("output", "dir"), (("translate", "out"),)),
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
    Binding(("triage", "review_budget"), (("translate", "review_budget"),)),
    Binding(("triage", "review_threshold"), (("translate", "review_threshold"),)),
    Binding(("review", "out"), (("translate", "review_out"),)),
    Binding(("review", "format"), (("translate", "review_format"),)),
    Binding(("spec", "profile"), (("check", "spec"),)),
    Binding(("spec", "fail_on"), (("check", "fail_on"),)),
    Binding(("spec", "limit"), (("check", "limit"),)),
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
