"""CLI 표면(surface) 테스트.

골격 단계이므로 "기능이 동작한다"가 아니라
**인자 스키마와 종료 코드 계약이 유지된다**를 검증한다.
"""

import inspect
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cuesift import __version__
from cuesift.cli import EXIT_NOT_IMPLEMENTED, FailOn, app, check

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
    """요구사항정의서 §8.1 S3의 CI 게이트 호출 형태.

    문서의 예시는 --spec th였으나 내장 프로파일에 th가 없다. Q2가 초기 언어쌍을
    ko→en/ja로 확정했으므로 태국어는 v0.1 범위 밖이고, 문서를 --spec ja로
    정정했다(Task 7·설계 §9).
    """
    result = runner.invoke(
        app,
        ["check", str(FIXTURES / "minimal.srt"), "--spec", "ko", "--fail-on", "hard"],
    )
    assert result.exit_code == 0


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
        # `!= 2`는 check가 골격이던 시절의 단언이다. 이제 세 값 모두 깨끗한 파일에서
        # 정확히 0이므로 좁힌다 — `!= 2`는 70·1·66도 통과시켜 판별력이 거의 없다.
        assert result.exit_code == 0, f"--fail-on {value} 가 파싱되지 않았다"


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
        assert value in result.stderr, f"거부 사유가 --fail-on {value} 가 아니다"


def test_fail_on_members_match_the_requirements_doc():
    """FR-7.5가 정한 목록은 hard|any|none **뿐**이다.

    종료 코드를 보지 않으므로 Task 6이 exit 2의 의미를 넓혀도 판별력이 남는다.
    수용·거부만 보는 테스트는 네 번째 값이 추가돼도 전부 통과한다 —
    FR-7.5는 목록을 확정한 것이므로 집합 동등성이 맞는 단언이다.
    """
    assert [member.value for member in FailOn] == ["hard", "any", "none"]


@pytest.mark.parametrize(
    "args",
    [["--help"], ["check", "--help"], ["translate", "--help"], ["transcribe", "--help"]],
)
def test_help_output_is_encodable_in_the_cp949_locale(args: list[str]):
    """전역 제약 "출력 문자열에 em dash 금지"를 규율이 아니라 게이트로 닫는다.

    `--help`는 **그룹 콜백보다 먼저** 렌더되므로(실측: eager 옵션은 make_context에서
    처리된다) `_harden_output_streams`가 닿지 않는다. 리터럴을 고치는 것 말고는
    막을 방법이 없고, 리터럴은 규율로만 지켜지므로 반드시 새어 나간다 —
    실제로 `translate`·`transcribe` 독스트링의 U+2014가 남아 있어
    `cuesift --help > help.txt`가 cp949 로케일에서 **종료 코드 1**로 죽었다.
    이 저장소에서 1은 "규격 위반 발견"이다.

    커맨드 독스트링이 그대로 help가 되므로 **독스트링도 출력 문자열이다.**
    `·`(U+00B7)·`§`(U+00A7)·`→`(U+2192)는 cp949가 인코딩하므로 계속 써도 된다.

    렌더된 출력 전체를 보는 것은 rich의 테두리 문자까지 포함해야 진짜 계약이기
    때문이다. rich가 둥근 모서리(U+256D)로 바꾸면 이 테스트가 빨개지는데,
    그것은 오탐이 아니라 Windows 사용자에게 실제로 깨지는 회귀다.
    """
    result = runner.invoke(app, args)

    assert result.exit_code == 0
    # 인코딩 불가 문자가 있으면 UnicodeEncodeError로 여기서 터진다.
    result.stdout.encode("cp949")


def test_fail_on_defaults_to_hard():
    """설계 §5.1 — 기본값이 none으로 바뀌면 check가 항상 exit 0이 되어
    '검사하지 않고 통과하는 게이트'가 된다.
    """
    default = inspect.signature(check).parameters["fail_on"].default
    assert default is FailOn.hard
