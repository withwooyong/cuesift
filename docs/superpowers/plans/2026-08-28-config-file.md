# 설정 파일 `cuesift.yaml` (FR-8.4) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cuesift.yaml`로 CLI 옵션 23개를 전부 지정할 수 있게 하고, CLI 인자가 그것을 이기게 한다 (FR-8.4).

**Architecture:** 우선순위 해결을 손으로 짜지 않는다. 로더가 도메인 중첩 YAML을 커맨드 중첩 딕셔너리로 접어 `ctx.default_map`에 넣으면 click이 파라미터 단위로 `COMMANDLINE > DEFAULT_MAP > DEFAULT`를 해결한다. CLI 옵션이 아닌 `signals.weights`만 `ctx.obj`로 따로 흘러 `fuse()` 호출 3곳에 도달한다.

**Tech Stack:** Python 3.11+ · typer 0.27(click 벤더링) · PyYAML · 표준 라이브러리 `difflib`

**Spec:** [`docs/superpowers/specs/2026-08-28-config-file-design.md`](../specs/2026-08-28-config-file-design.md)

## Global Constraints

- 모든 모듈 첫 줄에 `from __future__ import annotations`
- 독스트링과 주석은 **한국어**, 근거 FR·§ 번호를 병기한다 (예: `FR-8.4`, `D4`)
- 테스트 함수 이름도 **한국어**다 (기존 관례)
- ruff: `line-length = 100`, 규칙 `E,F,I,UP,B,SIM`
- 커밋 메시지는 **한국어**
- **의존성을 추가하지 않는다.** 런타임 4개(`typer`·`pysubs2`·`pyyaml`·`httpx`), dev 3개(`pytest`·`pytest-cov`·`ruff`)
- **사용자에게 나가는 문자열에 em dash(U+2014)를 쓰지 않는다.** cp949가 인코딩하지 못해 리다이렉트 시 종료 코드가 2에서 1로 바뀐다 (1은 "규격 위반 발견")
- **`typer._click`을 import하지 않는다.** 벤더링된 private 경로다. `ParameterSource`는 `getattr(src, "name", "")` 문자열 비교로 판정한다
- 파이썬 실행은 `.venv/Scripts/python.exe`
- 로컬 게이트는 CI와 대상이 같아야 한다. `src tests`로 좁히지 않는다

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

**착수 시점 게이트 수치: 테스트 1379개** (`1379/1382 collected, 3 deselected`).

## 스펙에서 바뀐 것

계획을 쓰면서 스펙의 구멍 하나를 찾았다. **Task 2에서 닫는다.**

| 스펙 | 무엇이 틀렸나 | 계획의 처리 |
| --- | --- | --- |
| D5 "값 검증은 click에 맡긴다" | `signals.weights`는 **click을 거치지 않는다.** `spec.violation: "높음"`을 쓰면 `fuse()`의 `math.isfinite(weight)`가 `TypeError`를 내고, 미처리 traceback은 종료 코드 1이 되어 "규격 위반 발견"으로 오보된다 | 로더가 weights 값만 `float`로 검증한다. 다른 22개 파라미터는 스펙대로 click에 맡긴다 |

## 파일 구조

| 파일 | 책임 | 태스크 |
| --- | --- | --- |
| `src/cuesift/config/__init__.py` | 공개 API — `Config` · `load_config` | 2·3 |
| `src/cuesift/config/schema.py` | 매핑표 `BINDINGS` + 허용 경로 파생 | 1 |
| `src/cuesift/config/loader.py` | YAML 읽기 · 평탄화 · 미지 키 거부 · weights 검증 | 2 |
| `src/cuesift/cli.py` | `main` 콜백 배선 · ENV 양보 · weights 전달 | 4·5·6 |
| `src/cuesift/tier1.py` | Tier 1 경로의 `fuse()` 2곳에 weights 전달 | 7 |
| `tests/test_config_schema.py` | 매핑표 상등 게이트 (R5) | 1 |
| `tests/test_config_loader.py` | 로더 단위 — 파싱·미지 키·weights·매핑 전수 | 2·3 |
| `tests/test_cli_config.py` | CLI 배선 — 우선순위 진리표·자동 탐색·ENV | 4·5 |
| `tests/test_cli_tier1.py` | 가중치 통로 2건 | 6·7 |

**가중치 테스트를 `test_cli_tier1.py`에 두는 이유**는 끝까지 도는 실행의 준비(`_full_args`·`_patch_provider`·`_clean_echo`·`_run_plain`·`_run_tier1`)가 전부 거기 있기 때문이다. 새 파일에 복사하면 준비 코드가 두 벌이 되고, 한쪽만 고쳐지면 두 테스트가 서로 다른 파이프라인을 재게 된다.

---

### Task 1: 매핑표와 상등 게이트

**Files:**

- Create: `src/cuesift/config/__init__.py`
- Create: `src/cuesift/config/schema.py`
- Test: `tests/test_config_schema.py`

**Interfaces:**

- Consumes: `cuesift.cli.app` (introspection 대상), `cuesift.risk.fuse.DEFAULT_WEIGHTS`
- Produces:
  - `Binding(path: tuple[str, ...], targets: tuple[tuple[str, str], ...], transform: Callable[[object], object] | None)`
  - `BINDINGS: tuple[Binding, ...]` — 22행
  - `SPECIAL_PATHS: tuple[tuple[str, ...], ...]` — `("llm","provider")` · `("signals","weights")`
  - `ALLOWED_PATHS: frozenset[tuple[str, ...]]` — BINDINGS와 SPECIAL_PATHS에서 **파생**
  - `BRANCH_PATHS: frozenset[tuple[str, ...]]` — 허용 경로들의 진접두사
  - `LEAF_PATHS: frozenset[tuple[str, ...]]` — `("signals","weights")` 하나. 하위로 내려가지 않는다
  - `join_targets(value) -> str` · `negate(value) -> bool`

- [x] **Step 1: 상등 게이트 테스트를 쓴다**

`tests/test_config_schema.py`:

```python
"""매핑표가 CLI 옵션 집합과 어긋나지 않는지 검사한다 (FR-8.4 · 설계 R5)."""

from __future__ import annotations

import typer

from cuesift.cli import app
from cuesift.config.schema import ALLOWED_PATHS, BINDINGS, LEAF_PATHS, SPECIAL_PATHS


def _cli_options() -> set[tuple[str, str]]:
    """(커맨드, 파라미터명) 집합. 위치인자와 --help는 뺀다 (설계 D13)."""
    group = typer.main.get_command(app)
    found: set[tuple[str, str]] = set()
    for name, sub in group.commands.items():
        for param in sub.params:
            if param.param_type_name != "option" or param.name == "help":
                continue
            found.add((name, param.name))
    return found


def test_매핑표가_CLI_옵션_집합과_상등이다() -> None:
    # 부분집합이 아니라 상등을 본다. 한쪽 방향만 보면 매핑표에 남은
    # 죽은 행을 못 잡는다(설계 R5).
    mapped = {target for binding in BINDINGS for target in binding.targets}
    assert mapped == _cli_options()


def test_CLI_옵션은_23개다() -> None:
    # translate 19 + check 3 + transcribe 1. 이 수가 바뀌면 위 상등도
    # 깨지지만, 여기서 먼저 어긋난 쪽을 알려 준다(설계 §5).
    assert len(_cli_options()) == 23


def test_허용_경로는_매핑표에서_파생된다() -> None:
    # 허용 목록을 손으로 두면 "허용은 되는데 아무 데도 안 가는 키"가
    # 생긴다 - 조용히 무시되는 설정이다(설계 §4.1).
    assert ALLOWED_PATHS == frozenset({b.path for b in BINDINGS}) | frozenset(SPECIAL_PATHS)


def test_signals_weights는_잎이다() -> None:
    # 하위 키가 신호 이름이라 미지 키 검사가 내려가면 안 된다.
    assert LEAF_PATHS == frozenset({("signals", "weights")})
```

- [x] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cuesift.config'`

- [x] **Step 3: `schema.py`를 쓴다**

```python
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
        return ",".join(str(item) for item in value)
    raise ValueError("targets는 목록이거나 쉼표로 구분한 문자열이어야 한다")


def negate(value: object) -> bool:
    """`cache.enabled`를 `--no-cache`로 뒤집는다 (설계 D12).

    YAML은 긍정형이다. `no_cache: false`는 이중부정이라 손으로 쓰고 오래
    남는 문서에서 매번 되짚게 된다.
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
```

`src/cuesift/config/__init__.py`는 이 태스크에서는 비워 두지 않고 한 줄만 쓴다 (Task 2에서 채운다):

```python
"""`cuesift.yaml` 설정 파일 (FR-8.4)."""

from __future__ import annotations
```

- [x] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_schema.py -v`
Expected: PASS 4건

- [x] **Step 5: 게이트를 실패시켜 본다**

`BINDINGS`의 마지막 행(`spec.limit`)을 주석 처리하고 다시 돌린다.

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_schema.py::test_매핑표가_CLI_옵션_집합과_상등이다 -v`
Expected: FAIL — 상등이 깨진다. **확인한 뒤 주석을 되돌린다.**

이 리포의 규율이다 — 실패시켜 보지 않은 게이트는 게이트가 아니다.

- [x] **Step 6: 커밋**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/config/ tests/test_config_schema.py
git commit -m "기능: FR-8.4 설정 키와 CLI 옵션을 잇는 매핑표와 상등 게이트

허용 키 목록을 매핑표에서 파생시킨다. 손으로 두면 '허용은 되는데 아무 데도
가지 않는 키'가 생기고 그것은 조용히 무시되는 설정이다.

상등 게이트는 부분집합이 아니라 상등을 본다. 한쪽 방향만 보면 매핑표에
남은 죽은 행을 못 잡는다. spec.limit 행을 지워 실제로 실패하는 것을 확인했다."
```

---

### Task 2: 로더 — 파싱·미지 키 거부·weights 검증

**Files:**

- Create: `src/cuesift/config/loader.py`
- Modify: `src/cuesift/config/__init__.py`
- Test: `tests/test_config_loader.py`

**Interfaces:**

- Consumes: Task 1의 `ALLOWED_PATHS` · `BRANCH_PATHS` · `LEAF_PATHS`
- Produces:
  - `Config(source: Path, values: dict[tuple[str, ...], object], weights: dict[str, float] | None)`
  - `load_config(path: Path) -> Config` — 모든 내용 오류를 `ValueError`로 정규화한다

- [x] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_config_loader.py`:

```python
"""`cuesift.yaml` 로더 (FR-8.4 · 설계 §6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cuesift.config import load_config


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cuesift.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_도메인_중첩을_평평한_경로로_읽는다(tmp_path: Path) -> None:
    path = _write(tmp_path, "source_lang: ko\nllm:\n  model: qwen2.5:3b\n")
    cfg = load_config(path)
    assert cfg.values[("source_lang",)] == "ko"
    assert cfg.values[("llm", "model")] == "qwen2.5:3b"
    assert cfg.source == path


def test_빈_파일은_빈_설정이다(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, ""))
    assert cfg.values == {}
    assert cfg.weights is None


def test_최상위가_매핑이_아니면_거부한다(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="최상위가 매핑이 아니다"):
        load_config(_write(tmp_path, "- a\n- b\n"))


def test_YAML_문법_오류를_거부한다(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="YAML을 읽을 수 없다"):
        load_config(_write(tmp_path, "a: [1, 2\n"))


def test_utf8이_아니면_거부한다(tmp_path: Path) -> None:
    path = tmp_path / "cuesift.yaml"
    path.write_bytes("source_lang: 한국어\n".encode("cp949"))
    with pytest.raises(ValueError, match="utf-8로 읽을 수 없다"):
        load_config(path)


def test_모르는_키를_거부하고_후보를_제시한다(tmp_path: Path) -> None:
    path = _write(tmp_path, "triage:\n  review_budgt: 10%\n")
    with pytest.raises(ValueError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert "모르는 키 'triage.review_budgt'" in message
    assert "가까운 키: triage.review_budget" in message


def test_후보가_없으면_후보절을_붙이지_않는다(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as excinfo:
        load_config(_write(tmp_path, "zzzzzzzz: 1\n"))
    assert "가까운 키" not in str(excinfo.value)


def test_지원하지_않는_provider를_거부한다(tmp_path: Path) -> None:
    path = _write(tmp_path, "llm:\n  provider: anthropic\n")
    with pytest.raises(ValueError, match="llm.provider"):
        load_config(path)


def test_openai_compatible은_통과한다(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, "llm:\n  provider: openai-compatible\n"))
    assert cfg.values[("llm", "provider")] == "openai-compatible"


def test_weights는_기본값_위에_얹힌다(tmp_path: Path) -> None:
    path = _write(tmp_path, "signals:\n  weights:\n    spec.violation: 0.3\n")
    cfg = load_config(path)
    assert cfg.weights is not None
    assert cfg.weights["spec.violation"] == 0.3
    # 명시하지 않은 신호는 1.0을 유지한다(설계 §5.1). 전량 지정을 요구하면
    # v0.2에서 신호가 늘 때 기존 설정 파일이 전부 거부된다(FR-6.5).
    assert cfg.weights["glossary.miss"] == 1.0
    assert len(cfg.weights) == 10


def test_모르는_신호_이름을_거부한다(tmp_path: Path) -> None:
    path = _write(tmp_path, "signals:\n  weights:\n    spec.violaton: 0.3\n")
    with pytest.raises(ValueError) as excinfo:
        load_config(path)
    assert "가까운 키: spec.violation" in str(excinfo.value)


def test_숫자가_아닌_가중치를_거부한다(tmp_path: Path) -> None:
    # 이 검사가 없으면 fuse()의 math.isfinite가 TypeError를 내고, 미처리
    # traceback은 종료 코드 1("규격 위반 발견")로 오보된다.
    path = _write(tmp_path, "signals:\n  weights:\n    spec.violation: 높음\n")
    with pytest.raises(ValueError, match="숫자가 아니다"):
        load_config(path)


def test_불리언_가중치를_거부한다(tmp_path: Path) -> None:
    # bool은 int의 하위형이라 float()에 통과한다. True가 1.0이 되면
    # "가중치를 껐다"고 믿은 사용자가 1.0으로 검수받는다.
    path = _write(tmp_path, "signals:\n  weights:\n    spec.violation: true\n")
    with pytest.raises(ValueError, match="숫자가 아니다"):
        load_config(path)


def test_weights가_매핑이_아니면_거부한다(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="매핑이 아니다"):
        load_config(_write(tmp_path, "signals:\n  weights: 0.3\n"))
```

- [x] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_loader.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_config'`

- [x] **Step 3: `loader.py`를 쓴다**

```python
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

from cuesift.config.schema import ALLOWED_PATHS, BRANCH_PATHS, LEAF_PATHS
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


def _unknown_key(
    path: Path, here: tuple[str, ...], known: frozenset[tuple[str, ...]]
) -> str:
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
```

`src/cuesift/config/__init__.py`:

```python
"""`cuesift.yaml` 설정 파일 (FR-8.4)."""

from __future__ import annotations

from cuesift.config.loader import Config, load_config

__all__ = ["Config", "load_config"]
```

- [x] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_loader.py -v`
Expected: PASS 14건

- [x] **Step 5: 게이트를 실패시켜 본다**

`_read_weights`의 `isinstance(value, bool)` 항을 지우고 돌린다.

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_loader.py::test_불리언_가중치를_거부한다 -v`
Expected: FAIL. **확인한 뒤 되돌린다.**

- [x] **Step 6: 커밋**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/config/ tests/test_config_loader.py
git commit -m "기능: FR-8.4 설정 파일 로더 - 미지 키 거부와 가중치 검증

내용 오류를 전부 ValueError로 정규화한다. spec/profile.py와 같은 계약이라
호출자가 하나만 잡아 종료 코드 2로 번역한다.

스펙 D5는 '값 검증을 click에 맡긴다'였는데 signals.weights만은 click을
거치지 않는다. 숫자가 아니면 fuse()의 math.isfinite가 TypeError를 내고
미처리 traceback이 종료 코드 1('규격 위반 발견')로 오보된다. 그래서
가중치 값만 로더가 검사한다. bool을 먼저 막는 것은 int의 하위형이라
float()에 통과하기 때문이다."
```

---

### Task 3: `to_default_map` — 도메인을 커맨드로 접는다

**Files:**

- Modify: `src/cuesift/config/loader.py`
- Test: `tests/test_config_loader.py`

**Interfaces:**

- Consumes: Task 1의 `BINDINGS`, Task 2의 `Config`
- Produces: `Config.to_default_map() -> dict[str, dict[str, object]]`

- [x] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_config_loader.py` 아래에 덧붙인다:

```python
def test_도메인_키가_커맨드별로_접힌다(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "llm:\n  model: m1\nspec:\n  profile: ko\n  limit: 5\n",
    )
    dm = load_config(path).to_default_map()
    assert dm["translate"]["model"] == "m1"
    assert dm["check"]["spec"] == "ko"
    assert dm["check"]["limit"] == 5


def test_source_lang은_두_커맨드에_뿌려진다(tmp_path: Path) -> None:
    dm = load_config(_write(tmp_path, "source_lang: ja\n")).to_default_map()
    assert dm["translate"]["source_lang"] == "ja"
    assert dm["transcribe"]["source_lang"] == "ja"


def test_targets_목록이_쉼표_문자열이_된다(tmp_path: Path) -> None:
    dm = load_config(_write(tmp_path, "targets: [en, ja]\n")).to_default_map()
    assert dm["translate"]["to"] == "en,ja"


def test_targets가_문자열이면_그대로_쓴다(tmp_path: Path) -> None:
    dm = load_config(_write(tmp_path, "targets: en,ja\n")).to_default_map()
    assert dm["translate"]["to"] == "en,ja"


def test_targets가_매핑이면_거부한다(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="targets는 목록이거나"):
        load_config(_write(tmp_path, "targets:\n  en: 1\n")).to_default_map()


def test_cache_enabled가_no_cache로_뒤집힌다(tmp_path: Path) -> None:
    dm = load_config(_write(tmp_path, "cache:\n  enabled: false\n")).to_default_map()
    assert dm["translate"]["no_cache"] is True
    dm = load_config(_write(tmp_path, "cache:\n  enabled: true\n")).to_default_map()
    assert dm["translate"]["no_cache"] is False


def test_weights는_default_map에_들어가지_않는다(tmp_path: Path) -> None:
    # CLI 옵션이 아니다(설계 D6). 여기 들어가면 click이 모르는 파라미터로
    # 죽는다.
    path = _write(tmp_path, "signals:\n  weights:\n    spec.violation: 0.3\n")
    cfg = load_config(path)
    dm = cfg.to_default_map()
    assert dm == {}
    assert cfg.weights is not None


def test_llm_provider는_default_map에_들어가지_않는다(tmp_path: Path) -> None:
    dm = load_config(_write(tmp_path, "llm:\n  provider: openai-compatible\n")).to_default_map()
    assert dm == {}


def test_지정하지_않은_키는_비어_있다(tmp_path: Path) -> None:
    # 없는 키를 None으로 채우면 click이 그것을 "설정이 준 값"으로 보고
    # 기본값을 덮는다.
    dm = load_config(_write(tmp_path, "source_lang: ko\n")).to_default_map()
    assert dm["translate"] == {"source_lang": "ko"}


def test_변환_오류에_파일_경로가_실린다(tmp_path: Path) -> None:
    path = _write(tmp_path, "targets:\n  en: 1\n")
    with pytest.raises(ValueError) as excinfo:
        load_config(path).to_default_map()
    assert str(path) in str(excinfo.value)


# 매핑 전수 - 24행 각각 1건 (설계 §8). 상등 게이트(Task 1)는 "행이 있는가"만
# 보고 "값이 실제로 도착하는가"는 보지 않는다. 오타 난 파라미터명이 상등
# 게이트를 통과한 뒤 click에서 조용히 무시되는 경로가 여기서 막힌다.
@pytest.mark.parametrize(
    ("yaml_text", "command", "param", "expected"),
    [
        ("source_lang: ja\n", "translate", "source_lang", "ja"),
        ("source_lang: ja\n", "transcribe", "source_lang", "ja"),
        ("targets: [en]\n", "translate", "to", "en"),
        ("llm:\n  base_url: http://h/v1\n", "translate", "base_url", "http://h/v1"),
        ("llm:\n  model: m1\n", "translate", "model", "m1"),
        ("llm:\n  context_window: 5\n", "translate", "context_window", 5),
        ("glossary: ./g.yaml\n", "translate", "glossary", "./g.yaml"),
        ("work_context: 다큐\n", "translate", "work_context", "다큐"),
        ("output:\n  dir: ./out\n", "translate", "out", "./out"),
        ("cache:\n  dir: ./c\n", "translate", "cache_dir", "./c"),
        ("cache:\n  enabled: false\n", "translate", "no_cache", True),
        ("dry_run: true\n", "translate", "dry_run", True),
        ("signals:\n  tier1:\n    enabled: true\n", "translate", "tier1", True),
        ("signals:\n  tier1:\n    max_ratio: 0.2\n", "translate", "tier1_max_ratio", 0.2),
        ("signals:\n  tier1:\n    samples: 2\n", "translate", "tier1_samples", 2),
        (
            "signals:\n  tier1:\n    temperature: 0.7\n",
            "translate",
            "tier1_temperature",
            0.7,
        ),
        ('triage:\n  review_budget: "10%"\n', "translate", "review_budget", "10%"),
        ("triage:\n  review_threshold: 0.7\n", "translate", "review_threshold", 0.7),
        ("review:\n  out: ./r\n", "translate", "review_out", "./r"),
        ("review:\n  format: html\n", "translate", "review_format", "html"),
        ("spec:\n  profile: ko\n", "check", "spec", "ko"),
        ("spec:\n  fail_on: none\n", "check", "fail_on", "none"),
        ("spec:\n  limit: 3\n", "check", "limit", 3),
    ],
)
def test_매핑_전수가_도착한다(
    tmp_path: Path, yaml_text: str, command: str, param: str, expected: object
) -> None:
    dm = load_config(_write(tmp_path, yaml_text)).to_default_map()
    assert dm[command][param] == expected
```

> **실행자 주의:** 위 목록은 22행이다 — `llm.provider`와 `signals.weights`는
> 파라미터로 가지 않아 여기 없고(대신 바로 위 두 테스트가 본다),
> `source_lang`이 두 커맨드로 뿌려져 항목이 23개가 된다.
> **`Path`·`Enum` 변환은 여기서 일어나지 않는다** — click이 파라미터를 해결할
> 때 한다(설계 P4). 그래서 기대값이 `"./out"`이지 `Path("./out")`이 아니다.

- [x] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_loader.py -k default_map -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'to_default_map'`

- [x] **Step 3: `Config`에 메서드를 더한다**

`loader.py`의 `Config` 안에 넣는다. `from cuesift.config.schema import BINDINGS`를 import에 더한다.

```python
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
```

- [x] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_loader.py -v`
Expected: PASS 24건

- [x] **Step 5: 커밋**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/config/loader.py tests/test_config_loader.py
git commit -m "기능: FR-8.4 도메인 중첩을 커맨드 중첩으로 접는다

변환이 있는 행은 셋뿐이다 - targets(목록에서 쉼표 문자열), cache.enabled
(부정 반전), weights(별도 경로라 default_map에 넣지 않는다).

없는 키를 None으로 채우지 않는다. 채우면 click이 그것을 '설정이 준 값'으로
보고 옵션의 실제 기본값을 덮는다."
```

---

### Task 4: `main` 콜백 배선 — 자동 탐색·`default_map`·출처 한 줄

**Files:**

- Modify: `src/cuesift/cli.py` (`main` 콜백, 420-460 부근)
- Test: `tests/test_cli_config.py`

**Interfaces:**

- Consumes: Task 2·3의 `load_config` · `Config.to_default_map`
- Produces: `ctx.default_map`이 채워진 상태 · `ctx.obj`가 `Config | None`

- [x] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cli_config.py`:

```python
"""설정 파일의 CLI 배선 (FR-8.4 · 설계 §4)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from cuesift.cli import app

runner = CliRunner()


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cuesift.yaml"
    path.write_text(text, encoding="utf-8")
    return path


_VIOLATIONS = Path(__file__).parent / "fixtures" / "ingest" / "check_violations.ass"


def _violation_lines(output: str) -> int:
    """위반 목록 줄 수. rich 장식에 흔들리지 않게 접두로 센다."""
    return sum(1 for line in output.splitlines() if line.lstrip().startswith("#"))


def _check(cfg: Path, *after: str):
    """`--config`는 그룹 옵션이라 `check` **앞**에, 나머지는 뒤에 온다."""
    return runner.invoke(app, ["--config", str(cfg), "check", str(_VIOLATIONS), *after])


# 우선순위 진리표 (설계 D3 · §8).
#
# **프로바이더가 필요 없는 `check`로 잰다.** `--limit`은 출력 줄 수를,
# `--fail-on`은 종료 코드를 바꾸므로 관측이 확실하다. 실측 기준값
# (`check_violations.ass` · `--spec ko`): 기본 exit 1 · 위반줄 4,
# `--limit 1`이면 위반줄 1, `--fail-on none`이면 exit 0.


def test_진리표_설정도_CLI도_없으면_기본값이다(tmp_path: Path) -> None:
    result = _check(_config(tmp_path, "spec:\n  profile: ko\n"))
    assert _violation_lines(result.output) == 4
    assert result.exit_code == 1


def test_진리표_설정만_있으면_설정이_이긴다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "spec:\n  profile: ko\n  limit: 2\n  fail_on: none\n")
    result = _check(cfg)
    assert _violation_lines(result.output) == 2
    assert result.exit_code == 0


def test_진리표_CLI만_있으면_CLI가_이긴다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "spec:\n  profile: ko\n")
    result = _check(cfg, "--limit", "1", "--fail-on", "none")
    assert _violation_lines(result.output) == 1
    assert result.exit_code == 0


def test_진리표_둘_다_있으면_CLI가_이긴다(tmp_path: Path) -> None:
    # **이 한 건이 FR-8.4 본문의 후반절 전체다.**
    cfg = _config(tmp_path, "spec:\n  profile: ko\n  limit: 2\n  fail_on: none\n")
    result = _check(cfg, "--limit", "1", "--fail-on", "hard")
    assert _violation_lines(result.output) == 1
    assert result.exit_code == 1


def test_설정이_필수_옵션을_만족시킨다(tmp_path: Path) -> None:
    # `--spec`은 필수다. 설정이 그것을 채우지 못하면 설정 파일로 '모든 옵션'을
    # 지정한다는 FR-8.4 본문이 성립하지 않는다(설계 P3).
    result = _check(_config(tmp_path, "spec:\n  profile: ko\n"))
    assert "Missing option" not in result.output


def test_설정에_넣은_input은_무시된다(tmp_path: Path) -> None:
    # 설계 D13 - 위치인자는 설정 대상이 아니다. `input`이 매핑표에 있으면
    # 다른 파일을 검수하고도 통과한다. 매핑표에 없으므로 모르는 키가 된다.
    cfg = _config(tmp_path, "spec:\n  profile: ko\ninput: 아무거나.srt\n")
    result = _check(cfg)
    assert result.exit_code == 2
    assert "모르는 키 'input'" in result.output


def test_설정을_읽으면_출처가_stderr에_나간다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "source_lang: ko\n")
    result = runner.invoke(app, ["--config", str(cfg), "check", "--help"])
    assert str(cfg) in result.output


def test_config_파일이_없으면_종료_코드_2다(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--config", str(tmp_path / "없다.yaml"), "check", "--help"])
    assert result.exit_code == 2


def test_모르는_키는_종료_코드_2다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "triage:\n  review_budgt: 10%\n")
    result = runner.invoke(app, ["--config", str(cfg), "check", "--help"])
    assert result.exit_code == 2
    assert "모르는 키" in result.output


def test_틀린_값은_click이_종료_코드_2로_낸다(tmp_path: Path) -> None:
    # 설계 D5·P4 - 로더가 아니라 click이 판정한다.
    cfg = _config(tmp_path, "review:\n  format: xml\n")
    result = runner.invoke(
        app, ["--config", str(cfg), "translate", str(tmp_path / "a.srt"), "--to", "en"]
    )
    assert result.exit_code == 2


def test_현재_디렉터리의_cuesift_yaml을_자동으로_읽는다(
    tmp_path: Path, monkeypatch
) -> None:
    _config(tmp_path, "source_lang: ja\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["check", "--help"])
    assert "cuesift.yaml" in result.output


def test_상위_디렉터리는_읽지_않는다(tmp_path: Path, monkeypatch) -> None:
    # 설계 D2 - 사용자가 존재를 모르는 파일이 검수 기준을 바꾸는 것을 막는다.
    _config(tmp_path, "source_lang: ja\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    result = runner.invoke(app, ["check", "--help"])
    assert "cuesift.yaml" not in result.output


def test_설정이_없으면_조용히_넘어간다(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0


def test_미구현_경고가_사라졌다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "source_lang: ko\n")
    result = runner.invoke(app, ["--config", str(cfg), "check", "--help"])
    assert "아직 구현되지" not in result.output


def test_help에서_미구현_문구가_사라졌다() -> None:
    result = runner.invoke(app, ["--help"])
    assert "아직 구현되지" not in result.output
```

- [x] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_config.py -v`
Expected: FAIL — 자동 탐색·출처 줄·경고 제거가 전부 미구현

- [x] **Step 3: `main` 콜백을 고친다**

`cli.py`의 import에 더한다:

```python
from cuesift.config import Config, load_config
```

모듈 상수를 `EXIT_*` 상수 근처에 더한다:

```python
# 자동 탐색은 현재 디렉터리 한 칸뿐이다(설계 D2). 상위로 올라가면 사용자가
# 존재를 모르는 파일이 검수 기준을 바꾸고, 가중치와 hard fail 임계가 실린
# 파일에서 그것은 Recall@Budget 수치를 조용히 오염시킨다.
_DEFAULT_CONFIG_NAME = "cuesift.yaml"
```

`main` 시그니처의 첫 인자로 `ctx: typer.Context`를 더하고, `config` 옵션의 help를 바꾸고, 본문을 교체한다.

```python
@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="버전을 출력하고 종료합니다.",
        ),
    ] = False,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            # `--help`로 출력되는 문자열이므로 em dash를 쓰지 않는다(전역 제약).
            help="설정 파일 경로 (FR-8.4). 기본은 현재 디렉터리의 ./cuesift.yaml입니다. "
            "CLI 인자가 설정 파일보다 우선합니다.",
        ),
    ] = None,
) -> None:
    """공통 옵션."""
    _harden_output_streams()
    _apply_config(ctx, config)


def _apply_config(ctx: typer.Context, config: Path | None) -> None:
    """설정 파일을 읽어 `ctx`에 싣는다 (FR-8.4 · 설계 §4.2).

    **`_harden_output_streams()` 뒤여야 한다.** 출처 줄에 사용자가 준 경로가
    그대로 실리므로, 하드닝 전에 쓰면 cp949로 인코딩할 수 없는 경로에서
    `UnicodeEncodeError`가 나고 종료 코드 1("규격 위반 발견")로 오보된다.

    **`typer.BadParameter`로 던지는 이유**는 `--spec`의 선례를 따르기
    때문이다(설계 D10) - 설정 파일은 명령줄의 연장이므로 종료 코드가 2다.
    """
    if config is None:
        candidate = Path(_DEFAULT_CONFIG_NAME)
        if not candidate.is_file():
            # 자동 탐색은 없으면 조용히 넘어간다. 이것이 정상 경로다.
            return
        source = candidate
    else:
        if not config.is_file():
            raise typer.BadParameter(f"{config}: 설정 파일이 없다", param_hint="--config")
        source = config

    try:
        cfg: Config = load_config(source)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--config") from exc

    try:
        ctx.default_map = cfg.to_default_map()
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--config") from exc
    ctx.obj = cfg

    # **출처를 낸다**(설계 D7). click의 오류 메시지가 `Invalid value for
    # '--review-format'`이라 그 옵션을 친 적 없는 사용자가 명령줄을
    # 노려보게 된다. 자동 탐색이 있으면 특히 필요하다.
    _echo(f"설정을 읽었다: {source}", err=True)
```

**`_apply_config`의 옛 경고 블록을 지운다.** `main` 본문에 있던 `if config is not None: _echo("경고: --config는 아직 구현되지 않았습니다 ...")`가 그것이다.

- [x] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_config.py -v`
Expected: PASS 11건

- [x] **Step 5: 기존 테스트가 깨지지 않았는지 본다**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 1379 + 신규 통과. **`--config` 경고문을 단언하던 기존 테스트가 있으면 이번에 고친다** — 검색: `git grep -n "아직 구현되지" tests/`

- [x] **Step 6: 커밋**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/cli.py tests/test_cli_config.py
git commit -m "기능: FR-8.4 설정 파일을 ctx.default_map에 실어 CLI 우선순위를 연다

우선순위 해결 코드를 쓰지 않는다. click이 파라미터 단위로
COMMANDLINE > DEFAULT_MAP > DEFAULT를 해결한다.

자동 탐색은 현재 디렉터리 한 칸이다. 상위로 올라가면 사용자가 존재를
모르는 파일이 검수 기준을 바꾼다.

설정을 읽으면 출처를 stderr에 낸다. click의 오류 메시지가 옵션 이름을
가리켜서, 그 옵션을 친 적 없는 사용자가 명령줄을 노려보게 된다.

--config의 '아직 구현되지 않았습니다' 경고와 help 문구를 지웠다.
남겨 두면 이번에는 반대 방향의 거짓말이 된다."
```

---

### Task 5: 환경변수 양보 — `CLI > ENV > 설정 파일`

**Files:**

- Modify: `src/cuesift/cli.py` (`_resolve_llm`, 호출부 포함)
- Test: `tests/test_cli_config.py`

**Interfaces:**

- Consumes: Task 4의 `ctx`
- Produces: `_resolve_llm(ctx: typer.Context | None, base_url: str | None, model: str | None) -> tuple[str, str, str | None]` — **첫 인자가 늘었다**

- [x] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cli_config.py`에 덧붙인다:

```python
class _FakeCtx:
    """`get_parameter_source`만 흉내 낸다. 값의 출처를 고정해 준다."""

    def __init__(self, source_name: str) -> None:
        self._source_name = source_name

    def get_parameter_source(self, name: str) -> object:
        return type("Src", (), {"name": self._source_name})()


def test_설정보다_환경변수가_우선한다(monkeypatch) -> None:
    # 설계 D3 - CLI > 환경변수 > 설정 파일.
    # `_resolve_llm`의 `base_url or os.environ.get(...)`을 그대로 두면
    # 설정 파일이 환경변수를 이긴다. 값은 둘 다 나오고 종료 코드는 0이라
    # 이 테스트가 없으면 절대 드러나지 않는다.
    from cuesift.cli import _resolve_llm

    monkeypatch.setenv("CUESIFT_BASE_URL", "http://env")
    monkeypatch.setenv("CUESIFT_MODEL", "m")
    base, _model, _key = _resolve_llm(_FakeCtx("DEFAULT_MAP"), "http://config", None)
    assert base == "http://env"


def test_CLI가_환경변수를_이긴다(monkeypatch) -> None:
    from cuesift.cli import _resolve_llm

    monkeypatch.setenv("CUESIFT_BASE_URL", "http://env")
    monkeypatch.setenv("CUESIFT_MODEL", "m")
    base, _model, _key = _resolve_llm(_FakeCtx("COMMANDLINE"), "http://cli", None)
    assert base == "http://cli"


def test_환경변수가_없으면_설정을_쓴다(monkeypatch) -> None:
    from cuesift.cli import _resolve_llm

    monkeypatch.delenv("CUESIFT_BASE_URL", raising=False)
    monkeypatch.setenv("CUESIFT_MODEL", "m")
    base, _model, _key = _resolve_llm(_FakeCtx("DEFAULT_MAP"), "http://config", None)
    assert base == "http://config"


def test_ctx가_없어도_동작한다(monkeypatch) -> None:
    # 기존 호출부와의 호환. ctx를 모르면 설정에서 온 값이 아니라고 본다.
    from cuesift.cli import _resolve_llm

    monkeypatch.setenv("CUESIFT_BASE_URL", "http://env")
    monkeypatch.setenv("CUESIFT_MODEL", "m")
    base, _model, _key = _resolve_llm(None, "http://cli", None)
    assert base == "http://cli"
```

- [x] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_config.py -k 환경변수 -v`
Expected: FAIL — `_resolve_llm() takes 2 positional arguments but 3 were given`

- [x] **Step 3: `_resolve_llm`을 고친다**

```python
def _from_config(ctx: typer.Context | None, name: str) -> bool:
    """이 파라미터의 값이 설정 파일에서 왔는가 (설계 D3).

    **`typer._click`을 import하지 않는다.** 벤더링된 private 경로라
    typer 업그레이드가 위치를 바꾼다. `ParameterSource`는 이름 문자열로
    판정한다.
    """
    if ctx is None:
        return False
    try:
        source = ctx.get_parameter_source(name)
    except (AttributeError, KeyError):
        return False
    return getattr(source, "name", "") == "DEFAULT_MAP"


def _prefer_env(
    ctx: typer.Context | None, name: str, value: str | None, env_name: str
) -> str | None:
    """우선순위를 적용한다 - CLI > 환경변수 > 설정 파일 (설계 D3).

    `value or os.environ.get(...)`만 쓰면 설정 파일이 환경변수를 이긴다.
    `value`가 어디서 왔는지를 `ctx`가 알려 준다.
    """
    env = os.environ.get(env_name)
    if env and _from_config(ctx, name):
        return env
    return value or env
```

`_resolve_llm`의 시그니처와 첫 두 줄을 바꾼다:

```python
def _resolve_llm(
    ctx: typer.Context | None, base_url: str | None, model: str | None
) -> tuple[str, str, str | None]:
```

```python
    resolved_base = _prefer_env(ctx, "base_url", base_url, "CUESIFT_BASE_URL")
    resolved_model = _prefer_env(ctx, "model", model, "CUESIFT_MODEL")
```

독스트링의 "FR-8.4(`cuesift.yaml`)가 오면 환경변수 아래에 한 칸이 더 낀다"를 현재형으로 고친다: "우선순위는 **CLI 옵션 > 환경변수 > 설정 파일**이다."

`_resolve_llm` 호출부를 전부 찾아 `ctx`를 넘긴다. `translate` 시그니처의 첫 인자로 `ctx: typer.Context`를 더해야 한다.

Run: `git grep -n "_resolve_llm(" src/`

- [x] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_config.py -v && .venv/Scripts/python.exe -m pytest -q`
Expected: 전부 PASS

- [x] **Step 5: 게이트를 실패시켜 본다**

`_prefer_env`의 본문을 `return value or os.environ.get(env_name)`으로 되돌리고 돌린다.

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_config.py::test_설정보다_환경변수가_우선한다 -v`
Expected: FAIL — `assert 'http://config' == 'http://env'`. **확인한 뒤 되돌린다.**

- [x] **Step 6: 커밋**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/cli.py tests/test_cli_config.py
git commit -m "수정: FR-8.4 설정 파일이 환경변수를 이기지 않게 한다

_resolve_llm의 'base_url or os.environ.get(...)'은 default_map이 채운 값을
왼쪽에서 참으로 만들어 환경변수를 이긴다. 선언된 순서는
CLI > 환경변수 > 설정 파일이다.

get_parameter_source가 DEFAULT_MAP인지를 보고 양보시킨다. 값은 어느 쪽이
이기든 나오고 종료 코드는 0이라 이 테스트가 없으면 드러나지 않는다.
실제로 되돌려서 실패하는 것을 확인했다."
```

---

### Task 6: 가중치 통로 — Tier 0 경로

**Files:**

- Modify: `src/cuesift/cli.py` (`_run_triage` · `translate` 호출부)
- Test: `tests/test_cli_tier1.py`

**Interfaces:**

- Consumes: Task 2의 `Config.weights`, Task 4의 `ctx.obj`
- Produces: `_run_triage(..., weights: Mapping[str, float] | None = None)` — 키워드 인자가 하나 늘었다

- [x] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cli_tier1.py` 끝에 덧붙인다. 이 파일의 기존 헬퍼 `_full_args` ·
`_patch_provider` · `_clean_echo` · `_TIER1_RUNS`를 그대로 쓴다.

```python
def _write_weights_config(tmp_path: Path) -> Path:
    """가중치만 담은 설정 파일. 다른 키를 넣지 않는 것이 중요하다 -
    넣으면 이 테스트가 무엇 때문에 통과했는지 알 수 없어진다."""
    path = tmp_path / "cuesift.yaml"
    path.write_text(
        "signals:\n  weights:\n    spec.violation: 0.3\n", encoding="utf-8"
    )
    return path


def _spy_fuse(monkeypatch: pytest.MonkeyPatch, module: object) -> list[object]:
    """`module.fuse`가 받은 `weights`를 순서대로 모은다."""
    본 것: list[object] = []
    진짜 = module.fuse

    def spy(segment_id: str, signals: Sequence[object], weights: object = None) -> object:
        본 것.append(weights)
        return 진짜(segment_id, signals, weights)

    monkeypatch.setattr(module, "fuse", spy)
    return 본 것


def test_설정_가중치가_Tier0_융합에_도달한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-8.4 · FR-6.1 - `_run_triage`가 weights를 넘기는지 본다.

    **`assert 본 것`을 먼저 둔다.** 호출이 0건이면 아래 `all(...)`이 공허참이
    되어 통로를 통째로 지워도 초록이 된다.
    """
    cfg = _write_weights_config(tmp_path)
    본 것 = _spy_fuse(monkeypatch, cli_module)
    _patch_provider(monkeypatch, _clean_echo())

    result = runner.invoke(app, ["--config", str(cfg), *_full_args(tmp_path)])

    assert result.exit_code == 0, result.output
    assert 본 것, "fuse가 한 번도 불리지 않았다 - 이 테스트는 아무것도 검사하지 않는다"
    assert all(w is not None and w["spec.violation"] == 0.3 for w in 본 것)


def test_설정이_없으면_기본_가중치를_쓴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """반대 방향. `weights=`를 무조건 딕셔너리로 채우는 구현을 막는다."""
    본 것 = _spy_fuse(monkeypatch, cli_module)
    _patch_provider(monkeypatch, _clean_echo())

    result = runner.invoke(app, _full_args(tmp_path))

    assert result.exit_code == 0, result.output
    assert 본 것
    assert all(w is None for w in 본 것)
```

> **실행자 주의:** `_full_args`는 `["translate", ...]`로 시작하므로
> `--config`는 그 **앞**에 온다 (그룹 옵션이다). `_full_args`가 이미
> `--review-budget 10%`를 넣으므로 트리아지가 실제로 돈다 — `--dry-run`을
> 쓰지 않는 이유가 이것이다.
>
> `cli_module`·`runner`·`pytest`·`Sequence`·`Path`는 이 파일에 이미
> import돼 있다. 없으면 더한다.

- [x] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_tier1.py -k 가중치 -v`
Expected: FAIL — `weights`가 `None`이다

- [x] **Step 3: 통로를 연다**

`_run_triage` 시그니처에 더한다:

```python
    weights: Mapping[str, float] | None = None,
```

독스트링에 한 줄 더한다:

```python
    **`weights`는 설정 파일에서만 온다**(FR-8.4 · FR-6.1). CLI 옵션이 없는
    것은 10개 실수를 명령줄에 쓰는 것이 쓸모없기 때문이다(설계 D6).
```

`fuse` 호출을 고친다:

```python
    risks = [fuse(seg.id, signals[seg.id], weights) for seg in kept]
```

`cli.py`에 헬퍼를 더한다:

```python
def _config_weights(ctx: typer.Context | None) -> Mapping[str, float] | None:
    """설정 파일의 가중치를 꺼낸다 (FR-8.4 · 설계 D6).

    `ctx.obj`는 `_apply_config`가 심는다. 설정이 없으면 `None`이고
    `fuse`가 `DEFAULT_WEIGHTS`를 쓴다.
    """
    cfg = getattr(ctx, "obj", None)
    return getattr(cfg, "weights", None)
```

`translate` 시그니처의 첫 인자로 `ctx: typer.Context`를 더하고(Task 5에서 이미 더했으면 그대로 쓴다), `_run_triage(...)` 호출에 `weights=_config_weights(ctx)`를 넘긴다.

- [x] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_tier1.py -v`
Expected: PASS

- [x] **Step 5: 커밋**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/cli.py tests/test_cli_tier1.py
git commit -m "기능: FR-8.4 설정 가중치를 Tier 0 융합에 전달한다

FR-6.1의 '설정 가능한 가중치'가 기다리던 통로다. fuse()는 이미 weights를
받고 있었고 호출부가 넘기지 않았을 뿐이다.

CLI 옵션을 만들지 않는다. 10개 실수를 명령줄에 쓰는 것은 쓸모가 없고,
만들면 '설정 파일 전용 값'이라는 범주가 사라져 v0.2의 QE 가중치도 갈 곳이
없어진다."
```

---

### Task 7: 가중치 통로 — Tier 1 경로 2곳

**Files:**

- Modify: `src/cuesift/tier1.py` (`triage_with_tier1`, `fuse` 호출 2곳)
- Modify: `src/cuesift/cli.py` (`_run_triage`가 `triage_with_tier1`에 전달)
- Test: `tests/test_cli_tier1.py`

**Interfaces:**

- Consumes: Task 6의 `weights` · `_write_weights_config` · `_spy_fuse`
- Produces: `triage_with_tier1(..., weights: Mapping[str, float] | None = None)`

- [x] **Step 1: 실패하는 테스트를 쓴다**

Task 6이 만든 헬퍼 둘을 그대로 쓴다. 다른 것은 `_spy_fuse`의 대상 모듈이
`cli_module`이 아니라 `tier1_module`이라는 것뿐이다.

```python
def test_Tier1_경로의_융합_두_곳도_설정_가중치를_쓴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """설계 §4.3 ② - `tier1.py`의 `fuse` 호출 2곳.

    **`--tier1` 없이 통과하는 테스트만 쓰면 이 두 줄을 빼먹어도 전부
    초록이다.** 그중 재점수 지점(⑥)이 특히 위험하다 - 사용자 가중치로 고른
    후보를 기본 가중치로 다시 세우게 되어, 가중치를 설정한 사용자에게만
    순위가 어긋난다.

    **호출이 2회 이상인 것을 함께 단언한다.** 1회면 ②만 돌고 ⑥에 닿지 않은
    것이며(후보 0건의 조기 반환), 그 상태로는 ⑥의 누락을 잡지 못한다.
    """
    from cuesift import tier1 as tier1_module

    cfg = _write_weights_config(tmp_path)
    fake = _clean_echo()
    본 것 = _spy_fuse(monkeypatch, tier1_module)
    _patch_provider(monkeypatch, fake)

    result = runner.invoke(
        app,
        [
            "--config",
            str(cfg),
            *_full_args(tmp_path, "--tier1", *_TIER1_RUNS),
        ],
    )

    assert result.exit_code == 0, result.output
    _assert_tier1_ran(fake)
    assert len(본 것) >= 2, f"융합이 {len(본 것)}회만 돌았다 - 재점수(⑥)에 닿지 않았다"
    assert all(w is not None and w["spec.violation"] == 0.3 for w in 본 것)
```

> **실행자 주의:** `_assert_tier1_ran(fake)`는 Tier 1이 실제로 돌았음을 못
> 박는다. 이것이 없으면 후보 0건으로 조기 반환한 실행에서도 위 단언이
> 성립해 버린다 — 이 파일의 기존 테스트들이 같은 이유로 그 헬퍼를 쓴다.

- [x] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_tier1.py -k Tier1_경로의 -v`
Expected: FAIL — `unexpected keyword argument 'weights'`

- [x] **Step 3: `tier1.py`를 고친다**

`triage_with_tier1` 시그니처에 더한다:

```python
    weights: Mapping[str, float] | None = None,
```

`Mapping`을 import에 더한다: `from collections.abc import Callable, Collection, Mapping, Sequence`

`fuse` 호출 2곳을 고친다.

```python
    risks = [fuse(seg.id, tier0[seg.id], weights) for seg in kept]
```

```python
    rescored = [fuse(seg.id, tier0[seg.id] + tier1.get(seg.id, []), weights) for seg in kept]
```

독스트링에 한 줄 더한다:

```python
    **`weights`는 두 `fuse` 호출에 모두 간다**(FR-8.4 · 설계 §4.3 ②).
    ②만 넘기고 ⑥을 두면 사용자 가중치로 고른 후보를 기본 가중치로 다시
    세우게 되어, 가중치를 설정한 사용자에게만 순위가 어긋난다.
```

`cli.py`의 `_run_triage`에서 `triage_with_tier1(...)` 호출에 `weights=weights`를 더한다.

- [x] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 전부 PASS

- [x] **Step 5: 게이트를 실패시켜 본다**

`rescored` 줄의 `weights`만 지우고 돌린다 (②는 남긴다).

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_tier1.py -k Tier1_경로의 -v`
Expected: FAIL. **이것이 이 태스크의 핵심이다** — ②만 고치고 ⑥을 두는 것이 가장 흔한 누락이다. **확인한 뒤 되돌린다.**

- [x] **Step 6: 커밋**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
git add src/cuesift/tier1.py src/cuesift/cli.py tests/
git commit -m "기능: FR-8.4 설정 가중치를 Tier 1 경로의 융합 두 곳에 전달한다

fuse 호출부가 셋인데 tier1.py의 둘을 빼먹으면 --tier1 유무로 순위가
달라진다. 특히 재점수 지점을 빼먹으면 사용자 가중치로 고른 후보를 기본
가중치로 다시 세우게 되어, 가중치를 설정한 사용자에게만 결과가 어긋난다.

재점수 쪽만 지워서 실제로 실패하는 것을 확인했다."
```

---

### Task 8: 요구사항정의서 §8.2 정정과 문서 갱신

**Files:**

- Modify: `docs/요구사항정의서.md` (§8.2 · FR-6.1 · FR-4.3 상태 칸)
- Modify: `docs/WBS.md` (WP6 행 · "다음 작업 순서")
- Modify: `CHANGELOG.md`

- [x] **Step 1: §8.2 예시를 다시 쓴다**

정정 5건이다 (설계 §7).

```yaml
source_lang: ko
targets: [en, ja]
dry_run: false

llm:
  provider: openai-compatible   # v0.1은 이 값만 지원한다 (Q3)
  base_url: http://localhost:11434/v1
  model: <모델명>
  context_window: 3             # 앞뒤 세그먼트 수

glossary: ./glossary.yaml
work_context: "다큐멘터리, 존댓말"

output:
  dir: ./out

cache:
  enabled: true                 # false면 --no-cache와 같다
  dir: ./.cuesift-cache

signals:
  tier1:
    enabled: true
    max_ratio: 0.25             # 전체의 25%까지만 Tier1 적용 (비용 상한)
    samples: 3
    temperature: 1.0
  weights:                      # 키는 review.json의 signals[].name과 같다
    struct.untranslated: 1.0
    struct.empty: 1.0
    struct.degeneration: 1.0
    struct.number_missing: 1.0
    struct.tag_lost: 1.0
    spec.violation: 1.0
    spec.overlap: 1.0
    glossary.miss: 1.0
    length.ratio: 1.0
    llm.self_consistency: 1.0

triage:
  review_budget: "10%"          # 또는 review_threshold: 0.7

review:
  out: ./review
  format: json                  # json | html | both

spec:
  profile: ko                   # 내장 이름 또는 ./my-spec.yaml
  fail_on: hard
  limit: 0
```

**표 하나를 아래에 붙인다** — 우선순위와 미지 키 정책이 예시만으로는 드러나지 않는다.

| 항목 | 규칙 |
| --- | --- |
| 우선순위 | CLI 인자 > 환경변수 > `cuesift.yaml` > 기본값 |
| 자동 탐색 | 현재 디렉터리의 `./cuesift.yaml`만. 상위로 올라가지 않는다 |
| 모르는 키 | 종료 코드 2로 거부하고 가까운 키를 제시한다 |
| 부분 지정 | 명시하지 않은 키는 기본값을 유지한다. `signals.weights`도 같다 |

- [x] **Step 2: FR-6.1과 FR-4.3의 상태 칸을 고친다**

두 칸이 "§8.2의 그 키는 여전히 미구현이다"를 적고 있다. 고치지 않으면 요구사항정의서가 자기 자신과 어긋난다.

- FR-6.1: "가중치를 사용자가 설정하는 통로는 아직 없다" → **`cuesift.yaml`의 `signals.weights`가 열렸다(FR-8.4).** `DEFAULT_WEIGHTS`는 여전히 전부 1.0이고 **튜닝하지 않는다** — 사용자가 바꿀 수 있는 것과 우리가 맞추는 것은 다르다.
- FR-4.3: "§8.2 설정 파일의 `signals.tier1.max_ratio`는 여전히 미구현이다" → **열렸다(FR-8.4).**

- [x] **Step 3: FR-8.4 상태 칸을 ✅로 바꾸고 근거를 적는다**

갱신 규칙상 **근거 커밋을 함께 적는다.** 상태만 바꾸고 근거가 없으면 이 문서는 검증할 수 없는 주장이 된다.

- [x] **Step 4: WBS를 갱신한다**

- 현재 위치 진척: v0.1 완료 **35 → 36** (FR-8.4). WP6은 8.3·8.5가 남아 여전히 🟡
- "30 → 32 → 34 → 35" 산수 표에 한 줄 더한다: `FR-8.4` `+1` — ⬜ → ✅
- WP6 행에 `--config` 배선을 적고 설계 스펙을 링크한다
- "다음 작업 순서"의 1순위를 갱신한다: **FR-8.5**(진행 표시·CI 감지)가 남고, FR-8.3은 WP9 선행이 필요하다는 사실을 명시한다

- [x] **Step 5: CHANGELOG에 적는다**

Keep a Changelog 형식의 `Added`에 넣는다.

- [x] **Step 6: 문서 게이트를 돌린다**

```bash
git add -A docs/ CHANGELOG.md
npx --yes markdownlint-cli2
.venv/Scripts/python.exe scripts/check_links.py
```

**두 도구의 파일 개수가 같은지 본다.** 다르면 새 문서가 `git add`되지 않은 것이고, 그 문서는 링크 검사를 아예 받지 않는다.

- [x] **Step 7: 커밋**

```bash
git commit -m "문서: FR-8.4를 닫고 §8.2 예시를 CLI 옵션 23개에 맞춘다

정정 5건 - consistency_n을 samples로, risk_threshold를 review_threshold로,
profiles_dir을 spec.profile로 고치고, 가중치 키를 실제 신호 이름 10종으로
바꾸고, 덮이지 않던 12키를 더했다. 세 이름은 CLI에도 review.json에도 없어
사용자가 세 번째 어휘를 배워야 했다.

FR-6.1과 FR-4.3의 상태 칸이 '§8.2의 그 키는 여전히 미구현'을 적고 있어
함께 고쳤다. 둘 다 이미 ✅라 완료 개수는 늘지 않는다.

v0.1 완료 35 -> 36."
```

---

### Task 9: 실물 확인

**Files:**

- Modify: `HANDOFF.md` (실측 결과 기록)

**pytest는 실제 파일시스템의 현재 디렉터리에서 CLI를 띄우지 않는다.** 자동 탐색(D2)이 `CliRunner`가 아니라 진짜 프로세스에서 도는지는 서브프로세스로만 확인된다 — WP7b가 재개를 실물 확인한 것과 같은 이유다.

- [x] **Step 1: 임시 디렉터리에 설정과 자막을 둔다**

```bash
mkdir -p /tmp/cuesift-live && cd /tmp/cuesift-live
cp <리포>/tests/fixtures/<적당한 srt> ./a.ko.srt
```

`cuesift.yaml`을 쓴다.

```yaml
source_lang: ko
targets: [en]
llm:
  base_url: http://localhost:11434/v1
  model: qwen2.5:3b
triage:
  review_budget: "10%"
signals:
  weights:
    spec.violation: 0.3
```

- [x] **Step 2: 옵션 없이 실행한다**

```bash
<리포>/.venv/Scripts/python.exe -m cuesift translate a.ko.srt --dry-run
```

Expected: stderr 첫 줄에 `설정을 읽었다: cuesift.yaml`, `--to` 없이도 `Missing option`이 나오지 않는다.

- [x] **Step 3: CLI가 이기는지 확인한다**

```bash
<리포>/.venv/Scripts/python.exe -m cuesift translate a.ko.srt --to ja --dry-run
```

Expected: 대상 언어가 `ja`다 (설정의 `en`이 아니다).

- [x] **Step 4: 상위 디렉터리를 읽지 않는지 확인한다**

```bash
mkdir -p sub && cd sub
<리포>/.venv/Scripts/python.exe -m cuesift --version
```

Expected: `설정을 읽었다` 줄이 **나오지 않는다.**

- [x] **Step 5: 모르는 키가 2로 죽는지 확인한다**

```bash
cd /tmp/cuesift-live
printf 'triage:\n  review_budgt: 10%%\n' > bad.yaml
<리포>/.venv/Scripts/python.exe -m cuesift --config bad.yaml check a.ko.srt --spec ko
echo "exit=$?"
```

Expected: `exit=2`, 메시지에 `가까운 키: triage.review_budget`

- [x] **Step 6: 결과를 `HANDOFF.md`에 적는다**

**값을 옮겨 적지 말고 실행한 명령과 관측한 것을 적는다.**

- [x] **Step 7: 최종 게이트와 커밋**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

**개수를 읽는다.** pytest의 수집 개수, markdownlint의 `Linting: N files`, 링크 체커의 상대 링크 개수. 0개 수집은 통과가 아니라 설정 오류다.

**로컬과 CI의 게이트 수치는 다르다** — `data/`가 gitignore라 bench 테스트가 CI에서만 skip된다. `passed`만 보면 1건 어긋난다.

```bash
git add -A
git commit -m "문서: FR-8.4 실물 확인 - 자동 탐색과 우선순위를 서브프로세스로 검증"
```

---

## 구현 중 바뀐 결정

**이 절이 위 본문의 코드 블록보다 최신이다.** 계획은 실행되기 전에 쓰였고, 아래는 실행이 가르쳐 준 것이다.

### 계획서가 틀렸던 곳

| # | 계획서 | 실제 | 어떻게 처리했나 |
| --- | --- | --- | --- |
| 1 | Task 3 Step 4가 "PASS 24건" 예고 | 매핑 전수 parametrize는 **23건** (22행 중 `source_lang`이 두 커맨드로 뿌려져 23) | 설계 §5의 산수가 옳다. 테스트를 고치지 않고 계획서 숫자만 낡은 것으로 판정 |
| 2 | Task 6이 `translate`에서 `weights=`를 넘기라고 지시 | `_run_triage` 호출부는 **`_translate_one`** 안에 있다 | `_translate_one`에 `weights` 키워드를 하나 더 뚫어 전달 |
| 3 | 파이썬 식별자 `본 것` | 공백이 들어가 **문법 오류** | `본것`으로 |
| 4 | Task 7 Step 2가 `unexpected keyword argument 'weights'` 실패를 예고 | 진짜 `fuse`가 이미 그 파라미터를 갖고 있어 **수집된 `weights`가 전부 `None`**인 형태로 실패한다 | 예상 문구만 다르고 red는 정상 |
| 5 | `test_진리표_둘_다_있으면_CLI가_이긴다`가 설정과 CLI에 **같은 값**을 넣는다 | 어느 쪽이 이겨도 결과가 같아 **방향을 못 가린다** | 설정 `hard` / CLI `none`으로 뒤집어 exit 0을 단언 |
| 6 | Task 9가 `signals.tier1.max_ratio: 0.25`를 예시로 지시 | `load_config`는 통과하지만 **CLI의 곱 게이트에서 exit 2** | 예시를 `0.05`로 고치고, §8.2 게이트를 로더가 아니라 **CLI까지** 태우도록 넓혔다 |

### 리포가 가르쳐 준 것 (브리프에 없으면 반복해서 걸린다)

| 사실 | 결과 |
| --- | --- |
| ruff **SIM300** — 단언에서 대문자 상수를 왼쪽에 두면 Yoda condition으로 잡힌다 | `assert derived == CONSTANT` 순서로. 집합 상등은 방향이 없어 단언의 힘은 그대로다 |
| `result.output`은 stdout이 아니라 **stdout+stderr 혼합** 스트림(typer 0.27 `StreamMixer`) | D7의 출처 줄이 stdout을 오염시키지 않는지 재려면 `.stdout`/`.stderr`를 나눠야 한다 |
| 부정 단언(`not in`)은 rich의 강제 개행에 **뚫린다** | `normalize_rich_message`를 통과시킨 뒤 단언 |
| conftest 임포트 관례는 `from conftest import ...` | `from tests.conftest`가 아니다 |
| `typer`가 `ctx: typer.Context`를 click 파라미터 목록에서 **제외**한다 | R5 상등 게이트가 그 성질에 의존한다 |

### 이중 리뷰가 찾은 것 — 계획에도 설계에도 없던 결함

| # | 결함 | 왜 계획이 못 봤나 |
| --- | --- | --- |
| **1** | **상호배타 검사가 값의 출처를 안 본다.** 설정에 `review_threshold`가 있으면 CLI `--review-budget`이 exit 2로 죽는다 | D1의 전제는 "click이 우선순위를 해결한다"인데, click의 해결 **뒤에** 도는 검사는 값의 출처를 모른다. P1~P4 탐침이 click의 경계를 짚었지만 **경계 바깥**은 못 봤다 |
| **2** | **자동 탐색(D2)이 테스트 스위트를 cwd에 종속시킨다.** 리포 루트에 `cuesift.yaml` 한 줄이면 81건이 깨진다 | 기능으로서는 정확히 의도대로 동작한다. 문제는 그 기능이 **테스트 하네스와 충돌**한다는 것이고, 설계는 하네스를 보지 않는다 |
| **3** | **`cache.enabled`가 로더·click 양쪽 검증을 빠져나간다.** `"false"`→캐시 켜짐, `null`→캐시 꺼짐 | D5가 "`signals.weights`만 click을 안 거친다"고 했는데, `cache.enabled`는 click을 거치되 `negate()`가 **먼저 먹어치워** 볼 값이 남지 않는다. D5의 예외가 하나 더 있었다 |
| **4** | **`test_틀린_값은_click이_종료_코드_2로_낸다`가 아무것도 검사하지 않았다** | 같은 명령이 `--base-url` 없음으로도 exit 2를 낸다. 종료 코드만 보는 단언의 전형적 실패 |

**1·3은 같은 부류다** — "click에 맡긴다"(D5·D1)의 경계를 실제보다 넓게 그렸다. 맡길 수 있는 것과 없는 것의 선은 탐침으로 확인한 자리(P1~P4)에서만 정확했다.

### 게이트에 대해 알게 된 것

- **`conftest`의 자동 탐색 차단은 인프로세스 전용이다.** `monkeypatch`라 `subprocess`로 CLI를 띄우는 테스트에는 안 듣는다. `test_cli_pipe.py`가 `cwd=`로 닫았고, **앞으로 서브프로세스로 CLI를 띄우는 테스트를 추가하면 같은 조치가 필요하다**
- **스펙 R1의 완화책이 실제보다 넓었다.** "P1~P4의 성질을 테스트가 직접 확인한다"고 적었으나, 실측 결과 **P4의 "검증"은 게이트가 잡고 "변환"은 안 잡는다**. 스펙 §9.1에 정정했다
- `loader.py`의 `RecursionError` 분기는 **도달 가능하다**(`'['*5000`으로 실측). PyYAML이 `YAMLError`로 감싸지 않는다. **테스트를 두지 않는 것은 의도다** — 재귀 한계가 파이썬 버전·플랫폼마다 달라 3.11~3.14 매트릭스에서 간헐 실패하는 게이트가 된다

### 최종 게이트 수치

착수 **1379** → 최종 **1480 passed, 3 deselected** (+101) · 커버리지 **99%** · ruff 2종 통과 · `109 files already formatted` · 링크 체커 35개 문서 175링크 0 broken · markdownlint 35 files 0 issues.

---

## PR

```bash
git push -u origin feat/config-file
gh pr create --base main --title "기능: FR-8.4 cuesift.yaml 설정 파일" --body "..."
gh pr checks --watch
```

PR 본문에는 **무엇을 · 근거 문서 · 게이트 수치**를 담는다. 게이트 수치는 개수를 그대로 적는다.

**`main` 머지는 별도 승인이다.** PR을 자동으로 열어도 머지는 열리지 않는다.
