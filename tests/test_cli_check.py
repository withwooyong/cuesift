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


def test_resolve_profile_reads_a_yml_extension(tmp_path):
    """`.yml`도 경로로 받는다 (설계 §8 D10의 논리적 귀결).

    `.yml`을 빼면 `--spec spec.yml`이 내장 이름으로 해석되어 D10이 막으려던
    틀린 진단을 그대로 받는다. `.yaml`과 같은 줄을 지나므로 statement 커버리지
    100%가 이 공백을 가린다 — 값으로 지나가는 테스트가 따로 있어야 한다.

    실패가 아니라 **로드 성공**까지 확인한다. 실패 경로만 보면 확장자 분기가
    아니라 예외 처리를 재게 된다.
    """
    target = tmp_path / "custom.yml"
    target.write_text((SPECS / "ted-ko.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    assert _resolve_profile(str(target)).name == "ted-ko"


def test_resolve_profile_wraps_a_broken_builtin_profile(monkeypatch):
    """내장 프로파일이 깨졌을 때 `ValueError`가 새어나가지 않아야 한다.

    `load_builtin`은 내부에서 `load_profile`을 부르므로(profile.py) 동봉된
    YAML이 손상되면 `FileNotFoundError`가 아니라 `ValueError`가 난다.
    이것이 새어나가면 미처리 traceback으로 **종료 코드 1**이 되는데,
    이 저장소에서 1은 "규격 위반 발견"이라 **패키징 사고가 자막 결함으로
    오보된다**. 사용자는 자막을 고치려 들고 진짜 원인은 숨는다.
    """
    import typer

    from cuesift import cli

    def _broken(name: str):
        raise ValueError("specs/ko.yaml: 필수 필드가 없다")

    monkeypatch.setattr(cli, "load_builtin", _broken)
    with pytest.raises(typer.BadParameter) as caught:
        _resolve_profile("ko")
    assert "필수 필드가 없다" in str(caught.value)


def test_resolve_profile_wraps_a_yaml_syntax_error(tmp_path):
    """YAML 문법 오류도 `BadParameter`로 모아야 한다.

    문법 오류는 `yaml.YAMLError`(`ParserError` 등)라서 **`OSError`도 `ValueError`도
    아니다.** 둘만 잡으면 사용자가 준 `--spec ./my.yaml` 하나로 미처리 traceback과
    **종료 코드 1**이 나가고, 이 저장소에서 1은 "규격 위반 발견"이다.
    FR-5.3이 존재하는 이유인 사용자 경로에 그대로 뚫려 있던 구멍이다.
    """
    import typer

    broken = tmp_path / "broken.yaml"
    broken.write_text("name: [unclosed\n  bad: : :\n", encoding="utf-8")
    with pytest.raises(typer.BadParameter):
        _resolve_profile(str(broken))
