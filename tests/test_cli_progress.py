"""`--progress` 옵션과 3상 기본값 (FR-8.5 · 설계 D5·D7)."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from tests.fakes.provider import EchoProvider
from typer.testing import CliRunner

from conftest import normalize_rich_message, strip_rich_decoration
from cuesift.cli import app

_FIXTURES = Path(__file__).parent / "fixtures" / "ingest"
runner = CliRunner()


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: object) -> None:
    """`test_cli_translate.py`와 같은 관례 - 네트워크를 타지 않는다."""
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: provider)


def _args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "translate",
        str(_FIXTURES / "minimal.srt"),
        "--to",
        "en",
        "--out",
        str(tmp_path),
        "--base-url",
        "http://h/v1",
        "--model",
        "m1",
        "--cache-dir",
        str(tmp_path / "cache"),
        *extra,
    ]


def test_progress_옵션이_help에_있다() -> None:
    """**색을 켜고 잰다.** rich 하이라이터가 옵션 이름을 ANSI로 쪼개는 사고가
    이 저장소에서 관측됐고(PR #10), 그것은 색이 켜진 CI에서만 난다 -
    `FORCE_COLOR` 없이 재면 로컬은 초록이고 CI에서만 죽는다.

    **`COLUMNS`도 못 박는다.** 기본 폭 80에서는 rich가 이름 열을 줄여
    말줄임(`…`)을 넣는다 - 그러면 이 단언이 색이 아니라 폭을 재게 된다.
    `test_cli_tier1.py`의 형제 검사가 같은 이유로 같은 값을 쓴다.
    """
    result = runner.invoke(
        app, ["translate", "--help"], color=True, env={"FORCE_COLOR": "1", "COLUMNS": "100"}
    )
    assert result.exit_code == 0
    squashed = normalize_rich_message(result.output)
    assert normalize_rich_message("--progress") in squashed
    assert normalize_rich_message("--no-progress") in squashed


def test_progress_기본값은_지정_안_함이다() -> None:
    # 기본이 `False`면 자동 감지가 영영 안 돈다 (설계 D7). `None`이라야
    # `resolve_style`이 감지로 내려간다.
    group = typer.main.get_command(app)
    param = next(p for p in group.commands["translate"].params if p.name == "progress")
    assert param.default is None


def test_progress를_주면_stderr에_진행이_실제로_나간다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**저장소에 이것이 없었다** (리뷰 라운드 2 F1).

    `--progress`를 켜는 테스트가 `--help`(문자열 검사)와 `test_cli_pipe.py`의
    `--dry-run` 둘뿐이었는데, `--dry-run`은 리포터를 설치하기 **전에**
    return한다. 그래서 `on_progress=reporter.update` 2곳과 `phase()`/`done()`
    6곳을 전부 지워도 죽는 테스트가 **0건**이었다 - 변이로 확인된 수치다.
    """
    _patch_provider(monkeypatch, EchoProvider())
    result = runner.invoke(app, _args(tmp_path, "--progress"))
    assert result.exit_code == 0, result.stderr

    # `CliRunner`의 stderr는 TTY가 아니므로 감지가 `plain`을 고른다(설계 D7).
    # 제어문자가 없고 이정표 줄이 누적된다.
    assert "\r" not in result.stderr
    assert "[en] 번역 2/2 (100%)" in result.stderr
    # **`done()`의 plain 갈래다.** 이 줄이 없으면 단계가 끝난 것과
    # 멈춘 것이 화면에서 구별되지 않는다.
    assert "[en] 번역 완료 (실패 0)" in result.stderr

    # **stdout은 진행으로 오염되지 않는다** (설계 D9). 파이프로 받는 쪽이
    # 깨진다 - `test_cli_pipe.py`가 지켜 온 계약이다.
    assert "(100%)" not in result.stdout
    assert "\r" not in result.stdout
    assert "완료 (실패" not in result.stdout


def test_no_progress를_주면_stderr에_진행이_없다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 짝을 이루는 음성 대조군이다. 위 테스트만 두면 "진행이 늘 나간다"와
    # "`--progress`가 켠다"가 구별되지 않는다.
    _patch_provider(monkeypatch, EchoProvider())
    result = runner.invoke(app, _args(tmp_path, "--no-progress"))
    assert result.exit_code == 0, result.stderr
    assert "(100%)" not in result.stderr
    assert "완료 (실패" not in result.stderr


def test_옵션_이름이_폭_88에서_잘리지_않는다() -> None:
    """도움말 폭 예산의 게이트. **렌더링을 고치는 것이 아니라 선을 긋는다.**

    `--progress/--no-progress`가 이름 열을 넓혀 좁은 폭에서 잘리는 옵션이
    늘었다. 리뷰어 실측(구트리 `4cfdc28` vs HEAD, 같은 probe, 잘린 개수):

    | COLUMNS | 60 | 70 | 76 | 80 | 84 | 88 |
    | --- | --- | --- | --- | --- | --- | --- |
    | 이전 | 5 | 2 | 0 | 0 | 0 | 0 |
    | 지금 | 7 | 6 | 4 | 3 | 1 | 0 |

    **색과 무관하다** - `NO_COLOR=1`에서도 같은 수치다. rich의 표 렌더링
    문제이고 FR-8.5의 범위 밖이라 여기서 고치지 않는다. 안전한 폭이 88이고,
    **이 테스트가 깨지면 옵션을 더 붙인 사람이 폭 예산을 넘긴 것이다.**

    **`normalize_rich_message`를 쓰지 않는다.** 그 함수는 공백까지 지워
    줄바꿈으로 쪼개진 이름(`--tier1-` + `temperature`)을 도로 붙이므로
    **잘린 것도 통과시킨다.** 한 줄 안에 통째로 있는지를 봐야 한다.

    ANSI만 지운다. 앞선 테스트가 `FORCE_COLOR=1`로 `--help`를 한 번 그리면
    `typer.rich_utils`의 `FORCE_TERMINAL`이 **임포트 시점에 고정돼** 세션
    내내 색이 켜진 채로 남고(실측), 그러면 rich 하이라이터가 옵션 이름
    가운데에 ANSI를 넣어 전부 "잘린 것"으로 읽힌다.
    """
    group = typer.main.get_command(app)
    names = sorted(
        {
            opt
            for param in group.commands["translate"].params
            for opt in (getattr(param, "opts", []) or [])
            if opt.startswith("--")
        }
    )
    assert len(names) >= 20, "옵션을 못 모았으면 0건 통과가 된다"
    output = runner.invoke(
        app, ["translate", "--help"], env={"COLUMNS": "88", "NO_COLOR": "1"}
    ).output
    lines = strip_rich_decoration(output).splitlines()
    truncated = [name for name in names if not any(name in line for line in lines)]
    assert truncated == [], f"폭 88에서 잘린 옵션: {truncated}"
