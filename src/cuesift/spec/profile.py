"""규격 프로파일 로드와 검증 (요구사항정의서 FR-5.1, FR-5.3, §8.3).

프로파일은 언어별 YAML 하나다. TED 벤치마크 프로파일도 같은 스키마를 쓰고
파일만 나눈다(결정 D3) — 파일 안에 언어별 절을 두면 로더가 두 형태를 모두
다뤄야 하고, `--spec ted-ko` 같은 직접 지정도 못 하게 된다.
"""

from __future__ import annotations

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
    if raw[key] <= 0:
        # 0이면 모든 세그먼트가 위반이 되어 신호가 무의미해진다.
        # 조용히 통과시키면 "규격 위반 100%"가 정상처럼 보인다.
        raise ValueError(f"{key}는 0보다 커야 한다 (받은 값: {raw[key]})")


def load_profile(path: Path) -> SpecProfile:
    """YAML 파일 하나를 검증된 프로파일로 만든다."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: 최상위가 매핑이 아니다")

    # 기본값으로 조용히 채우지 않는다. 빠진 필드는 설정 실수이고,
    # 기본값을 넣으면 사용자가 의도한 것과 다른 규격으로 검사하게 된다.
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        raise ValueError(f"{path}: 필수 필드가 없다 — {', '.join(missing)}")

    try:
        counting = CharCounting(raw["char_counting"])
    except ValueError as exc:
        allowed = ", ".join(m.value for m in CharCounting)
        raise ValueError(
            f"{path}: char_counting이 '{raw['char_counting']}'다. 허용값: {allowed}"
        ) from exc

    for key in ("max_chars_per_line", "max_cps", "max_lines", "min_duration_ms"):
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
