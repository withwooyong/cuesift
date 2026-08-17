# 트리아지 CLI 배선 구현 계획 (FR-6.3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cuesift translate`에 검수 큐 선별을 배선해 `--review-budget`·`--review-threshold`가
실제로 동작하게 한다.

**Architecture:** 라이브러리(`signals`·`risk`·`triage`)에 이미 있는 것을 CLI에서 호출하는
배선 작업이다. `src/cuesift/` 아래 **라이브러리 코드는 한 줄도 바꾸지 않는다** — 변경이
`cli.py` 하나에 갇힌다. 순수 함수 셋(`_parse_review_budget` · `_run_triage` ·
`_format_triage_summary`)으로 나눠 CLI 없이도 테스트할 수 있게 한다.

**Tech Stack:** Python 3.11+ · Typer · pytest. 의존성 추가 없음.

**Spec:** [설계 문서](../specs/2026-08-18-triage-cli-design.md) — 결정 D1~D13의 근거가
거기 있다. 이 계획은 스펙에서 논증을 가져오지 않고 **참조**한다.

## Global Constraints

- 모든 모듈 첫 줄에 `from __future__ import annotations`
- 독스트링·주석은 **한국어**, 근거 FR·§ 번호를 병기한다 (예: `FR-6.3`, `설계 §5.2`)
- 주석에는 "왜 이 값인가"가 아니라 **"이 값이 아니면 무엇이 깨지는가"**를 적는다
- ruff: `line-length = 100`, 규칙 `E,F,I,UP,B,SIM`
- 커밋 메시지는 **한국어**. 푸시는 하지 않는다 (사용자가 명시적으로 요청할 때만)
- Python 실행은 반드시 `.venv/Scripts/python.exe` (시스템 Python은 3.14로 다르다)
- em dash(U+2014)를 `--help`에 출력되는 문자열에 쓰지 않는다 — cp949가 인코딩하지 못한다.
  `·`(U+00B7)는 쓸 수 있다
- 테스트 이름은 **한국어**로 쓴다 (`test_번역해서_파일을_낸다` 형식)
- 로컬 게이트는 CI와 대상이 같아야 한다. **`src tests`로 좁히지 않는다**:

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

---

## File Structure

| 파일 | 책임 | 변경 |
| --- | --- | --- |
| `src/cuesift/cli.py` | CLI 표면 전부 | **수정** — 옵션 1개 신설, 순수 함수 3개 추가, `_translate_one`에 인자 4개 추가 |
| `tests/test_cli_triage.py` | 트리아지 배선 검증 | **신설** — `test_cli_translate.py`가 이미 102개라 새 관심사는 새 파일에 둔다 |
| `tests/fixtures/ingest/ten_cues.srt` | 세그먼트 10개 픽스처 | **신설** — §9.2 게이트의 산수를 깔끔하게 만든다(14.3% vs 30.0%) |
| `docs/WBS.md` · `docs/요구사항정의서.md` · `HANDOFF.md` · `CHANGELOG.md` | 문서 | **수정** — Task 5 |

**새 모듈을 만들지 않는다.** `cli.py`가 1339줄이라 커지는 것이 걸리지만, 이 파일은 이미
`_dry_run_report`(160줄)·`_translate_one`(160줄)을 담고 있고 저장소 패턴은 "CLI 관련은
`cli.py` 한 곳"이다. 기존 코드베이스의 패턴을 일방적으로 재구성하지 않는다.

---

## Task 1: 예산 값 파싱

**Files:**

- Modify: `src/cuesift/cli.py` (새 함수를 `_resolve_profile` 근처, 다른 `_resolve_*`
  헬퍼와 같은 구역에 둔다)
- Test: `tests/test_cli_triage.py` (신설)

**Interfaces:**

- Consumes: 없음 (순수 함수)
- Produces: `_parse_review_budget(raw: str) -> float` — 실패 시 `ValueError`를 던진다.
  Task 2가 이것을 잡아 `typer.Exit(2)`로 바꾼다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cli_triage.py`를 새로 만든다:

```python
"""`cuesift translate`의 트리아지 배선 검증 (FR-6.3 · 설계 §5·§7).

**네트워크를 타지 않는다.** `_build_provider`를 monkeypatch해 가짜를 꽂는다 -
`test_cli_translate.py`와 같은 방식이다.
"""

from __future__ import annotations

import pytest

from cuesift.cli import _parse_review_budget


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10%", 0.10),
        ("0.1", 0.10),
        ("5%", 0.05),
        ("0", 0.0),
        ("0%", 0.0),
        ("100%", 1.0),
        ("1.0", 1.0),
        # `1`은 100%다. `1%`를 의도한 사용자가 전량을 받지만 Tier 0만 쓰므로
        # LLM 비용이 0이고 요약이 "실제 100.0%"를 내 즉시 드러난다(설계 §5.2).
        ("1", 1.0),
        ("  10%  ", 0.10),
    ],
)
def test_비율을_파싱한다(raw: str, expected: float) -> None:
    assert _parse_review_budget(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw",
    [
        "50",  # 개수 지정 - 범위 밖이다
        "-5%",
        "1.5",
        "101%",
        "abc",
        "",
        "   ",
        "%",
        # NaN·inf를 비교 연산의 우연에 맡기지 않는다. `nan <= 1.0`이 False라
        # 범위 검사에서 거부되는 것이 **의도**이므로 테스트로 못 박는다 -
        # `policy.py`가 같은 부류의 결함(Task 9)을 겪은 전례가 있다.
        "nan",
        "inf",
    ],
)
def test_잘못된_값은_ValueError다(raw: str) -> None:
    with pytest.raises(ValueError):
        _parse_review_budget(raw)


def test_개수를_주면_비율로_지정하라고_안내한다() -> None:
    with pytest.raises(ValueError, match="비율로 지정하라"):
        _parse_review_budget("50")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_triage.py -v`

Expected: FAIL — `ImportError: cannot import name '_parse_review_budget' from 'cuesift.cli'`

- [ ] **Step 3: 최소 구현을 쓴다**

`src/cuesift/cli.py`의 `_resolve_profile` 바로 위에 추가한다:

```python
def _parse_review_budget(raw: str) -> float:
    """`--review-budget` 값을 비율로 바꾼다 (FR-6.3 ① · 설계 §5.2).

    `10%`와 `0.1`을 모두 받는다. **개수 지정(`50`)은 범위 밖으로 거부된다** -
    라이브러리에 개수 기반 선별 함수가 없고, `k/n`으로 환산하면 `ceil`과 hard
    fail 소진 때문에 정확히 K개가 나오지 않아 옵션이 거짓말을 한다(설계 D5).

    **`1`은 100%다.** `%` 유무만 다르고 나머지는 `0.0 <= x <= 1.0` 한 규칙이라
    그 결과다. 규칙을 좁혀(`%` 없는 값에 소수점을 요구해) `1`을 거부하면 `0`도
    함께 막혀 "hard fail만 보기"가 사라진다.

    **NaN·inf는 범위 검사가 거부한다** - `nan <= 1.0`이 False이기 때문이다.
    이것이 우연이 아니라 의도임을 테스트가 못 박고 있다. 이 방어가 없으면
    `select_by_budget`이 `math.isnan`으로 다시 막아 주지만, 그때는 오류
    메시지가 옵션 이름을 말하지 못한다.
    """
    text = raw.strip()
    percent = text.endswith("%")
    number = text[:-1].strip() if percent else text
    try:
        value = float(number) / 100.0 if percent else float(number)
    except ValueError as exc:
        raise ValueError(f"--review-budget을 숫자로 읽지 못했다: {raw!r}") from exc
    if not 0.0 <= value <= 1.0:
        raise ValueError(
            f"--review-budget이 0~100% 범위를 벗어났다: {raw!r}. "
            f"개수 지정은 v0.1 범위 밖이다 - 비율로 지정하라 (예: 10%)"
        )
    return value
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_triage.py -v`

Expected: PASS — 파라미터 케이스 9 + 10 + 1 = **20개 통과**

- [ ] **Step 5: 게이트를 돌린다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
```

Expected: `All checks passed` · `N files already formatted`

- [ ] **Step 6: 커밋**

```bash
git add src/cuesift/cli.py tests/test_cli_triage.py
git commit -m "기능: --review-budget 값 파싱 (FR-6.3 · 설계 §5.2)"
```

---

## Task 2: CLI 표면 — 옵션 신설·상호배타·프로파일 사전 검증

**Files:**

- Modify: `src/cuesift/cli.py`
  - `translate` 시그니처: `review_budget`의 help 갱신 + `review_threshold` 신설
  - `translate` 본문 `485-492`: "경고 후 무시" 코드를 실제 검증으로 교체
- Test: `tests/test_cli_triage.py`

**Interfaces:**

- Consumes: `_parse_review_budget(raw: str) -> float` (Task 1)
- Produces: `translate` 본문의 지역 변수 셋 — Task 3이 `_translate_one`에 넘긴다
  - `budget_ratio: float | None`
  - `review_threshold: float | None` (옵션 값 그대로)
  - `profiles: dict[str, SpecProfile]` — 대상 언어별로 미리 로드한 프로파일
  - `policy_label: str` — 요약에 찍을 라벨. 사용자가 준 원문을 쓴다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cli_triage.py`에 추가한다. 파일 상단의 import를 다음으로 **교체**한다:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from tests.fakes.provider import EchoProvider
from typer.testing import CliRunner

from cuesift.cli import _parse_review_budget, app

_FIXTURES = Path(__file__).parent / "fixtures" / "ingest"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """사용자 환경이 테스트 결과를 바꾸지 못하게 한다."""
    for name in ("CUESIFT_BASE_URL", "CUESIFT_MODEL", "CUESIFT_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: object) -> None:
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: provider)


def _args(tmp_path: Path, fixture: str, *extra: str) -> list[str]:
    return [
        "translate",
        str(_FIXTURES / fixture),
        "--to",
        "en",
        "--out",
        str(tmp_path),
        "--base-url",
        "http://h/v1",
        "--model",
        "m1",
        "--no-cache",
        *extra,
    ]
```

그리고 다음 테스트를 파일 끝에 추가한다:

```python
def test_두_정책을_함께_주면_exit_2다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-threshold",
            "0.7",
        ),
    )

    assert result.exit_code == 2, result.output
    # 두 옵션 이름이 모두 나와야 사용자가 무엇을 지울지 안다.
    assert "--review-budget" in result.output
    assert "--review-threshold" in result.output


def test_예산_파싱_실패는_exit_2다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt", "--review-budget", "50"))

    assert result.exit_code == 2, result.output
    assert "비율로 지정하라" in result.output


def test_프로파일이_없는_언어는_경고하고_건너뛴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D7 — 전량 거부하면 프로파일이 **있는** 언어의 트리아지까지 잃는다.

    요구사항정의서 §8.1 S3의 문서화된 호출이 `--to en,ja,th,vi`인데 th·vi
    프로파일은 없다(`tests/test_cli.py:57-73`이 그것을 exit 0으로 고정한다).
    선례도 있다 - `cli.py:869-877`이 프로바이더가 `cache_identity`를 주지
    않으면 경고하고 캐시를 끈다("조용히 끄지는 않는다").
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en,fr",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--no-cache",
            "--review-budget",
            "10%",
        ],
    )

    assert result.exit_code == 0, result.output
    # load_builtin의 메시지를 그대로 전달한다 - 사용 가능 목록이 거기 있다.
    assert "사용 가능" in result.output
    assert "[fr]" in result.output
    # 프로파일이 있는 언어는 정상 트리아지된다 - 이것이 전량 거부와 갈리는 지점이다.
    assert "[en] 트리아지" in result.output
    assert "[fr] 트리아지" not in result.output


def test_트리아지할_언어가_하나도_없으면_exit_2다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling 3a + D13 — 요청이 통째로 무시되는 경우만 사용법 오류다.

    **프로바이더 호출 0회를 단언하는 것이 이 테스트의 요점이다.** exit 2만
    보면 "언제" 죽었는지 알 수 없어, LLM 비용을 쓴 뒤 죽는 구현도 통과한다.
    """
    provider = EchoProvider()
    _patch_provider(monkeypatch, provider)

    # `_args`를 쓰지 않는다 - 그것은 `--to en`을 주므로 프로파일이 존재해
    # 번역이 실제로 돌고, 그러면 `provider.calls == []` 단언이 무의미해진다.
    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "fr",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--no-cache",
            "--review-budget",
            "10%",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "적용할 수 있는 대상 언어가 없다" in result.output
    assert provider.calls == [], "프로파일 검증 전에 번역을 호출했다"


def test_정책이_없으면_기존_동작이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """하위 호환 - 두 옵션이 없으면 트리아지가 돌지 않는다."""
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt"))

    assert result.exit_code == 0, result.output
    assert "트리아지" not in result.output
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_triage.py -v -k "exit_2 or 기존_동작"`

Expected: FAIL — `--review-threshold` 옵션이 없어 `exit_code == 2`가 나오지만 이유가
다르다(Typer가 "No such option"). `test_프로파일이_없는_언어는`은 `provider.calls == []`
단언에서 실패한다(현재는 번역이 먼저 돈다).

**실패 이유를 반드시 눈으로 확인한다** — "우연히 exit 2"가 아니라 우리가 의도한 검증에서
실패해야 한다. `result.output`을 출력해 확인한다.

- [ ] **Step 3: import를 추가한다**

`src/cuesift/cli.py`의 `from cuesift.spec import (...)` 블록은 이미 `load_builtin`을
포함하므로 건드리지 않는다. 파일 상단 import에 다음을 추가한다:

```python
from collections import Counter
```

`cuesift.risk`·`cuesift.signals`·`cuesift.triage`·`cuesift.segment` import는 Task 3에서
추가한다 — 이 태스크는 아직 그것들을 쓰지 않는다.

- [ ] **Step 4: 옵션을 신설하고 help를 갱신한다**

`src/cuesift/cli.py:464-467`의 `review_budget`을 다음으로 교체한다:

```python
    review_budget: Annotated[
        str | None,
        typer.Option(
            "--review-budget",
            # em dash(U+2014)를 쓰지 않는다(전역 제약, cp949 미인코딩).
            help="사람이 검수할 상위 비율 (예: 10% 또는 0.1). "
            "--review-threshold와 함께 쓸 수 없다",
        ),
    ] = None,
    review_threshold: Annotated[
        float | None,
        typer.Option(
            "--review-threshold",
            min=0.0,
            max=1.0,
            # 라이브러리(`policy.py`)도 범위를 검사하지만 여기서 막으면 오류
            # 메시지가 옵션 이름을 말한다 - `--context-window`·`--limit`이
            # 이미 같은 패턴이다.
            help="이 위험도 이상을 검수 큐에 담는다 (0.0~1.0). "
            "--review-budget과 함께 쓸 수 없다",
        ),
    ] = None,
```

- [ ] **Step 5: 경고 코드를 검증으로 교체한다**

`src/cuesift/cli.py:485-492`의 경고 블록을 다음으로 교체한다:

```python
    if review_budget is not None and review_threshold is not None:
        # FR-6.3은 "두 방식으로 지정할 수 있다"이지 "동시에"가 아니다.
        # 합성하면 어느 쪽이 이겼는지가 출력에서 사라진다(설계 D4).
        _echo("--review-budget과 --review-threshold는 함께 쓸 수 없다", err=True)
        raise typer.Exit(2)

    budget_ratio: float | None = None
    if review_budget is not None:
        try:
            budget_ratio = _parse_review_budget(review_budget)
        except ValueError as exc:
            _echo(str(exc), err=True)
            raise typer.Exit(2) from exc

    # 사용자가 준 원문을 라벨에 쓴다 - 파싱 결과(`0.1`)를 찍으면 `10%`라고
    # 쓴 사람이 자기 입력을 화면에서 못 찾는다. 이해가 맞았는지는 별도로
    # 출력되는 "실제 N%"가 말한다.
    policy_label = (
        f"예산 {review_budget}" if review_budget is not None else f"임계값 {review_threshold}"
    )
```

- [ ] **Step 6: 프로파일 사전 검증을 넣는다**

`targets` 검증(`cli.py:498-503`의 `invalid` 블록) **직후**, `load_subtitle` 호출 전에
다음을 추가한다:

```python
    triage_requested = review_budget is not None or review_threshold is not None
    profiles: dict[str, SpecProfile] = {}
    if triage_requested:
        # **모든 대상 언어를 여기서 검사한다 - 루프 안에서 하지 않는다.**
        # 루프 안에서만 보면 `--to en,ja,fr`이 en·ja의 LLM 비용을 실제로 쓴
        # 뒤 fr에서 exit 2를 낸다(설계 D13). `--dry-run`의 용어집 검사가
        # 이미 같은 이유로 `targets[0]`이 아니라 전량을 본다(위 주석 참고).
        #
        # 프로파일은 **대상 언어**의 규격이다 - `check --spec ko`(검사 대상
        # 자막의 규격)와 이름이 같아도 다른 것이다(설계 §3.2). 신호 2종
        # (`spec.violation`·`length.ratio`)이 번역문에 이것을 적용한다.
        for target in targets:
            try:
                profiles[target] = load_builtin(target)
            except (OSError, ValueError) as exc:
                # **경고하고 그 언어만 건너뛴다 - 전량 거부하지 않는다**(D7).
                # 전량 거부는 프로파일이 **있는** 언어의 트리아지까지 잃게 하고,
                # 요구사항정의서 §8.1 S3의 문서화된 호출
                # (`--to en,ja,th,vi --review-budget 10%`)을 깨뜨린다 -
                # th·vi 프로파일이 없고 `tests/test_cli.py:57-73`이 그것을
                # exit 0으로 고정하고 있다. 선례는 `cli.py:869-877`이다:
                # 프로바이더가 `cache_identity`를 주지 않으면 경고하고 캐시를
                # 끈다("끄는 쪽이 안전하고, 조용히 끄지는 않는다").
                #
                # `load_builtin`의 메시지가 이미 사용 가능 목록을 담으므로
                # (`spec/profile.py:177-180`) 새로 쓰지 않고 전달한다.
                # `[target]` 라벨만 붙인다: 이 함수의 다른 `_echo`들이 전부
                # 그렇게 하고, 언어가 여러 개일 때 어느 언어인지 구별해야
                # 한다(`cli.py:883-889`가 라벨 누락으로 겪은 문제).
                _echo(f"[{target}] 경고: 규격 프로파일이 없어 트리아지를 건너뛴다 - {exc}", err=True)

        if not profiles:
            # **한 언어도 못 돌면 요청이 통째로 무시된 것이다.** 경고만 내고
            # exit 0으로 끝나면 CI가 "트리아지했다"로 읽는다. 하나라도 돌면
            # 부분 적용이고 어느 언어가 빠졌는지는 위 경고가 말한다.
            _echo("트리아지를 적용할 수 있는 대상 언어가 없다", err=True)
            raise typer.Exit(2)
```

- [ ] **Step 7: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_triage.py -v`

Expected: PASS — 20 + 5 = **25개 통과**

- [ ] **Step 8: 경고를 단언하던 기존 테스트를 고친다**

**정확한 위치를 이미 확인했다.** `tests/test_cli_translate.py:324`의
`test_review_budget은_경고한다`가 `assert "review-budget" in result.output`으로 경고 문구를
단언한다. 경고를 없앤 것이 이 태스크의 목적이므로 이 테스트가 죽는다.

**삭제하지 않고 고쳐 쓴다.** 원래 의도("조용한 무시를 금지한다")는 여전히 유효하고,
경고가 아니라 동작으로 바뀌었을 뿐이다:

```python
def test_review_budget이_실제로_트리아지한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 조용한 무시는 이 저장소가 1급으로 금지한 것이다 (--config 선례).
    # WP8b 전에는 경고만 냈고, 이제는 실제로 트리아지한다.
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--review-budget", "10%"])

    assert result.exit_code == 0, result.output
    assert "트리아지" in result.output
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_translate.py tests/test_cli.py -v 2>&1 | tail -5`

Expected: 전부 통과. **`tests/test_cli.py:57-73`이 특히 중요하다** —
`--to en,ja,th,vi --review-budget 10%`로 exit 0을 단언하는 요구사항정의서 §8.1 S3의
문서화된 호출이고, th·vi 프로파일이 없다. D7이 경고+건너뛰기이므로 이 테스트는 **수정 없이
통과해야 한다.** 죽으면 Step 6의 구현이 전량 거부로 되어 있다는 뜻이다.

- [ ] **Step 9: 게이트를 돌린다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing 2>&1 | tail -20
```

Expected: 전체 통과. **수집 개수를 기록한다** — 0개 수집은 통과가 아니라 설정 오류다.

- [ ] **Step 10: 커밋**

```bash
git add src/cuesift/cli.py tests/test_cli_triage.py
git commit -m "기능: --review-threshold 신설·상호배타·프로파일 사전 검증 (FR-6.3 · D4·D13)"
```

---

## Task 3: 트리아지 실행과 요약 출력

**Files:**

- Create: `tests/fixtures/ingest/ten_cues.srt`
- Modify: `src/cuesift/cli.py`
  - `_run_triage` · `_format_triage_summary` 신설 (`_format_translate_summary` 뒤에 둔다)
  - `_translate_one` 시그니처에 인자 4개 추가
  - `_translate_one` 본문 끝(요약 출력 뒤)에 트리아지 호출
  - `translate` 본문의 `_translate_one` 호출에 인자 4개 전달
- Test: `tests/test_cli_triage.py`

**Interfaces:**

- Consumes: Task 2가 만든 `budget_ratio`·`review_threshold`·`profiles`·`policy_label`
- Produces:
  - `_run_triage(*, target_lang: str, profile: SpecProfile, glossary: Glossary | None,
    source_lang: str, translated: TranslationResult, budget_ratio: float | None,
    threshold: float | None, policy_label: str) -> list[str]`
  - `_format_triage_summary(*, target_lang: str, policy_label: str,
    risks: Sequence[SegmentRisk], excluded: int) -> list[str]`

- [ ] **Step 1: 픽스처를 만든다**

`tests/fixtures/ingest/ten_cues.srt` — 세그먼트 10개. 각 큐는 2초이고 겹치지 않는다
(겹침 신호가 섞이면 §9.2 게이트의 "신호별 적발" 단언이 흐려진다):

```text
1
00:00:01,000 --> 00:00:03,000
첫째 줄입니다

2
00:00:04,000 --> 00:00:06,000
둘째 줄입니다

3
00:00:07,000 --> 00:00:09,000
셋째 줄입니다

4
00:00:10,000 --> 00:00:12,000
넷째 줄입니다

5
00:00:13,000 --> 00:00:15,000
다섯째 줄입니다

6
00:00:16,000 --> 00:00:18,000
여섯째 줄입니다

7
00:00:19,000 --> 00:00:21,000
일곱째 줄입니다

8
00:00:22,000 --> 00:00:24,000
여덟째 줄입니다

9
00:00:25,000 --> 00:00:27,000
아홉째 줄입니다

10
00:00:28,000 --> 00:00:30,000
열째 줄입니다
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`tests/test_cli_triage.py`에 추가한다. 먼저 상단 import에 다음을 더한다:

```python
import json

from tests.fakes.provider import EchoProvider, ScriptedProvider
```

그리고 헬퍼와 테스트를 파일 끝에 추가한다:

```python
def _blank_at(indices: set[int], count: int) -> ScriptedProvider:
    """지정한 인덱스만 **공백 번역**으로 답하는 가짜.

    공백 번역은 `engine.py:419`가 `reason="empty_translation"`으로 실패
    처리한다 - 응답 형식은 올바르므로 개별 폴백이 개입하지 않아 호출이
    배치 1회로 끝난다. `EchoProvider(drop_last=True)`는 이 목적에 쓸 수
    없다: 배치가 개수 불일치로 실패하면 폴백이 개별 호출로 재시도하고
    거기서는 `len(items) > 1`이 거짓이라 **전부 성공한다**.
    """
    items = [
        {"id": i, "text": "   " if i in indices else f"EN{i}"} for i in range(count)
    ]
    return ScriptedProvider([json.dumps({"translations": items}, ensure_ascii=False)])


def test_번역_실패분은_트리아지에서_빠진다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """설계 §3.6·D12 — 라이브러리가 계약으로 요구한다.

    산수(세그먼트 10건 · 실패 3건 · 예산 10%):

    | 구현 | 대상 | quota | hard fail | 선별 | 실제 비율 |
    | --- | --- | --- | --- | --- | --- |
    | 올바름 | 7 | ceil(0.7)=1 | 0 | 1 | **14.3%** |
    | 틀림 | 10 | ceil(1.0)=1 | 3 | 3 | **30.0%** |

    틀린 구현에서는 `struct.empty`가 quota를 소진한다
    (`remaining = max(0, 1-3) = 0`). 실측으로는 실패 20건에서 Recall@10%가
    0%까지 떨어진다(`TranslationResult` 독스트링).
    """
    _patch_provider(monkeypatch, _blank_at({2, 5, 9}, 10))

    result = runner.invoke(
        app, _args(tmp_path, "ten_cues.srt", "--review-budget", "10%")
    )

    assert result.exit_code == 1, result.output  # 번역 실패가 있으면 1이다 (FR-2.6)
    assert "대상 세그먼트 7개 (번역 실패 3건 제외)" in result.output
    assert "실제 14.3%" in result.output
    # **실패분이 애초에 안 들어왔다의 직접 증거다.** 비율만 보면 세그먼트
    # 수가 우연히 맞는 데이터에서 통과할 수 있다.
    assert "struct.empty" not in result.output


def test_요청_예산과_실제_비율이_어긋난다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """설계 D8·§9.3 — `ceil` 하나로 어긋난다. hard fail이 필요하지 않다.

    `large.srt`는 세그먼트 26개다. 예산 10% → `quota = ceil(2.6) = 3` →
    실제 `3/26 = 11.5%`.

    **신호 구현에 의존하지 않는다** - 위험도가 전부 0.0이어도
    `select_by_budget`은 정렬 후 상위 3건을 선별한다(동점은 세그먼트 ID 순,
    `policy.py:52`).
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "large.srt", "--review-budget", "10%"))

    assert result.exit_code == 0, result.output
    assert "예산 10%" in result.output
    assert "실제 11.5%" in result.output


def test_배치_신호가_발화한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """설계 §9.1 — 최우선 게이트.

    `spec.overlap`은 `BatchCollector`라 트랙 전체를 봐야 판정된다
    (`spec/check.py:100-127`이 정렬 후 누적 `run_end`와 비교한다).
    `collect_all`에 세그먼트를 하나씩 넘기면 **신호가 발화하지 않고
    프로그램은 정상 종료한다** - 조용한 실패다.

    `overlap.vtt`는 큐 2개가 3000~4000ms에서 겹친다.
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "overlap.vtt", "--review-budget", "50%"))

    assert result.exit_code == 0, result.output
    assert "spec.overlap" in result.output


def test_실패가_없으면_제외_괄호를_내지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app, _args(tmp_path, "ten_cues.srt", "--review-budget", "10%")
    )

    assert result.exit_code == 0, result.output
    assert "대상 세그먼트 10개" in result.output
    # **괄호까지 포함해 단언한다.** `_format_translate_summary`가 바로 위에서
    # "성공 10개 · 실패 0개"를 내므로 `"번역 실패"`만으로는 우연에 기댄다.
    assert "(번역 실패" not in result.output


def test_임계값_방식이_동작한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-6.3 ② — `select_by_threshold`를 부른다."""
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app, _args(tmp_path, "ten_cues.srt", "--review-threshold", "0.7")
    )

    assert result.exit_code == 0, result.output
    assert "임계값 0.7" in result.output
    assert "대상 세그먼트 10개" in result.output


def test_언어별로_트리아지가_돈다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "ten_cues.srt"),
            "--to",
            "en,ja",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--no-cache",
            "--review-budget",
            "10%",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[en] 트리아지" in result.output
    assert "[ja] 트리아지" in result.output
```

- [ ] **Step 3: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_triage.py -v -k "트리아지 or 어긋 or 발화 or 괄호 or 임계값"`

Expected: FAIL — 출력에 "트리아지"라는 문자열이 없다(아직 아무것도 출력하지 않는다).

- [ ] **Step 4: import를 추가한다**

`src/cuesift/cli.py` 상단 import에 추가한다:

```python
from cuesift.risk import fuse
from cuesift.segment import SegmentRisk
from cuesift.signals import SignalContext, collect_all
from cuesift.triage import review_ratio, select_by_budget, select_by_threshold
```

- [ ] **Step 5: 요약 포맷터를 쓴다**

`src/cuesift/cli.py`의 `_format_translate_summary` 바로 뒤에 추가한다:

```python
def _format_triage_summary(
    *,
    target_lang: str,
    policy_label: str,
    risks: Sequence[SegmentRisk],
    excluded: int,
) -> list[str]:
    """트리아지 결과를 요약한다 (FR-7.4 · 설계 §7.1).

    **`risks`는 `select_by_*`가 돌려준 전체 목록이다.** 선별분만 넘기면
    `review_ratio`가 언제나 1.0이 되고, 그 값이 스펙 §6.2의 "실제 검수
    비율"이자 README 배수의 분모라 조용히 틀리면 프로젝트의 핵심 주장이
    무너진다(`triage/policy.py` 모듈 독스트링).

    **요청 예산과 실제 비율을 함께 낸다.** hard fail이 quota를 소진하므로
    (`policy.py:92`) 둘은 정기적으로 어긋나고, `ceil` 하나로도 어긋난다
    (26건에 10%면 실제 11.5%). 요청만 찍으면 사용자가 배수를 틀린 분모로
    재계산한다.

    `excluded`가 0이면 괄호를 내지 않는다 - 실패가 없는 정상 실행에서
    "(번역 실패 0건 제외)"는 없는 문제를 있는 것처럼 보이게 한다. 실패 ID
    자체는 `_format_translate_summary`가 바로 위에서 나열했으므로 여기서
    반복하지 않는다(설계 §7.1).
    """
    total = len(risks)
    selected = sum(1 for r in risks if r.selected)
    hard = sum(1 for r in risks if r.hard_fail)

    scope = f"  대상 세그먼트 {total}개"
    if excluded:
        scope += f" (번역 실패 {excluded}건 제외)"

    # `reasons`는 0점 신호를 담지 않는다(`fuse.py:73` - "0점 신호를 사유에
    # 넣으면 리포트가 '이것 때문에 뽑혔다'고 거짓말한다"). 따라서 이 집계가
    # 곧 "적발 건수"다.
    counts: Counter[str] = Counter()
    for risk in risks:
        counts.update(risk.reasons)

    lines = [
        f"[{target_lang}] 트리아지 ({policy_label})",
        scope,
        f"  검수 대상 {selected}개 (실제 {review_ratio(risks):.1%})",
        f"  hard fail {hard}개",
    ]
    if counts:
        lines.append("  신호별 적발")
        # 정렬은 NFR-3(재현성)이다 - Counter의 순서는 삽입 순이라 세그먼트
        # 순서가 바뀌면 화면이 달라지고 테스트가 흔들린다.
        lines.extend(f"    {name} {count}개" for name, count in sorted(counts.items()))
    return lines
```

- [ ] **Step 6: 트리아지 실행 함수를 쓴다**

바로 뒤에 추가한다:

```python
def _run_triage(
    *,
    target_lang: str,
    profile: SpecProfile,
    glossary: Glossary | None,
    source_lang: str,
    translated: TranslationResult,
    budget_ratio: float | None,
    threshold: float | None,
    policy_label: str,
) -> list[str]:
    """번역 결과를 트리아지해 요약 줄을 낸다 (FR-6.1~6.3 · 설계 §4).

    **번역 실패분을 입력에서 뺀다.** `TranslationResult`가 독스트링으로
    요구하는 계약이다 - 실패분은 `segments`에 `target_text=None`으로 남아
    `struct.empty`가 `hard_fail=True`를 내고(`structural.py:166-172`), hard
    fail은 예산 quota를 소진해 진짜 오류를 큐에서 밀어낸다. 실측(200큐·진짜
    오류 20건·예산 10%): 실패 20건에서 **Recall@10%가 0%**가 되고 30건에서는
    실제 비율이 15%로 부풀어 배수의 분모까지 망가진다. 번역 안 된 자막은
    검수 대상이 아니라 **재실행 대상**이다.

    **`collect_all`에 트랙 전체를 한 번에 넘긴다.** `spec.overlap`이
    `BatchCollector`라 세그먼트를 하나씩 넘기면 신호가 발화하지 않는데
    프로그램은 정상 종료한다 - 종료 코드로는 알 수 없는 조용한 실패다.
    """
    failed_ids = {f.segment_id for f in translated.failures}
    kept = [seg for seg in translated.segments if seg.id not in failed_ids]
    if not kept:
        # 전량 실패에서 `review_ratio`는 0.0을 내지만(빈 목록 가드,
        # `policy.py:194-195`) "검수 대상 0개"는 "볼 것이 없다"로 읽힌다.
        # 실제로는 **판정 자체를 못 한 것**이므로 구별해 말한다.
        return [
            f"[{target_lang}] 트리아지: 번역된 세그먼트가 없어 건너뛴다 "
            f"(전량 {len(failed_ids)}건 실패)"
        ]

    ctx = SignalContext(
        profile=profile,
        glossary=glossary,
        source_lang=source_lang,
        target_lang=target_lang,
    )
    signals = collect_all(kept, ctx)
    # `collect_all`은 신호가 없는 세그먼트도 빈 리스트로 키를 갖는다
    # (`signals/base.py`) - KeyError 없이 전량을 돌 수 있다는 보장이다.
    risks = [fuse(seg.id, signals[seg.id]) for seg in kept]

    if budget_ratio is not None:
        scored = select_by_budget(risks, budget_ratio)
    elif threshold is not None:
        scored = select_by_threshold(risks, threshold)
    else:
        # 호출자가 트리아지를 요청하지 않았는데 여기 도달한 것이다.
        # 조용히 빈 목록을 내면 "트리아지가 돌았고 아무것도 안 걸렸다"로
        # 읽혀 미배선을 정상으로 오인한다.
        raise ValueError("budget_ratio와 threshold가 둘 다 None이다")

    return _format_triage_summary(
        target_lang=target_lang,
        policy_label=policy_label,
        risks=scored,
        excluded=len(failed_ids),
    )
```

- [ ] **Step 7: `_translate_one`에 배선한다**

`_translate_one` 시그니처의 `cache_dir` 뒤에 인자 4개를 추가한다:

```python
    cache_dir: Path | None,
    triage_profile: SpecProfile | None,
    budget_ratio: float | None,
    threshold: float | None,
    policy_label: str,
) -> int:
```

그리고 본문 끝의 `return 1 if translated.failures else 0` **직전**에 추가한다:

```python
    if triage_profile is not None:
        # 요약 출력 **뒤**에 온다 - `_format_translate_summary`가 실패 ID를
        # 먼저 나열하고, 트리아지 요약은 그것이 분모에서 빠졌다고 말한다.
        # 순서가 뒤집히면 "3건 제외"가 무엇을 가리키는지 알 수 없다.
        for line in _run_triage(
            target_lang=target_lang,
            profile=triage_profile,
            glossary=glossary,
            source_lang=source_lang,
            translated=translated,
            budget_ratio=budget_ratio,
            threshold=threshold,
            policy_label=policy_label,
        ):
            _echo(line)

    return 1 if translated.failures else 0
```

- [ ] **Step 8: 호출부에 인자를 넘긴다**

`translate` 본문의 `_translate_one(...)` 호출(`cli.py:606-617`)에 인자를 추가한다:

```python
            cache_dir=None if no_cache else (cache_dir or DEFAULT_CACHE_DIR),
            triage_profile=profiles.get(target),
            budget_ratio=budget_ratio,
            threshold=review_threshold,
            policy_label=policy_label,
        )
```

`profiles`는 트리아지를 요청하지 않으면 빈 dict라 `.get(target)`이 `None`을 내고
트리아지가 돌지 않는다(D3 하위 호환).

- [ ] **Step 9: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_triage.py -v`

Expected: PASS — 25 + 6 = **31개 통과**

각 단언의 실제 값을 눈으로 확인한다. 특히 `실제 14.3%`와 `실제 11.5%`가 반올림으로
어긋나지 않는지 본다(`{:.1%}`는 `1/7 = 0.142857`을 `14.3%`로, `3/26 = 0.115385`를
`11.5%`로 낸다).

- [ ] **Step 10: 전체 게이트를 돌린다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing 2>&1 | tail -20
```

Expected: 전체 통과. 수집 개수와 커버리지를 기록한다.

- [ ] **Step 11: 실동작을 확인한다**

```powershell
.venv/Scripts/python.exe -m cuesift translate tests/fixtures/ingest/ten_cues.srt `
  --to en --out $env:TEMP\cuesift-out --review-budget 10% --dry-run
```

Expected: dry-run은 트리아지를 돌리지 않는다(번역이 없다). 프로파일 검증은 통과한다 —
`--to fr`로 바꾸면 dry-run에서도 exit 2가 나야 한다(검증이 `dry_run` 분기보다 앞에 있다).

실제 LLM으로 확인하려면(Ollama가 떠 있을 때):

```powershell
$env:CUESIFT_BASE_URL="http://localhost:11434/v1"
$env:CUESIFT_MODEL="qwen2.5:3b"
.venv/Scripts/python.exe -m cuesift translate tests/fixtures/ingest/ten_cues.srt `
  --to en --out $env:TEMP\cuesift-out --review-budget 10%
```

Expected: `[en] 트리아지 (예산 10%)` 블록이 나온다.

- [ ] **Step 12: 커밋**

```bash
git add src/cuesift/cli.py tests/test_cli_triage.py tests/fixtures/ingest/ten_cues.srt
git commit -m "기능: translate에 트리아지 배선 — 실패분 제외·실제 비율 출력 (FR-6.1~6.3 · D12)"
```

---

## Task 4: 게이트를 되돌려 실패시켜 확인한다

**이 태스크는 코드를 쓰지 않는다.** 세 게이트가 버그 버전에서 실제로 죽는지 확인하고
그 값을 기록한다. 이 저장소의 규율이다 — "회귀 테스트는 버그 코드에서 실제로 실패하는
것을 확인한 뒤에야 회귀 테스트다." 실제로 이 작업의 스펙 초안 §9.2가 **틀린 구현에서
통과하고 옳은 구현에서 실패하는** 부호 반전 테스트였다.

**Files:**

- 임시로 수정한 뒤 **반드시 되돌린다**: `src/cuesift/cli.py`
- 기록: 커밋 메시지 본문

**Interfaces:**

- Consumes: Task 3의 세 테스트
- Produces: 없음 (검증 기록만)

- [ ] **Step 1: 실패분 제외를 되돌린다 (§9.2)**

`_run_triage`의 필터를 무력화한다:

```python
    failed_ids = {f.segment_id for f in translated.failures}
    kept = list(translated.segments)  # ← 일부러 틀린 배선
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_triage.py -k 실패분 -v`

Expected: **FAIL**. 확인할 값 셋을 기록한다:

| 단언 | 옳은 구현 | 버그 버전 |
| --- | --- | --- |
| `대상 세그먼트 N개` | 7개 (번역 실패 3건 제외) | 10개 |
| `실제 N%` | 14.3% | 30.0% |
| `struct.empty` | 출력에 없다 | 3개 |

세 단언이 모두 죽어야 한다. 하나만 죽으면 나머지 단언이 게이트로 기능하지 않는다는 뜻이다.

- [ ] **Step 2: 되돌린다**

```bash
git checkout src/cuesift/cli.py
.venv/Scripts/python.exe -m pytest tests/test_cli_triage.py -k 실패분 -v
```

Expected: PASS

- [ ] **Step 3: 배치 신호를 되돌린다 (§9.1)**

`_run_triage`의 `collect_all` 호출을 세그먼트 단위로 바꾼다:

```python
    signals = {}
    for seg in kept:  # ← 일부러 틀린 배선
        signals.update(collect_all([seg], ctx))
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_triage.py -k 배치_신호 -v`

Expected: **FAIL** — `assert "spec.overlap" in result.output`이 죽는다.
**exit code는 여전히 0이다** — 이것이 이 게이트가 값을 검사해야 하는 이유다.

- [ ] **Step 4: 되돌린다**

```bash
git checkout src/cuesift/cli.py
.venv/Scripts/python.exe -m pytest tests/test_cli_triage.py -k 배치_신호 -v
```

Expected: PASS

- [ ] **Step 5: 실제 비율을 되돌린다 (§9.3)**

`_format_triage_summary`가 요청 예산을 실제 비율 자리에 찍게 한다:

```python
        f"  검수 대상 {selected}개 (실제 {selected / total if total else 0:.1%})",
```

이것은 hard fail 소진이 없을 때는 우연히 맞는다. 더 확실한 버그 버전은 라벨의 숫자를
그대로 쓰는 것이다 — `policy_label`에서 파싱해 찍는 구현. 간단히는 `review_ratio` 호출을
지우고 요청 예산을 넘겨 찍는다.

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_triage.py -k 어긋 -v`

Expected: **FAIL** — `실제 11.5%`가 아니라 `실제 10.0%`가 나온다.

**주의**: 첫 번째 버그 버전(`selected / total`)은 이 케이스에서 `3/26 = 11.5%`로 **통과한다**
(hard fail이 없어 `review_ratio`와 같은 값이다). 통과하면 그것을 기록하고, `review_ratio`가
없으면 어떤 케이스에서 갈라지는지(hard fail이 quota를 초과하는 경우) 함께 적는다 —
게이트의 한계를 아는 것이 게이트가 없는 것보다 낫다.

- [ ] **Step 6: 되돌리고 전체를 확인한다**

```bash
git checkout src/cuesift/cli.py
git status --short   # 수정된 파일이 없어야 한다
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing 2>&1 | tail -20
```

Expected: `git status`가 깨끗하다. 전체 테스트 통과.

- [ ] **Step 7: 검증 기록을 빈 커밋으로 남긴다**

PowerShell here-string을 쓴다. 닫는 `'@`는 **행 시작(column 0)**에 있어야 한다 —
들여쓰면 파스 오류다.

```powershell
git commit --allow-empty -m @'
검증: 게이트 3종을 버그 버전에서 실패시켜 확인했다

코드 변경은 없다. Task 3이 만든 게이트가 실제로 결함을 잡는지 확인한 기록이다.

| 게이트 | 버그 버전 | 옳은 구현 | 버그에서 관측된 값 |
| --- | --- | --- | --- |
| 실패분 제외 (§9.2) | kept = list(translated.segments) | 7개·14.3%·struct.empty 없음 | 10개·30.0%·struct.empty 3개 |
| 배치 신호 (§9.1) | collect_all([seg]) 반복 | spec.overlap 발화 | 발화 없음, **exit 0 유지** |
| 실제 비율 (§9.3) | 요청 예산을 그대로 찍는다 | 실제 11.5% | 실제 10.0% |

배치 신호 게이트에서 exit code가 0으로 유지되는 것을 확인했다 - 종료 코드로는
알 수 없는 조용한 실패이므로 값을 검사하는 게이트가 유일한 수단이다.
'@
```

---

## Task 5: 문서 정정

**Files:**

- Modify: `docs/WBS.md` — FR-6.3의 CLI 배선 담당을 명시, 의존 그래프, 다음 작업 순서
- Modify: `docs/요구사항정의서.md` §5.4 — FR-6.3·6.4·7.4 상태
- Modify: `HANDOFF.md` — 낡은 절 정리 + 이번 작업 반영
- Modify: `CHANGELOG.md` — `[Unreleased]`

**Interfaces:**

- Consumes: Task 1~4의 게이트 수치
- Produces: 없음 (문서)

- [ ] **Step 1: 요구사항정의서 §5.4의 상태를 갱신한다**

FR-6.3·FR-6.4·FR-7.4에 상태를 적는다. **FR-6.3 전체는 🟡다** — ②(위험도 임계값)는
닫히지만 ①이 "상위 K개"를 빼고 부분 충족이므로 ✅로 세지 않는다. FR-5.3이 "라이브러리엔
있으나 CLI에서 도달 못함"으로 한 번 걸렸던 자리이므로 같은 함정을 반대 방향으로도
반복하지 않는다.

| FR | 상태 | 남는 것 |
| --- | --- | --- |
| FR-6.1 | ✅ | — |
| FR-6.2 | ✅ | — |
| FR-6.3 | 🟡 | ① "상위 K개" |
| FR-6.4 | 🟡 | 세그먼트별 사유 → FR-7.2 |
| FR-7.4 | 🟡 | 소요 토큰(Tier 1) |

- [ ] **Step 2: WBS를 정정한다**

세 곳을 고친다.

1. **FR-6.3의 CLI 배선 담당을 명시한다.** 현재 WP5는 FR-7.1~7.5만, WP6은 FR-8.1~8.5만
   담당해 이 항목이 어느 WP에도 없다. WP6(CLI 배선)에 넣는 것이 FR-8 계열과 성격이 같다
2. **의존 그래프에 "FR-6.3 CLI → FR-4.3 CLI" 간선을 넣는다** — `triage_with_tier1()`이
   `budget_ratio`를 필수로 요구하므로 예산 배선 없이 Tier 1만 얹을 수 없다
3. **"다음 작업 순서"에서 WP8b가 1순위라는 서술을 정정한다**

구조적 원인도 한 줄 남긴다 — WBS가 FR 번호로 WP를 나누므로, 라이브러리와 CLI 두 계층에
걸친 FR은 WP1에서 라이브러리만 만들고 완료로 닫히며 CLI 절반이 미아가 된다. FR-5.3·FR-4.3이
이미 같은 함정에 걸렸다.

- [ ] **Step 3: CHANGELOG에 항목을 넣는다**

`[Unreleased]`의 `Added`에 넣는다. **`[Unreleased]`는 현재 상태를 서술한다**(Keep a
Changelog) — 이미 릴리스된 이력이 아니므로 손대는 데 제약이 없다.

담을 것: `--review-budget` 실제 동작 · `--review-threshold` 신설 · 프로파일 자동 유도 ·
번역 실패분 제외(계약 근거와 실측) · 요청 예산과 실제 비율 병기.

- [ ] **Step 4: HANDOFF를 갱신한다**

- "🔴 즉시 해야 할 것 — PR을 만들어야 CI가 돈다"는 이미 끝났다(`e88177e`가 PR #7 스쿼시
  머지). 낡은 절을 지운다
- 브랜치를 `feat/triage-cli`로 갱신
- 게이트 수치를 이번 실측으로 갱신
- "다음 1순위"를 **Tier 1 CLI 배선(FR-4.3)**으로 바꾼다. 그리고 이전 HANDOFF ①의 4건
  (배치 무력화·Tier 1 토큰 미집계·`samples` 상한 부재·`CacheRequest.attempt` 검증 부재)이
  **여전히 유효한 선결 사항**임을 남긴다

- [ ] **Step 5: 문서 게이트를 돌린다**

```bash
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

Expected: 두 게이트의 **파일 수가 일치해야 한다.** 갈라지면 추적 안 된 문서가 있다는
뜻이다 — 다만 새 파일을 `git add`하기 전에는 링크 체커가 세지 않으므로(`git ls-files`
기준) add 후 다시 돌린다.

- [ ] **Step 6: 커밋**

```bash
git add docs/ HANDOFF.md CHANGELOG.md
git commit -m "문서: FR-6.3 CLI 배선 반영과 WBS 구멍 정정"
```

---

## Task 6: PR 생성

- [ ] **Step 1: 전체 게이트를 마지막으로 돌린다**

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest --cov=cuesift --cov-report=term-missing 2>&1 | tail -20
.venv/Scripts/python.exe scripts/check_links.py
npx --yes markdownlint-cli2
```

**모든 수치를 기록한다.** "통과했나"가 아니라 "무엇을 대상으로 통과했나"를 본다.

- [ ] **Step 2: 사용자에게 푸시 허락을 구한다**

**푸시는 사용자가 명시적으로 요청할 때만 한다.** 커밋과 푸시를 한 명령에 묶지 않는다.

허락을 받은 뒤:

```bash
git push -u origin feat/triage-cli
gh pr create --base main
gh pr checks --watch     # test 3.11 · test 3.12 · docs
```

**로컬 venv는 3.14이고 CI는 3.11·3.12다.** 로컬 게이트 통과가 CI 통과를 보장하지 않는다 —
이 저장소에서 35커밋이 CI를 한 번도 안 거치고 쌓였다가 원격 병합 시 rich 렌더링 차이로
실패한 전례가 있다.

PR 본문에는 **무엇을 · 근거 문서 · 게이트 수치**를 담는다. 게이트 수치는 개수를 그대로 적는다.

---

## Self-Review

**Spec coverage:**

| 스펙 절 | 태스크 |
| --- | --- |
| §1.1 범위 (옵션·프로파일·신호 배선·실패분 제외·요약) | Task 1·2·3 |
| §2 D1·D2 (translate에 통합, check 확장 안 함) | Task 3 — 새 명령을 만들지 않는다 |
| §2 D3 (정책 없으면 트리아지 안 돎) | Task 2 Step 6 (`triage_requested`) · 테스트 |
| §2 D4 (상호배타) | Task 2 Step 5 · 테스트 |
| §2 D5 (비율만) | Task 1 |
| §2 D6·D7 (자동 유도 · 없으면 경고 후 건너뛰기 · 전부 없으면 exit 2) | Task 2 Step 6 · 테스트 2건 |
| §2 D8 (요청·실제 병기) | Task 3 Step 5 · §9.3 게이트 |
| §2 D9 (목록 출력 안 함) | Task 3 — `_format_triage_summary`가 집계만 낸다 |
| §2 D10 (exit 0 유지) | Task 3 — `return 1 if translated.failures else 0` 그대로 |
| §2 D11 (라이브러리 안 건드림) | Task 3 — `available_builtins()` 재사용, `src/` 수정 없음 |
| §2 D12 (실패분 제외) | Task 3 Step 6 · §9.2 게이트 |
| §2 D13 (프로파일 사전 검증) | Task 2 Step 6 · 전용 테스트 |
| §5.2 파싱 표 11행 | Task 1 파라미터 테스트 |
| §7.1 출력 5줄 | Task 3 Step 5 |
| §9.1~§9.3 게이트 | Task 3(작성) · Task 4(되돌림 확인) |
| §9.4 나머지 | Task 2·3 테스트 |
| §10 완료 판정 | Task 6 Step 1 |
| §11 문서 정정 | Task 5 |

빠진 것 하나: **§9.4의 "전체 목록 계약"**(선별분만 넘기면 `review_ratio`가 1.0이 된다).
`_format_triage_summary`가 `risks`를 그대로 받아 `review_ratio(risks)`를 부르므로 계약은
지켜지지만 **전용 게이트가 없다.** Task 3 Step 2의 `test_요청_예산과_실제_비율이_어긋난다`가
간접적으로 잡는다 — 선별분만 넘기면 `실제 100.0%`가 나와 `실제 11.5%` 단언이 죽는다.
이것을 Task 4 Step 5의 기록에 명시한다.

**Type consistency:** `_run_triage`의 `threshold` 인자와 `translate`의 `review_threshold`
옵션은 이름이 다르다(의도적이다 — CLI 옵션 이름과 내부 인자를 구별한다). Task 3 Step 8의
호출부가 `threshold=review_threshold`로 잇는다. `_format_triage_summary`의 `risks`는
`Sequence[SegmentRisk]`이고 `select_by_*`의 반환 타입 `list[SegmentRisk]`와 호환된다.

**Placeholder scan:** 없음. Task 4 Step 5만 "두 버그 버전 중 하나는 통과할 수 있다"는
조건부 지시를 담는데, 그것은 게이트의 실제 한계이므로 플레이스홀더가 아니라 **기록해야 할
사실**이다.
