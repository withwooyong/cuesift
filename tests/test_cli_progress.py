"""`--progress` 옵션과 3상 기본값 (FR-8.5 · 설계 D5·D7)."""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from conftest import normalize_rich_message
from cuesift.cli import app

runner = CliRunner()


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
