"""`--progress` 옵션과 3상 기본값 (FR-8.5 · 설계 D5·D7)."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from tests.fakes.provider import EchoProvider
from typer.testing import CliRunner

from conftest import normalize_rich_message
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
