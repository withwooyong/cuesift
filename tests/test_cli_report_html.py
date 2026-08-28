"""`--review-format` 배선 (FR-7.3 · 설계 D1).

이 파일이 고정하는 것은 셋이다 - 형식 스위치가 내는 **산출물 집합** ·
`--review-out` 없이는 쓸 수 없다는 **하위 옵션 관계** · `_review_path`와
**같은 stem 규칙**.

**기본값 테스트가 이 파일의 중심이다.** 기본이 `json`에서 벗어나는 순간
기존 실행의 산출물이 조용히 늘어나고, 그것은 종료 코드로 드러나지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fakes.provider import EchoProvider

# `click.testing`에서 직접 가져오지 않는다 - 이 리포의 런타임 의존성 4개에
# `click`이 없어(typer가 재노출할 뿐) 임포트가 실패한다.
from typer.testing import CliRunner, Result

from conftest import normalize_rich_message
from cuesift.cli import ReviewFormat, _report_path, _review_path, app

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
    """자막 출력을 `subs/` 밑으로 몬다.

    리포트 디렉터리와 겹치면 경로 결정이 통째로 틀려도 `glob`이 뭔가를
    찾아내 통과한다 - `test_cli_review_out.py`가 같은 이유로 같은 형태를 쓴다.
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


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> Result:
    _patch_provider(monkeypatch, EchoProvider())
    return runner.invoke(app, _args(tmp_path, "minimal.srt", *extra))


def test_기본값은_json이라_html이_생기지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기존 실행의 산출물이 변하지 않아야 한다 (설계 D1).

    `--review-format`을 주지 않은 실행이 HTML을 내기 시작하면, CI에서 JSON만
    쓰던 사용자의 디렉터리에 파일이 조용히 늘어난다.
    """
    rp = tmp_path / "rp"
    result = _run(tmp_path, monkeypatch, "--review-budget", "10%", "--review-out", str(rp))

    assert result.exit_code == 0, result.output
    assert list(rp.glob("*.review.json"))
    assert not list(rp.glob("*.report.html"))


def test_html을_주면_html만_생긴다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`html`은 JSON을 **대체한다** - 곁들이는 것이 아니다.

    **여기서만 파일 이름을 통째로 단언한다.** `glob("*.report.html")`의 존재만
    보면 `_report_path`가 통째로 틀린 이름을 내도 통과한다 - 확장자만 맞으면
    되기 때문이다(리뷰 라운드 1 지적). 순수 함수 테스트가 규칙을 재고, 이
    단언이 **그 규칙이 실제 실행까지 이어지는지**를 잰다.
    """
    rp = tmp_path / "rp"
    result = _run(
        tmp_path,
        monkeypatch,
        "--review-budget",
        "10%",
        "--review-out",
        str(rp),
        "--review-format",
        "html",
    )

    assert result.exit_code == 0, result.output
    assert {p.name for p in rp.iterdir()} == {"minimal.en.report.html"}


def test_both를_주면_둘_다_생긴다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rp = tmp_path / "rp"
    result = _run(
        tmp_path,
        monkeypatch,
        "--review-budget",
        "10%",
        "--review-out",
        str(rp),
        "--review-format",
        "both",
    )

    assert result.exit_code == 0, result.output
    assert list(rp.glob("*.review.json"))
    assert list(rp.glob("*.report.html"))


def test_review_out_없이_format만_주면_exit_2다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """낼 곳이 없다.

    조용히 무시하면 사용자는 나오지 않는 파일을 찾아 헤매고, 종료 코드가 0이라
    스크립트는 성공으로 읽는다.

    **사유 문자열까지 단언한다.** 종료 코드만 보면 exit 2를 내는 **다른** 가드가
    이 조합을 가로채도 통과한다 - 그러면 사용자는 엉뚱한 진단을 읽고 엉뚱한
    옵션을 고친다. `test_cli_review_out.py`의 같은 형태 테스트가 같은 이유로
    같은 단언을 쓴다.

    **`normalize_rich_message`를 통과시키는 이유**는 rich 하이라이터가 색이 켜진
    환경에서 옵션 이름을 줄바꿈으로 쪼개기 때문이다 - 원문 그대로 단언하면
    내용이 맞아도 CI에서만 깨진다.
    """
    result = _run(tmp_path, monkeypatch, "--review-budget", "10%", "--review-format", "html")

    assert result.exit_code == 2, result.output
    assert normalize_rich_message("--review-format") in normalize_rich_message(result.output)
    assert normalize_rich_message("--review-out") in normalize_rich_message(result.output)


def test_세_값_밖의_문자열은_exit_2다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """typer의 Enum 검증에 맡긴다 - 우리가 문자열을 파싱하지 않는다."""
    result = _run(
        tmp_path,
        monkeypatch,
        "--review-budget",
        "10%",
        "--review-out",
        str(tmp_path / "rp"),
        "--review-format",
        "pdf",
    )

    assert result.exit_code == 2, result.output


def test_html_파일명이_review_json과_같은_stem_규칙을_쓴다() -> None:
    """`ep01.ko.srt` -> `ep01.en.report.html` (`.ko`가 치환된다).

    **두 함수의 출력을 서로 비교한다.** 각각만 단언하면 둘이 함께 틀리는
    미래의 변경을 통과시킨다 - `test_stem_규칙이_자막_출력과_같다`가 같은
    이유로 같은 형태를 쓴다.
    """
    src = Path("a/ep01.ko.srt")

    review = _review_path(src, Path("reports"), "ko", "en")
    report = _report_path(src, Path("reports"), "ko", "en")

    assert report == Path("reports/ep01.en.report.html")
    assert report.name.removesuffix(".report.html") == review.name.removesuffix(".review.json"), (
        f"리포트({report.name})와 JSON({review.name})의 stem 규칙이 갈라졌다"
    )


def test_대문자_source_태그도_치환된다() -> None:
    """`ep01.KO.srt` -> `ep01.en.report.html`.

    Windows는 파일명 대소문자를 구분하지 않아 `ep01.KO.srt`가 정상이다.
    `stem.casefold()`가 없으면 이중 태그(`ep01.KO.en.report.html`)가 난다.
    """
    got = _report_path(Path("a/ep01.KO.srt"), Path("reports"), "ko", "en")

    assert got == Path("reports/ep01.en.report.html")


def test_대문자_source_lang_인자도_치환된다() -> None:
    """`--source-lang KO`는 CLI 어디에서도 접히지 않고 여기까지 온다.

    `suffix.casefold()`가 없으면 치환에 실패한다 - `_output_path`가 겪은
    사고의 거울상이다.
    """
    got = _report_path(Path("a/ep01.ko.srt"), Path("reports"), "KO", "en")

    assert got == Path("reports/ep01.en.report.html")


def test_source_태그가_없으면_덧붙인다() -> None:
    """치환 분기를 **무조건 타는** 변이를 잡는다.

    조건을 지우면 `ep01`에서 `.ko` 길이만큼 잘려 `ep.en.report.html`이 된다.
    """
    got = _report_path(Path("a/ep01.srt"), Path("reports"), "ko", "en")

    assert got == Path("reports/ep01.en.report.html")


def test_입력이_둘이면_html이_서로를_지우지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """고정 이름을 쓰면 뒤엣것이 앞엣것을 조용히 지우고 종료 코드는 0이다.

    `_review_path` 독스트링이 기록한 사고와 같은 것이다.
    """
    _patch_provider(monkeypatch, EchoProvider())
    rp = tmp_path / "rp"

    for fixture in ("minimal.srt", "crlf_bom.srt"):
        result = runner.invoke(
            app,
            _args(
                tmp_path,
                fixture,
                "--review-budget",
                "10%",
                "--review-out",
                str(rp),
                "--review-format",
                "html",
            ),
        )
        assert result.exit_code == 0, result.output

    assert len(list(rp.glob("*.report.html"))) == 2


def test_review_format_값_목록이_설계_D1과_같다() -> None:
    """설계 D1이 확정한 목록은 json|html|both **뿐**이다.

    수용·거부만 보는 테스트는 네 번째 값이 늘어도 전부 통과한다 - `FailOn`이
    같은 이유로 같은 형태의 집합 동등성 테스트를 갖고 있다
    (`test_fail_on_members_match_the_requirements_doc`).
    """
    assert [member.value for member in ReviewFormat] == ["json", "html", "both"]


def test_html_쓰기_실패는_exit_66이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """디스크 상태의 문제이지 명령줄 오류가 아니다 (설계 §8).

    **번역 파일은 이미 나갔다는 것을 함께 고정한다.** 그 사실을 말하지 않으면
    사용자는 번역까지 실패한 줄 알고 LLM 호출을 통째로 다시 쓴다 - `write_review`
    쪽 그물이 `test_쓰기_실패는_exit_66이다`로 같은 것을 재고 있고, 이 테스트는
    그 형제다.

    **이 게이트가 없으면 `return EXIT_BAD_INPUT`을 `return 0`으로 바꾸는 변이가
    생존한다** - 리뷰 라운드 1에서 실제로 생존했다(전체 1371건 그대로 통과).
    """
    _patch_provider(monkeypatch, EchoProvider())

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("디스크가 가득 찼다")

    monkeypatch.setattr("cuesift.cli.write_html", boom)

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(tmp_path / "rp"),
            "--review-format",
            "html",
        ),
    )

    assert result.exit_code == 66, result.output
    assert (tmp_path / "subs" / "minimal.en.srt").exists(), "번역까지 잃었다"


@pytest.mark.parametrize(
    ("exc", "label"),
    [
        (KeyError("segments"), "KeyError"),
        (ValueError("순환 참조"), "ValueError"),
        (
            UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed"),
            "UnicodeEncodeError",
        ),
    ],
)
def test_html_생성_실패는_예외_타입과_무관하게_exit_70이다(
    exc: Exception, label: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """exit 1로 새면 내부 결함이 자막 결함으로 오보된다 (설계 §8).

    1은 이 CLI에서 "규격 위반 발견 또는 번역 일부 실패"라, 새어 나간 내부 결함이
    **정상 종료와 종료 코드로 구별되지 않는다.** CI는 번역을 재시도하고 리포트는
    영영 안 나온다.

    **세 타입을 모두 도는 것이 이 게이트의 핵심이다.** 하나만 보면
    `except Exception`을 그 타입으로 좁히는 변이가 통과한다. 셋은 실제 도달
    경로에서 골랐다 - `Template.substitute`의 `KeyError`, `write_text`가 서로게이트를
    만났을 때의 `UnicodeEncodeError`, 그리고 그 둘 어디에도 속하지 않는 것 하나.

    **예외 타입명이 메시지에 있는지도 본다.** `{exc}`만 찍으면 `KeyError`(템플릿이
    틀렸다)와 `NameError`(버그를 신고해야 한다)가 사용자에게 같은 모양으로 보여
    넓은 catch의 대가만 남는다.

    **traceback이 없다는 것도 함께 본다.** 미처리 예외로 죽으면 종료 코드가 맞아도
    사용자는 스택트레이스를 보고 "이 도구가 깨졌다"로 읽는다.
    """
    _patch_provider(monkeypatch, EchoProvider())

    def boom(*_args: object, **_kwargs: object) -> None:
        raise exc

    monkeypatch.setattr("cuesift.cli.write_html", boom)

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(tmp_path / "rp"),
            "--review-format",
            "html",
        ),
    )

    assert result.exit_code == 70, result.output
    assert label in result.output, f"예외 타입명({label})이 메시지에 없다"
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("json", {"minimal.en.review.json"}),
        ("html", {"minimal.en.report.html"}),
        ("both", {"minimal.en.review.json", "minimal.en.report.html"}),
    ],
)
def test_dry_run이_예고한_파일과_본_실행이_내는_파일이_같다(
    fmt: str, expected: set[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**dry-run은 본 실행을 확인하는 용도라, 틀린 예고는 무해하지 않다** (설계 D7·D11).

    `--review-format`이 들어오기 전에는 산출물이 하나뿐이라 dry-run이 경로만
    조립해도 어긋날 수 없었다. 형식이 생긴 뒤 **어느 파일이 나가는가**가 판단이
    되었고, 그 판단이 두 곳에 있으면 갈라진다 - 실제로 `--review-format html`이
    나오지도 않을 `.review.json`을 예고했다(리뷰 라운드 1, 두 리뷰어가 독립 지목).

    **예고한 것과 나온 것을 맞대어 재는 것이 이 테스트의 전부다.** 각각만
    단언하면 둘이 **함께** 틀리는 변경을 통과시킨다. 나오지 않을 파일을 예고하지
    않는지(`not in`)까지 보는 이유는, 예고가 넘치는 쪽은 존재 단언만으로는
    영영 잡히지 않기 때문이다.

    **dry-run이 디렉터리를 만들지 않는 것도 함께 고정한다** - 만들면 다음 단계가
    빈 디렉터리를 산출물로 오인한다.
    """
    rp = tmp_path / "rp"
    common = ("--review-budget", "10%", "--review-out", str(rp), "--review-format", fmt)

    dry = _run(tmp_path, monkeypatch, *common, "--dry-run")

    assert dry.exit_code == 0, dry.output
    predicted = normalize_rich_message(dry.output)
    for name in expected:
        assert normalize_rich_message(name) in predicted, f"{name}을 예고하지 않았다"
    for name in {"minimal.en.review.json", "minimal.en.report.html"} - expected:
        assert normalize_rich_message(name) not in predicted, f"{name}은 나오지 않는다"
    assert not rp.exists(), "dry-run이 디렉터리를 만들었다"

    real = _run(tmp_path, monkeypatch, *common)

    assert real.exit_code == 0, real.output
    assert {p.name for p in rp.iterdir()} == expected
