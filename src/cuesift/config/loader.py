"""`cuesift.yaml`을 읽어 검증한다 (FR-8.4 · 설계 §6).

**모든 내용 오류를 `ValueError`로 정규화한다.** `spec/profile.py`가 같은
계약을 갖고 있고, 호출자(`cli.py`)가 그것 하나만 잡아 종료 코드 2로 번역한다.
여기서 예외를 새로 흘리면 호출자가 못 잡아 미처리 traceback이 되고, 그것은
종료 코드 1("규격 위반 발견")로 오보된다.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

import yaml

from cuesift.config.schema import ALLOWED_PATHS, BINDINGS, BRANCH_PATHS, LEAF_PATHS
from cuesift.risk.fuse import DEFAULT_WEIGHTS

# v0.1이 지원하는 값. 요구사항정의서 §12 Q3 - 로컬 LLM은 OpenAI 호환
# 엔드포인트로 일원화한다.
_SUPPORTED_PROVIDERS = ("openai-compatible",)

_WEIGHTS_PATH = ("signals", "weights")


@dataclass(frozen=True, slots=True)
class Config:
    """검증된 설정 하나.

    `values`가 평평한 것은 미지 키 진단이 경로 문자열을 필요로 하기 때문이다.
    중첩 딕셔너리로 들고 있으면 `to_default_map`이 매 행마다 다시 파고들어야
    한다.
    """

    source: Path
    values: dict[tuple[str, ...], object]
    weights: dict[str, float] | None

    def to_default_map(self) -> dict[str, dict[str, object]]:
        """click이 읽을 커맨드 중첩으로 접는다 (설계 D8 · §5).

        **없는 키를 채우지 않는다.** `None`으로 채우면 click이 그것을
        "설정이 준 값"으로 보고 옵션의 실제 기본값을 덮는다.
        """
        out: dict[str, dict[str, object]] = {}
        for binding in BINDINGS:
            if binding.path not in self.values:
                continue
            value = self.values[binding.path]
            if binding.transform is not None:
                try:
                    value = binding.transform(value)
                except ValueError as exc:
                    # 변환 함수는 파일 경로를 모른다. 여기서 실어 준다.
                    raise ValueError(f"{self.source}: {exc}") from exc
            for command, param in binding.targets:
                out.setdefault(command, {})[param] = value
        return out


def load_config(path: Path) -> Config:
    """설정 파일을 읽는다. 내용 오류는 전부 `ValueError`다."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # `spec/profile.py`와 같은 문구다. 두 경로의 진단을 일치시킨다.
        raise ValueError(
            f"{path}: utf-8로 읽을 수 없다 (바이트 {exc.start}). "
            "파일을 utf-8로 변환한 뒤 다시 시도한다."
        ) from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: YAML을 읽을 수 없다 - {exc}") from exc
    except RecursionError as exc:
        raise ValueError(f"{path}: YAML 중첩이 너무 깊다") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: 최상위가 매핑이 아니다")

    values = _flatten(path, raw)
    _check_provider(path, values)
    weights = _read_weights(path, values.pop(_WEIGHTS_PATH, None))
    return Config(source=path, values=values, weights=weights)


def _flatten(path: Path, raw: dict[object, object]) -> dict[tuple[str, ...], object]:
    """중첩 매핑을 경로->값으로 편다. 모르는 키는 거부한다 (설계 D4)."""
    out: dict[tuple[str, ...], object] = {}
    stack: list[tuple[tuple[str, ...], dict[object, object]]] = [((), raw)]
    while stack:
        prefix, node = stack.pop()
        for key, value in node.items():
            here = (*prefix, str(key))
            if here in LEAF_PATHS:
                # 하위 키가 신호 이름이라 내려가지 않는다.
                out[here] = value
            elif here in ALLOWED_PATHS:
                out[here] = value
            elif here in BRANCH_PATHS and isinstance(value, dict):
                stack.append((here, value))
            else:
                raise ValueError(_unknown_key(path, here, ALLOWED_PATHS | BRANCH_PATHS))
    return out


def _unknown_key(path: Path, here: tuple[str, ...], known: frozenset[tuple[str, ...]]) -> str:
    """모르는 키 메시지. 가까운 키를 함께 낸다 (설계 §6).

    후보 제시에 표준 라이브러리만 쓴다 - 의존성을 늘리지 않는다.
    """
    dotted = ".".join(here)
    candidates = difflib.get_close_matches(
        dotted, sorted(".".join(p) for p in known), n=1, cutoff=0.6
    )
    tail = f". 가까운 키: {candidates[0]}" if candidates else ""
    return f"{path}: 모르는 키 '{dotted}'{tail}"


def _check_provider(path: Path, values: dict[tuple[str, ...], object]) -> None:
    """로더가 판정하는 유일한 값이다 (설계 D5).

    나머지 22개는 click이 파라미터 타입으로 변환하며 검증한다. 여기서 다시
    검사하면 같은 규칙이 두 곳에 생기고 반드시 갈린다.
    """
    provider = values.get(("llm", "provider"))
    if provider is None:
        return
    if provider not in _SUPPORTED_PROVIDERS:
        allowed = ", ".join(_SUPPORTED_PROVIDERS)
        raise ValueError(f"{path}: llm.provider가 '{provider}'다. 허용값: {allowed}")


def _read_weights(path: Path, raw: object) -> dict[str, float] | None:
    """가중치를 기본값 위에 얹는다 (설계 §5.1).

    **값 타입을 여기서 검사한다.** 이 22개 중 유일하게 click을 거치지 않는
    값이라, 숫자가 아니면 `fuse()`의 `math.isfinite`가 `TypeError`를 내고
    미처리 traceback이 종료 코드 1("규격 위반 발견")로 오보된다.

    **범위(음수·NaN·inf)는 검사하지 않는다.** `fuse()`가 이미 막는다.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: signals.weights가 매핑이 아니다")

    merged = dict(DEFAULT_WEIGHTS)
    known = frozenset((name,) for name in DEFAULT_WEIGHTS)
    for key, value in raw.items():
        name = str(key)
        if name not in DEFAULT_WEIGHTS:
            raise ValueError(_unknown_key(path, (name,), known))
        # `bool`을 먼저 막는다. `bool`은 `int`의 하위형이라 `float()`에
        # 통과하고, `true`가 1.0이 되면 "가중치를 껐다"고 믿은 사용자가
        # 1.0으로 검수받는다.
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{path}: signals.weights.{name}가 숫자가 아니다 ({value!r})")
        merged[name] = float(value)
    return merged
