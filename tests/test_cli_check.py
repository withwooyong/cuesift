"""`cuesift check` 배선 테스트 (FR-8.2·FR-7.5).

설계 §10.3이 지목한 "diff로 판정할 수 없는 것" 셋을 여기서 닫는다 —
큐 번호가 원본 위치인가 · stdout/stderr 분리 · 종료 코드 2와 66의 구분.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cuesift.cli import _resolve_profile

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures" / "ingest"
# 리포 루트 기준으로 고정한다. 상대 경로 "specs/ted-ko.yaml"로 두면 pytest를
# 리포 루트가 아닌 곳에서 돌릴 때만 실패해, 통과가 실행 위치에 의존하게 된다.
SPECS = Path(__file__).parents[1] / "specs"


def test_resolve_profile_reads_a_builtin_name():
    assert _resolve_profile("ko").name == "ko"


def test_resolve_profile_reads_a_yaml_path():
    """FR-5.3 — 사용자 프로파일이 CLI에서 도달 가능해야 한다."""
    profile = _resolve_profile(str(SPECS / "ted-ko.yaml"))
    assert profile.name == "ted-ko"


def test_resolve_profile_rejects_an_unknown_builtin_name():
    """오타 난 내장 이름은 사용 가능한 목록을 함께 보여준다."""
    import typer

    with pytest.raises(typer.BadParameter) as caught:
        _resolve_profile("th")
    assert "ted-ko" in str(caught.value)


def test_resolve_profile_reports_a_missing_yaml_as_a_path_problem():
    """확장자로 가르므로 오타 난 경로가 '내장 이름이 없다'는 틀린 진단을 받지 않는다.

    존재 여부로 갈랐다면 './없는.yaml'이 내장 이름으로 해석되어
    "'./없는.yaml' 프로파일이 없다. 사용 가능: en, ja, ..."라는
    엉뚱한 메시지가 나갔을 것이다.
    """
    import typer

    with pytest.raises(typer.BadParameter) as caught:
        _resolve_profile("./없는파일.yaml")
    message = str(caught.value)
    assert "사용 가능" not in message, "내장 이름으로 잘못 해석됐다"
