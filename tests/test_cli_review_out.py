"""`--review-out` CLI 표면 (FR-7.2 · 설계 §5).

**파일을 실제로 쓰는 배선은 여기 없다.** 이 파일이 고정하는 것은 셋이다 -
경로 규칙(`_review_path`) · 옵션 선언 · 조합 검증. 쓰기는 Task 6이 붙인다.
그래서 아래 성공 경로 테스트들은 **파일의 부재를 단언하지 않는다** -
그렇게 하면 Task 6이 반드시 지워야 하는 테스트가 되고, 지워야 하는 게이트는
게이트가 아니다. 단언하지 않는 유일한 예외가 `test_예산만_주면_파일을_쓰지_않는다`
인데, 그쪽은 `--review-out`이 아예 없어서 Task 6 이후에도 참이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fakes.provider import EchoProvider
from typer.testing import CliRunner

from conftest import normalize_rich_message
from cuesift.cli import _review_path, app

runner = CliRunner()

_FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """사용자 환경이 테스트 결과를 바꾸지 못하게 한다."""
    for name in ("CUESIFT_BASE_URL", "CUESIFT_MODEL", "CUESIFT_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: object) -> None:
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: provider)


def _args(tmp_path: Path, fixture: str, *extra: str) -> list[str]:
    """자막 출력은 `subs/` 밑으로 몬다.

    `--out`을 `tmp_path` 자체로 두면 `--review-out`이 낼 것과 같은 디렉터리가
    되어, 경로 결정이 통째로 틀려도 `rglob`이 뭔가를 찾아내 통과한다.
    """
    return [
        "translate",
        str(_FIXTURES / fixture),
        "--to",
        "en",
        "--out",
        str(tmp_path / "subs"),
        "--base-url",
        "http://h/v1",
        "--model",
        "m1",
        "--no-cache",
        *extra,
    ]


def test_stem_규칙이_자막_출력과_같다() -> None:
    """설계 D2 - 고정 이름은 입력 파일 여럿을 같은 디렉터리로 낼 때 서로를 지운다.

    네 값(`a` · `reports` · `ko` · `en`)을 **전부 다르게** 골랐다. 하나라도
    겹치면 그 축의 바꿔치기 변이가 살아남는다 - 예를 들어 출력 이름에
    `target_lang` 대신 `source_lang`을 쓰는 변이는 둘이 같으면 안 잡힌다.
    """
    got = _review_path(Path("a/ep01.ko.srt"), Path("reports"), "ko", "en")

    assert got == Path("reports/ep01.en.review.json")


def test_source_태그가_없으면_덧붙인다() -> None:
    """치환 분기를 **무조건 타는** 변이를 잡는 유일한 케이스다.

    조건을 지우면 `ep01`에서 `.ko` 길이만큼 잘려 `ep.en.review.json`이 된다.
    위 테스트만으로는 그것이 드러나지 않는다.
    """
    got = _review_path(Path("a/ep01.srt"), Path("reports"), "ko", "en")

    assert got == Path("reports/ep01.en.review.json")


def test_대문자_source_태그도_치환된다() -> None:
    """Windows는 파일명 대소문자를 구분하지 않아 `ep01.KO.srt`가 정상인 파일명이다.

    `endswith`가 대소문자를 구분해 치환에 실패하면 `ep01.KO.en.review.json`이라는
    이중 태그가 난다 - `_output_path`가 같은 사고를 이미 겪었다.
    """
    got = _review_path(Path("a/ep01.KO.srt"), Path("reports"), "ko", "en")

    assert got == Path("reports/ep01.en.review.json")


def test_대문자_source_lang_인자도_치환된다() -> None:
    """위 테스트의 **거울상**이다. 접어야 할 것이 파일명이 아니라 인자다.

    `stem.casefold()`만 두고 `suffix.casefold()`를 빼면 위 테스트는 통과하고
    이것만 죽는다 - 접기가 한쪽에만 걸린 변이는 대문자 파일명으로는
    드러나지 않는다.
    `--source-lang`은 CLI 어디에서도 접히지 않은 채 여기까지 온다
    (`cli.py`의 `_output_path` 호출부 - `--to`만 `load_builtin` 조회용으로
    `.lower()`를 거치고 원본은 그대로 경로에 쓰인다).
    """
    got = _review_path(Path("a/ep01.ko.srt"), Path("reports"), "KO", "en")

    assert got == Path("reports/ep01.en.review.json")


def test_review_out_단독은_exit_2다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """설계 D10 - 리포트를 기대했는데 조용히 안 나오는 것이 최악이다."""
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app, _args(tmp_path, "minimal.srt", "--review-out", str(tmp_path / "reports"))
    )

    assert result.exit_code == 2, result.output
    # **원문에 그대로 단언하지 않는다.** 지금은 `_echo`가 내는 평문이라
    # 통과하지만, 이 메시지가 언젠가 click/rich 경로로 옮겨지면 하이라이터가
    # `--review-out`을 `-`·`-review`·`-out`으로 쪼개 토큰 안쪽에 ANSI를 박는다
    # (실측: `test_cli_triage.py`의 `--review-threshold` 단언이 그렇게 죽었다.
    # 로컬 Windows는 색이 꺼져 통과하고 CI Linux만 실패했다).
    assert normalize_rich_message("--review-out") in normalize_rich_message(result.output)


def test_review_out_단독은_dry_run에서도_exit_2다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """설계 D11 - 조합 오류는 실행 전에 알아야 한다.

    `and not dry_run`으로 미루면 사용자가 dry-run으로 확인하고 본 실행에서야
    오류를 만난다. 프로파일 전량 검사가 이미 같은 규칙을 따른다.
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        _args(tmp_path, "minimal.srt", "--review-out", str(tmp_path / "reports"), "--dry-run"),
    )

    assert result.exit_code == 2, result.output


def test_예산과_함께_주면_받아들인다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**거부 조건의 여집합을 실제로 밟는 케이스다.**

    이것이 없으면 조합 검증을 `if review_out is not None:` 한 줄로 축약한
    변이가 **모든 실패 케이스를 그대로 통과한다** - 실패만 보는 테스트는
    "항상 거부한다"와 "조건부로 거부한다"를 구별하지 못한다.
    `review_budget is None` 항을 통째로 지운 변이도 이 테스트만이 죽인다.

    **파일 유무는 단언하지 않는다** - 쓰기는 Task 6이 붙이므로, 지금 부재를
    고정하면 Task 6이 그 줄을 지워야 한다. 여기서 고정하는 계약은 "이 조합이
    사용법 오류가 아니다"뿐이다.
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        _args(
            tmp_path, "minimal.srt", "--review-budget", "10%", "--review-out", str(tmp_path / "rp")
        ),
    )

    assert result.exit_code == 0, result.output


def test_임계값과_함께_주면_받아들인다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """위 테스트의 **다른 절반**이다. FR-6.3은 두 방식을 대등하게 둔다.

    예산 쪽만 있으면 `review_threshold is None` 항을 지운 변이가 살아남는다 -
    `--review-out --review-threshold`가 사용법 오류로 거부되는데 아무도 못 본다.
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-threshold",
            "0.5",
            "--review-out",
            str(tmp_path / "rp"),
        ),
    )

    assert result.exit_code == 0, result.output


def test_예산만_주면_파일을_쓰지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--review-out` 없이 트리아지만 요청한 기존 사용법이 그대로 돈다."""
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt", "--review-budget", "10%"))

    assert result.exit_code == 0, result.output
    assert list(tmp_path.rglob("*.review.json")) == []


def test_review_out이_파일이면_exit_2다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """typer `file_okay=False`가 먼저 거른다 - `--out`과 같은 방어다.

    **예산을 함께 준다.** 안 주면 우리 조합 검증도 exit 2를 내므로 어느 쪽이
    잡았는지 구별되지 않고, `file_okay=False`를 지워도 통과한다.
    """
    blocker = tmp_path / "notadir"
    blocker.write_text("파일이다", encoding="utf-8")
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        _args(tmp_path, "minimal.srt", "--review-budget", "10%", "--review-out", str(blocker)),
    )

    assert result.exit_code == 2, result.output
