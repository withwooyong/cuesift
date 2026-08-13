"""규격 프로파일 로드와 검증 (요구사항정의서 FR-5.1, FR-5.3, §8.3).

프로파일은 언어별 YAML 하나다. TED 벤치마크 프로파일도 같은 스키마를 쓰고
파일만 나눈다(결정 D3) — 파일 안에 언어별 절을 두면 로더가 두 형태를 모두
다뤄야 하고, `--spec ted-ko` 같은 직접 지정도 못 하게 된다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from cuesift.spec.counting import CharCounting

# 소스 트리에서는 리포 루트의 specs/, 설치본에서는 패키지 안의 specs/를 쓴다.
_PACKAGED = Path(__file__).resolve().parent.parent / "specs"
_REPO_ROOT = Path(__file__).resolve().parents[3] / "specs"
_BUILTIN_DIR = _PACKAGED if _PACKAGED.is_dir() else _REPO_ROOT

_REQUIRED = (
    "name",
    "source",
    "max_chars_per_line",
    "char_counting",
    "max_cps",
    "max_lines",
    "min_duration_ms",
    "max_duration_ms",
)


@dataclass(frozen=True, slots=True)
class SpecProfile:
    """언어 하나의 자막 규격 (FR-5.1)."""

    name: str
    source: str
    max_chars_per_line: float
    char_counting: CharCounting
    max_cps: float
    max_lines: int
    min_duration_ms: int
    max_duration_ms: int


def _require_positive(raw: dict[str, Any], key: str) -> None:
    value = raw[key]

    # bool을 먼저 막는다. isinstance(True, int)가 참이라 숫자 검사를 통과하고
    # max_lines: true가 1이 되어 2줄 자막이 전부 line_count 위반이 된다.
    if isinstance(value, bool) or not isinstance(value, int | float):
        # 비교(<=)가 값을 숫자라고 가정하면 TypeError가 나고, 미처리 traceback은
        # 종료 코드 1이 된다. 1은 "규격 위반 발견"이라 설정 실수가 자막 결함으로
        # 오보된다. 따옴표 친 숫자('20')와 빈 값은 YAML 초심자의 표준 실수다.
        raise ValueError(f"{key}는 숫자여야 한다 (받은 값: {value!r})")

    # float 변환을 여기서 미리 해 본다. 아래 isfinite도, SpecProfile을 만들 때의
    # float()도 거대 정수에서 OverflowError를 낸다 — 약 1.8e308(310자리)이 경계다.
    # int는 임의 정밀도라 YAML에서 얼마든지 커질 수 있고, OverflowError는
    # ValueError가 아니라서 호출자의 except를 그대로 빠져나간다.
    try:
        as_float = float(value)
    except OverflowError as exc:
        raise ValueError(f"{key}가 너무 크다 (자릿수: {len(str(value))})") from exc

    if not math.isfinite(as_float):
        # NaN은 모든 비교가 False라 해당 위반이 영원히 발화하지 않는데,
        # 로드에는 성공하므로 신호가 **조용히** 죽는다. 다른 위반은 계속
        # 발화해 겉보기엔 정상이라 더 위험하다. inf는 int() 변환에서 터진다.
        raise ValueError(f"{key}는 유한한 수여야 한다 (받은 값: {value!r})")

    if value <= 0:
        # 0이면 모든 세그먼트가 위반이 되어 신호가 무의미해진다.
        # 조용히 통과시키면 "규격 위반 100%"가 정상처럼 보인다.
        raise ValueError(f"{key}는 0보다 커야 한다 (받은 값: {value})")


def load_profile(path: Path) -> SpecProfile:
    """YAML 파일 하나를 검증된 프로파일로 만든다.

    **내용이 잘못된 경우는 전부 `ValueError`로 낸다**(파일을 못 읽는 것은
    `OSError`로 남는다). 호출자가 예외를 열거하지 않아도 되게 하는 것이 목적이다.

    열거는 계약이 아니라 **관찰**이라 이 함수가 새 예외를 낼 때마다 뒤처진다.
    실제로 `cli.py`의 열거가 두 번 넓어졌는데도 세 번째 누락(`TypeError`)이
    남아 있었다. 뒤처진 쪽으로 샌 예외는 미처리 traceback이 되어 종료 코드 1로
    나가고, 이 저장소에서 1은 "규격 위반 발견"이라 설정 사고가 자막 결함으로
    오보된다. 정규화를 여기서 하면 호출자는 `ValueError` 하나만 알면 된다.

    **`cli.py`의 `_resolve_profile`이 이 계약을 믿고 예외 열거를 그만뒀다.**
    여기를 느슨하게 만들면 그쪽이 조용히 무방비가 된다 — 호출부에는 아무 표시도
    나지 않고 새 예외가 그대로 통과한다. 정규화를 줄이려면 `cli.py`를 함께 봐야 한다.
    """
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: YAML을 읽을 수 없다 - {exc}") from exc
    except RecursionError as exc:
        # 중첩이 깊으면 파서가 재귀 한계에 걸린다. 이것도 입력 문제이므로
        # 프로그램 버그처럼 traceback으로 내보내지 않는다.
        raise ValueError(f"{path}: YAML 중첩이 너무 깊다") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"{path}: 최상위가 매핑이 아니다")

    # 기본값으로 조용히 채우지 않는다. 빠진 필드는 설정 실수이고,
    # 기본값을 넣으면 사용자가 의도한 것과 다른 규격으로 검사하게 된다.
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        # 이 메시지는 `--spec`을 통해 stderr로 나간다. em dash(U+2014)를 쓰지 않는 것은
        # cp949가 그것을 인코딩하지 못해(실측) 리다이렉트 시 종료 코드가 2에서 1로
        # 바뀌기 때문이다 — 1은 이 저장소에서 "규격 위반 발견"이다.
        raise ValueError(f"{path}: 필수 필드가 없다 - {', '.join(missing)}")

    try:
        counting = CharCounting(raw["char_counting"])
    except ValueError as exc:
        allowed = ", ".join(m.value for m in CharCounting)
        raise ValueError(
            f"{path}: char_counting이 '{raw['char_counting']}'다. 허용값: {allowed}"
        ) from exc

    # max_duration_ms가 빠져 있었다. 루프에 없는 키가 하나라도 있으면 그 키로
    # 뚫린다 — 아래 min/max 대소 비교가 숫자를 가정하기 때문이다.
    for key in (
        "max_chars_per_line",
        "max_cps",
        "max_lines",
        "min_duration_ms",
        "max_duration_ms",
    ):
        _require_positive(raw, key)

    if raw["max_duration_ms"] <= raw["min_duration_ms"]:
        raise ValueError(
            f"{path}: max_duration_ms({raw['max_duration_ms']})가 "
            f"min_duration_ms({raw['min_duration_ms']}) 이하다"
        )

    return SpecProfile(
        name=str(raw["name"]),
        source=str(raw["source"]),
        max_chars_per_line=float(raw["max_chars_per_line"]),
        char_counting=counting,
        max_cps=float(raw["max_cps"]),
        max_lines=int(raw["max_lines"]),
        min_duration_ms=int(raw["min_duration_ms"]),
        max_duration_ms=int(raw["max_duration_ms"]),
    )


def available_builtins() -> list[str]:
    """동봉된 프로파일 이름 목록."""
    return sorted(p.stem for p in _BUILTIN_DIR.glob("*.yaml"))


def load_builtin(name: str) -> SpecProfile:
    """`specs/<name>.yaml`을 읽는다."""
    path = _BUILTIN_DIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(
            f"'{name}' 프로파일이 없다. 사용 가능: {', '.join(available_builtins())}"
        )
    return load_profile(path)
