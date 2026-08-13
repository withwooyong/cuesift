"""CLI 표면(surface) 테스트.

골격 단계이므로 "기능이 동작한다"가 아니라
**인자 스키마와 종료 코드 계약이 유지된다**를 검증한다.
"""

from pathlib import Path

from typer.testing import CliRunner

from cuesift import __version__
from cuesift.cli import EXIT_NOT_IMPLEMENTED, app

runner = CliRunner()

# 픽스처 경로는 __file__ 기준으로 잡는다. 상대 경로로 두면 리포 루트가 아닌 곳에서
# pytest를 실행할 때 파일을 찾지 못해, 인자 파싱을 보는 테스트가 엉뚱한 이유로 실패한다.
FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


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


def test_fail_on_accepts_the_documented_values():
    """FR-7.5가 정한 값은 hard|any|none이다 (설계 §5.2)."""
    for value in ("hard", "any", "none"):
        result = runner.invoke(
            app,
            ["check", str(FIXTURES / "minimal.srt"), "--spec", "ko", "--fail-on", value],
        )
        assert result.exit_code != 2, f"--fail-on {value} 가 파싱되지 않았다"


def test_fail_on_rejects_the_removed_values():
    """soft·never는 요구사항정의서에 없는 이름이라 제거됐다.

    이름이 남아 있으면 사용자가 문서에 없는 값을 쓰고도 통과한다.
    """
    for value in ("soft", "never"):
        result = runner.invoke(
            app,
            ["check", str(FIXTURES / "minimal.srt"), "--spec", "ko", "--fail-on", value],
        )
        assert result.exit_code == 2, f"--fail-on {value} 가 아직 받아들여진다"
