"""CLI 표면(surface) 테스트.

골격 단계이므로 "기능이 동작한다"가 아니라
**인자 스키마와 종료 코드 계약이 유지된다**를 검증한다.
"""

from typer.testing import CliRunner

from cuesift import __version__
from cuesift.cli import EXIT_NOT_IMPLEMENTED, app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_no_args_shows_help():
    result = runner.invoke(app, [])
    assert "translate" in result.output
    assert "check" in result.output
    assert "transcribe" in result.output


def test_translate_accepts_documented_flags():
    """요구사항정의서 §8.1 S1의 호출 형태가 파싱되는지 확인한다."""
    result = runner.invoke(
        app,
        ["translate", "episode01.ko.srt", "--to", "en,ja,th,vi", "--review-budget", "10%"],
    )
    # 파싱 실패(2)가 아니라 미구현(70)으로 끝나야 한다.
    assert result.exit_code == EXIT_NOT_IMPLEMENTED


def test_check_accepts_documented_flags():
    """요구사항정의서 §8.1 S3의 CI 게이트 호출 형태."""
    result = runner.invoke(app, ["check", "episode01.th.srt", "--spec", "th", "--fail-on", "hard"])
    assert result.exit_code == EXIT_NOT_IMPLEMENTED


def test_transcribe_accepts_documented_flags():
    result = runner.invoke(app, ["transcribe", "episode02.mp4", "--source-lang", "ko"])
    assert result.exit_code == EXIT_NOT_IMPLEMENTED


def test_unknown_flag_is_a_usage_error():
    """미구현 코드(70)와 사용법 오류(2)가 섞이지 않는지 확인한다."""
    result = runner.invoke(app, ["translate", "a.srt", "--to", "en", "--nope"])
    assert result.exit_code == 2
