"""`cuesift translate`의 트리아지 배선 검증 (FR-6.3 · 설계 §5·§7).

**네트워크를 타지 않는다.** `_build_provider`를 monkeypatch해 가짜를 꽂는다 -
`test_cli_translate.py`와 같은 방식이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fakes.provider import EchoProvider
from typer.testing import CliRunner

from cuesift.cli import _parse_review_budget, app

_FIXTURES = Path(__file__).parent / "fixtures" / "ingest"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """사용자 환경이 테스트 결과를 바꾸지 못하게 한다."""
    for name in ("CUESIFT_BASE_URL", "CUESIFT_MODEL", "CUESIFT_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: object) -> None:
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: provider)


def _args(tmp_path: Path, fixture: str, *extra: str) -> list[str]:
    return [
        "translate",
        str(_FIXTURES / fixture),
        "--to",
        "en",
        "--out",
        str(tmp_path),
        "--base-url",
        "http://h/v1",
        "--model",
        "m1",
        "--no-cache",
        *extra,
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10%", 0.10),
        ("0.1", 0.10),
        ("5%", 0.05),
        ("0", 0.0),
        ("0%", 0.0),
        ("100%", 1.0),
        ("1.0", 1.0),
        # `1`은 100%다. `1%`를 의도한 사용자가 전량을 받지만 Tier 0만 쓰므로
        # LLM 비용이 0이고 요약이 "실제 100.0%"를 내 즉시 드러난다(설계 §5.2).
        ("1", 1.0),
        ("  10%  ", 0.10),
    ],
)
def test_비율을_파싱한다(raw: str, expected: float) -> None:
    assert _parse_review_budget(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw",
    [
        "50",  # 개수 지정 - 범위 밖이다
        "-5%",
        "1.5",
        "101%",
        "abc",
        "",
        "   ",
        "%",
    ],
)
def test_잘못된_값은_ValueError다(raw: str) -> None:
    with pytest.raises(ValueError):
        _parse_review_budget(raw)


@pytest.mark.parametrize("raw", ["nan", "inf"])
def test_NaN과_inf는_범위_검사가_거부한다(raw: str) -> None:
    """**타입이 아니라 메시지를 단언한다.**

    `float("nan")`은 파싱 자체는 성공하고 `nan <= 1.0`이 False라 **범위
    검사**에서 걸린다 - `_parse_review_budget`의 독스트링이 그것을 의도라고
    선언한다. 타입만 단언하면 누군가 `if math.isnan(value): raise
    ValueError("...숫자로 읽지 못했다...")`를 앞에 끼워 넣어도 전부 통과하고,
    오류 메시지가 범위·개수 안내를 잃는다 - 독스트링이 약속한 가드가 실제로는
    사라진 상태를 게이트가 못 잡는다.
    """
    with pytest.raises(ValueError, match="범위를 벗어났다"):
        _parse_review_budget(raw)


def test_개수를_주면_비율로_지정하라고_안내한다() -> None:
    with pytest.raises(ValueError, match="비율로 지정하라"):
        _parse_review_budget("50")


def test_두_정책을_함께_주면_exit_2다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-threshold",
            "0.7",
        ),
    )

    assert result.exit_code == 2, result.output
    # 두 옵션 이름이 모두 나와야 사용자가 무엇을 지울지 안다.
    assert "--review-budget" in result.output
    assert "--review-threshold" in result.output


def test_예산_파싱_실패는_exit_2다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt", "--review-budget", "50"))

    assert result.exit_code == 2, result.output
    assert "비율로 지정하라" in result.output


def test_프로파일이_없는_언어는_경고하고_건너뛴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D7 — 전량 거부하면 프로파일이 **있는** 언어의 트리아지까지 잃는다.

    요구사항정의서 §8.1 S3의 문서화된 호출이 `--to en,ja,th,vi`인데 th·vi
    프로파일은 없다(`tests/test_cli.py:57-73`이 그것을 exit 0으로 고정한다).
    선례도 있다 - `cli.py:869-877`이 프로바이더가 `cache_identity`를 주지
    않으면 경고하고 캐시를 끈다("조용히 끄지는 않는다").

    **건너뛰는 것은 트리아지이지 번역이 아니다.** fr도 번역 파일은 나온다 -
    이것이 "그 언어를 통째로 드롭"과 갈리는 지점이라 파일 존재로 못 박는다.
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en,fr",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--no-cache",
            "--review-budget",
            "10%",
        ],
    )

    assert result.exit_code == 0, result.output
    # load_builtin의 메시지를 그대로 전달한다 - 사용 가능 목록이 거기 있다.
    assert "사용 가능" in result.output
    assert "[fr]" in result.output
    # 프로파일이 있는 언어는 걸러지지 않는다 - 이것이 전량 거부와 갈리는 지점이다.
    # Task 3이 "[en] 트리아지" 요약 블록을 내면 그 문구로 단언을 강화한다.
    assert "[en] 경고: 규격 프로파일이 없어" not in result.output
    assert (tmp_path / "minimal.en.srt").exists()
    assert (tmp_path / "minimal.fr.srt").exists()


def test_트리아지할_언어가_하나도_없으면_exit_2다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling 3a + D13 — 요청이 통째로 무시되는 경우만 사용법 오류다.

    **프로바이더 호출 0회를 단언하는 것이 이 테스트의 요점이다.** exit 2만
    보면 "언제" 죽었는지 알 수 없어, LLM 비용을 쓴 뒤 죽는 구현도 통과한다.
    """
    provider = EchoProvider()
    _patch_provider(monkeypatch, provider)

    # `_args`를 쓰지 않는다 - 그것은 `--to en`을 주므로 프로파일이 존재해
    # 번역이 실제로 돌고, 그러면 `provider.calls == []` 단언이 무의미해진다.
    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "fr",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--no-cache",
            "--review-budget",
            "10%",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "적용할 수 있는 대상 언어가 없다" in result.output
    assert provider.calls == [], "프로파일 검증 전에 번역을 호출했다"


def test_정책이_없으면_기존_동작이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """하위 호환 - 두 옵션이 없으면 트리아지가 돌지 않는다."""
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt"))

    assert result.exit_code == 0, result.output
    assert "트리아지" not in result.output
