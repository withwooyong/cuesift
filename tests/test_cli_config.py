"""설정 파일의 CLI 배선 (FR-8.4 · 설계 §4).

**`result.output`은 stdout과 stderr가 섞인 스트림이다**(typer 0.27의
`StreamMixer` 실측). 산출물과 진단을 나눠 봐야 하는 곳에서는 `.stdout`·
`.stderr`를 따로 쓴다 - `test_cli_check.py`가 같은 관례를 갖고 있다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import normalize_rich_message
from cuesift.cli import app

runner = CliRunner()

_VIOLATIONS = Path(__file__).parent / "fixtures" / "ingest" / "check_violations.ass"


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cuesift.yaml"
    path.write_text(text, encoding="utf-8")
    return path


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
    assert _violation_lines(result.stdout) == 4
    assert result.exit_code == 1


def test_진리표_설정만_있으면_설정이_이긴다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "spec:\n  profile: ko\n  limit: 2\n  fail_on: none\n")
    result = _check(cfg)
    assert _violation_lines(result.stdout) == 2
    assert result.exit_code == 0


def test_진리표_CLI만_있으면_CLI가_이긴다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "spec:\n  profile: ko\n")
    result = _check(cfg, "--limit", "1", "--fail-on", "none")
    assert _violation_lines(result.stdout) == 1
    assert result.exit_code == 0


def test_진리표_둘_다_있으면_CLI가_이긴다(tmp_path: Path) -> None:
    # **이 한 건이 FR-8.4 본문의 후반절 전체다.**
    cfg = _config(tmp_path, "spec:\n  profile: ko\n  limit: 2\n  fail_on: hard\n")
    result = _check(cfg, "--limit", "1", "--fail-on", "none")
    assert _violation_lines(result.stdout) == 1
    assert result.exit_code == 0


def test_설정이_필수_옵션을_만족시킨다(tmp_path: Path) -> None:
    # `--spec`은 필수다. 설정이 그것을 채우지 못하면 설정 파일로 '모든 옵션'을
    # 지정한다는 FR-8.4 본문이 성립하지 않는다(설계 P3).
    #
    # **부정 단언이라 rich 정규화가 필수다.** 강제 개행이 `Missing option`
    # 사이에 떨어지면 배선이 없어도 이 단언이 통과한다.
    result = _check(_config(tmp_path, "spec:\n  profile: ko\n"))
    assert normalize_rich_message("Missing option") not in normalize_rich_message(result.output)
    assert result.exit_code == 1


def test_설정에_넣은_input은_무시된다(tmp_path: Path) -> None:
    # 설계 D13 - 위치인자는 설정 대상이 아니다. `input`이 매핑표에 있으면
    # 다른 파일을 검수하고도 통과한다. 매핑표에 없으므로 모르는 키가 된다.
    cfg = _config(tmp_path, "spec:\n  profile: ko\ninput: 아무거나.srt\n")
    result = _check(cfg)
    assert result.exit_code == 2
    assert normalize_rich_message("모르는 키 'input'") in normalize_rich_message(result.stderr)


def test_설정을_읽으면_출처가_stderr에_나간다(tmp_path: Path) -> None:
    # 설계 D7 - click의 오류가 `Invalid value for '--review-format'`이라
    # 그 옵션을 친 적 없는 사용자가 명령줄을 노려보게 된다.
    cfg = _config(tmp_path, "source_lang: ko\n")
    result = runner.invoke(app, ["--config", str(cfg), "check", "--help"])
    assert str(cfg) in result.stderr
    assert str(cfg) not in result.stdout, "출처는 산출물이 아니다"


def test_config_파일이_없으면_종료_코드_2다(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--config", str(tmp_path / "없다.yaml"), "check", "--help"])
    assert result.exit_code == 2


def test_모르는_키는_종료_코드_2다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "triage:\n  review_budgt: 10%\n")
    result = runner.invoke(app, ["--config", str(cfg), "check", "--help"])
    assert result.exit_code == 2
    assert normalize_rich_message("모르는 키") in normalize_rich_message(result.stderr)


def test_틀린_값은_click이_종료_코드_2로_낸다(tmp_path: Path) -> None:
    # 설계 D5·P4 - 로더가 아니라 click이 판정한다.
    target = tmp_path / "a.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n", encoding="utf-8")
    cfg = _config(tmp_path, "review:\n  format: xml\n")
    result = runner.invoke(app, ["--config", str(cfg), "translate", str(target), "--to", "en"])
    assert result.exit_code == 2


def test_현재_디렉터리의_cuesift_yaml을_자동으로_읽는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, "source_lang: ja\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["check", "--help"])
    assert "cuesift.yaml" in result.stderr


def test_상위_디렉터리는_읽지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 설계 D2 - 사용자가 존재를 모르는 파일이 검수 기준을 바꾸는 것을 막는다.
    # **반대 방향 회귀 테스트다.** 위 테스트만 두면 상위 탐색을 넣어도 초록이다.
    _config(tmp_path, "source_lang: ja\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    result = runner.invoke(app, ["check", "--help"])
    assert "cuesift.yaml" not in result.stderr
    assert result.exit_code == 0


def test_설정이_없으면_조용히_넘어간다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["check", str(_VIOLATIONS), "--spec", "ko", "--fail-on", "none"])
    assert result.exit_code == 0
    assert result.stderr.strip() == ""


def test_미구현_경고가_사라졌다(tmp_path: Path) -> None:
    # 설계 §4.3 함정 ③ - 남겨 두면 이번에는 반대 방향의 거짓말이 된다.
    cfg = _config(tmp_path, "source_lang: ko\n")
    result = runner.invoke(app, ["--config", str(cfg), "check", "--help"])
    assert normalize_rich_message("아직 구현되지") not in normalize_rich_message(result.output)


def test_help에서_미구현_문구가_사라졌다() -> None:
    result = runner.invoke(app, ["--help"], color=True, env={"FORCE_COLOR": "1"})
    # 색이 켜진 CI에서만 rich가 옵션 이름을 쪼갠 실측이 있다. 정규화로 막는다.
    assert normalize_rich_message("아직 구현되지") not in normalize_rich_message(result.output)


# 환경변수 3층 (설계 D3 · §4.3 함정 ①).
#
# `_resolve_llm`의 `base_url or os.environ.get(...)`을 그대로 두면
# `default_map`이 채운 값이 `or`의 왼쪽에서 참이 되어 **설정 파일이
# 환경변수를 이긴다.** 값은 어느 쪽이 이기든 나오고 종료 코드는 0이라
# 이 테스트들이 없으면 결함이 절대 드러나지 않는다.


class _FakeCtx:
    """`get_parameter_source`만 흉내 낸다. 값의 출처를 고정해 준다.

    `typer._click`을 임포트하지 않는다 - 벤더링된 private 경로라
    typer 업그레이드가 위치를 바꾼다. 판정은 이름 문자열로 한다.
    """

    def __init__(self, source_name: str) -> None:
        self._source_name = source_name

    def get_parameter_source(self, name: str) -> object:
        return type("Src", (), {"name": self._source_name})()


def test_설정보다_환경변수가_우선한다(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuesift.cli import _resolve_llm

    monkeypatch.setenv("CUESIFT_BASE_URL", "http://env")
    monkeypatch.setenv("CUESIFT_MODEL", "m")
    base, _model, _key = _resolve_llm(_FakeCtx("DEFAULT_MAP"), "http://config", None)
    assert base == "http://env"


def test_CLI가_환경변수를_이긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuesift.cli import _resolve_llm

    monkeypatch.setenv("CUESIFT_BASE_URL", "http://env")
    monkeypatch.setenv("CUESIFT_MODEL", "m")
    base, _model, _key = _resolve_llm(_FakeCtx("COMMANDLINE"), "http://cli", None)
    assert base == "http://cli"


def test_환경변수가_없으면_설정을_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuesift.cli import _resolve_llm

    monkeypatch.delenv("CUESIFT_BASE_URL", raising=False)
    monkeypatch.setenv("CUESIFT_MODEL", "m")
    base, _model, _key = _resolve_llm(_FakeCtx("DEFAULT_MAP"), "http://config", None)
    assert base == "http://config"


def test_ctx가_없어도_동작한다(monkeypatch: pytest.MonkeyPatch) -> None:
    # 기존 호출부와의 호환. ctx를 모르면 설정에서 온 값이 아니라고 본다.
    from cuesift.cli import _resolve_llm

    monkeypatch.setenv("CUESIFT_BASE_URL", "http://env")
    monkeypatch.setenv("CUESIFT_MODEL", "m")
    base, _model, _key = _resolve_llm(None, "http://cli", None)
    assert base == "http://cli"


def test_model도_같은_양보를_한다(monkeypatch: pytest.MonkeyPatch) -> None:
    # `base_url`만 고치면 `model`이 반대 순서로 남는다. 두 줄이 같은
    # 헬퍼를 쓰는지를 여기서 본다.
    from cuesift.cli import _resolve_llm

    monkeypatch.setenv("CUESIFT_BASE_URL", "http://env")
    monkeypatch.setenv("CUESIFT_MODEL", "env-model")
    _base, model, _key = _resolve_llm(_FakeCtx("DEFAULT_MAP"), "http://config", "config-model")
    assert model == "env-model"


def test_실제_translate에서도_환경변수가_설정을_이긴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**단위 테스트만으로는 배선이 지켜지지 않는다.**

    `_resolve_llm`이 옳아도 `translate`가 `ctx`를 넘기지 않으면 `None`이
    들어가 설정이 다시 환경변수를 이긴다. 그 경우 위 다섯은 전부 초록이다.
    `--dry-run` 요약이 실제로 쓰이는 base_url을 찍으므로 여기서 관측한다.
    """
    target = tmp_path / "a.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n", encoding="utf-8")
    cfg = _config(tmp_path, "llm:\n  base_url: http://from-config\n  model: config-model\n")
    monkeypatch.setenv("CUESIFT_BASE_URL", "http://from-env")
    monkeypatch.setenv("CUESIFT_MODEL", "env-model")

    result = runner.invoke(
        app,
        ["--config", str(cfg), "translate", str(target), "--to", "en", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "http://from-env" in result.stdout
    assert "http://from-config" not in result.stdout
    assert "env-model" in result.stdout


def test_실제_translate에서_CLI가_환경변수를_이긴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 양보를 너무 넓게 잡으면(출처를 안 보고 늘 환경변수를 쓰면) 이 건이 깨진다.
    target = tmp_path / "a.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n", encoding="utf-8")
    cfg = _config(tmp_path, "llm:\n  base_url: http://from-config\n  model: config-model\n")
    monkeypatch.setenv("CUESIFT_BASE_URL", "http://from-env")
    monkeypatch.setenv("CUESIFT_MODEL", "env-model")

    result = runner.invoke(
        app,
        [
            "--config",
            str(cfg),
            "translate",
            str(target),
            "--to",
            "en",
            "--dry-run",
            "--base-url",
            "http://from-cli",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "http://from-cli" in result.stdout
    assert "http://from-env" not in result.stdout
