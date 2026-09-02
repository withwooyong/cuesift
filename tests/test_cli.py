"""CLI 표면(surface) 테스트.

골격 단계이므로 "기능이 동작한다"가 아니라
**인자 스키마와 종료 코드 계약이 유지된다**를 검증한다.
"""

import inspect
from pathlib import Path

import pytest
from tests.fakes.provider import EchoProvider
from typer.testing import CliRunner

from conftest import strip_rich_decoration
from cuesift import __version__, cli
from cuesift.cli import FailOn, app, check

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


def test_translate_accepts_documented_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """요구사항정의서 §8.1 S1의 호출 형태가 파싱되는지 확인한다.

    **Task 4에서 `translate`가 골격을 벗어난 뒤로는 종료 코드 70을
    기대할 수 없다.** 이전 판은 `episode01.ko.srt`가 존재하지 않아도 골격이
    입력을 열어 보지 않아 항상 70으로 끝났는데, 지금은 `check`와 같은 이유로
    `exists=True`가 본문 전에 존재를 확인한다(그렇지 않으면
    `test_없는_파일은_exit_2다`가 요구하는 계약이 깨진다). 그래서 이 테스트도
    실재하는 파일과 네트워크를 타지 않는 가짜 프로바이더로 옮겨, "문서화된
    호출 형태가 실제로 실행까지 간다"는 더 강한 확인으로 바꾼다 - `check`가
    골격을 벗어났을 때 `test_fail_on_accepts_the_documented_values`가 같은
    방식으로 갱신된 선례를 따른다.
    """
    source = tmp_path / "episode01.ko.srt"
    source.write_bytes((FIXTURES / "minimal.srt").read_bytes())
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(source),
            "--to",
            "en,ja,th,vi",
            "--review-budget",
            "10%",
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0, result.output


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
    """**70을 기대하지 않는다**(G7). FR-8.3 배선으로 그 발신처가 사라졌다.

    STT 설정을 주지 않았으므로 종료 코드 2다 - 플래그가 파싱된다는 것과
    설정이 갖춰졌다는 것은 다르고, 이 테스트가 보는 것은 앞쪽이다.

    **`episode02.mp4`는 존재하지 않으므로 typer의 `exists=True`가 먼저
    잡는다.** 그것도 2라 단언은 참이지만 이유가 다르므로, 파싱 자체는
    아래 한 줄이 본다.
    """
    result = runner.invoke(app, ["transcribe", "episode02.mp4", "--source-lang", "ko"])
    assert result.exit_code == 2
    assert "No such option" not in result.output


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


# help이 렌더되는 네 경로. **두 테스트가 같은 목록을 봐야** 한쪽만 갱신되는 드리프트가
# 생기지 않는다 — 새 서브커맨드를 추가하면 여기 한 줄만 늘린다.
_HELP_INVOCATIONS = [
    ["--help"],
    ["check", "--help"],
    ["translate", "--help"],
    ["transcribe", "--help"],
]


@pytest.mark.parametrize("args", _HELP_INVOCATIONS)
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

    **rich의 테두리는 검사하지 않는다.** 이전 판은 렌더된 출력 전체를 검사하며
    "rich가 둥근 모서리로 바꾸면 Windows 사용자에게 깨지는 회귀"라고 적었는데,
    **실측으로 셋 다 틀린 것으로 드러났다**(근거는 `conftest.py`의 표).

    1. Windows의 rich는 애초에 둥근 모서리를 안 쓴다 — `legacy_windows=True`라
       `┌┐└┘`(cp949에 있다)를 그린다. 둥근 `╭`는 **Linux에서만** 나온다
    2. 사용자가 `cuesift --help > help.txt`를 하면 rich는 **박스를 아예 안 그린다**
       (터미널이 아니라서). 실측: 출력 1381바이트에 박스 문자 0개, exit 0
    3. 따라서 이 단언은 Windows에서 통과하고 **Linux CI에서만 실패했다** —
       보호하려던 사용자에게는 일어날 수 없는 조건을 검사한 것이다

    지키는 계약은 **"우리가 쓴 문자열이 cp949에서 인코딩된다"** 하나다.
    """
    result = runner.invoke(app, args)

    assert result.exit_code == 0
    # rich가 그린 테두리를 걷어내고 **우리 문자열만** 검사한다.
    # 인코딩 불가 문자가 있으면 UnicodeEncodeError로 여기서 터진다.
    strip_rich_decoration(result.stdout).encode("cp949")


def test_help_output_has_no_em_dash():
    """위 테스트가 지키는 계약을 **이름으로** 못 박는다.

    `encode("cp949")`는 "무언가 인코딩되지 않는다"까지만 말한다. 이 저장소가 실제로
    한 번 데인 것은 **em dash(U+2014)** 이고(`translate`·`transcribe` 독스트링에 남아
    `cuesift --help > help.txt`가 종료 코드 1로 죽었다), 1은 "규격 위반 발견"이다.

    두 테스트를 함께 두는 이유: 위쪽은 **범위**가 넓고(cp949 전체) 이쪽은 **원인**을
    지목한다. 위쪽만 있으면 실패했을 때 무엇을 고쳐야 하는지가 메시지에 없다.
    """
    for args in _HELP_INVOCATIONS:
        out = runner.invoke(app, args).stdout
        assert "—" not in out, f"{args}: em dash가 help에 있다. ASCII 하이픈을 쓴다"


def test_fail_on_defaults_to_hard():
    """설계 §5.1 — 기본값이 none으로 바뀌면 check가 항상 exit 0이 되어
    '검사하지 않고 통과하는 게이트'가 된다.
    """
    default = inspect.signature(check).parameters["fail_on"].default
    assert default is FailOn.hard


def test_output_path는_suffix를_반드시_받는다() -> None:
    """설계 D6. **기본값을 두면 위험한 쪽이 기본이 된다.**

    이 게이트는 동작이 아니라 **시그니처**를 본다 - 기본값
    (`suffix: str = ""` 또는 `input_path.suffix`)을 되돌려 넣는 변이는
    기존 호출부의 출력이 같아 다른 어떤 테스트로도 죽지 않는다. 다음에 영상
    경로를 하나 더 붙이는 사람이 값을 넘기지 않으면 `TypeError`를 받는다 -
    조용한 실패가 시끄러운 실패가 된다.
    """
    with pytest.raises(TypeError):
        cli._output_path(Path("talk.mp4"), None, "ko", "ko")  # type: ignore[call-arg]


def test_output_path가_입력_확장자를_물려받지_않는다() -> None:
    """C1. 예전 판은 `talk.ko.mp4`라는 이름의 SRT 파일을 만든다.

    **확장자만 다르고 예외는 없다** - 플레이어가 열지 못하는 파일이 조용히
    생기고 종료 코드는 0이다.
    """
    assert cli._output_path(Path("talk.mp4"), None, "ko", "ko", suffix=".srt") == Path(
        "talk.ko.srt"
    )
    # 이미 태그가 붙은 입력도 같은 출력을 낸다 - 치환 규칙이 작동한다.
    assert cli._output_path(Path("talk.ko.mp4"), None, "ko", "ko", suffix=".srt") == Path(
        "talk.ko.srt"
    )
