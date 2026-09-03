"""`cuesift translate`의 트리아지 배선 검증 (FR-6.3 · 설계 §5·§7).

**네트워크를 타지 않는다.** `_build_provider`를 monkeypatch해 가짜를 꽂는다 -
`test_cli_translate.py`와 같은 방식이다.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from tests.fakes.provider import EchoProvider
from typer.testing import CliRunner

from conftest import blank_at, normalize_rich_message
from cuesift.cli import (
    EXIT_TRANSLATION_FAILURE,
    _format_triage_summary,
    _parse_review_budget,
    app,
)
from cuesift.report import TriageOutcome
from cuesift.segment import Segment, SegmentRisk
from cuesift.spec import SpecProfile, available_builtins, load_builtin
from cuesift.translate.provider import Completion, TokenUsage

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


def test_개수를_주면_review_top_k를_안내한다() -> None:
    """**`--review-top-k`를 이름으로 말해야 한다** (FR-6.3 ①).

    `select_by_count`가 생기기 전에는 "개수 지정은 v0.1 범위 밖이다"가 참이었다.
    지금은 거짓이고, 그 문구가 남으면 CLI가 **이 저장소가 방금 만든 기능을
    없다고 말한다.** `50`을 친 사람은 십중팔구 50개를 원한 사람이다.
    """
    with pytest.raises(ValueError, match="--review-top-k"):
        _parse_review_budget("50")


def test_세_정책_옵션의_help가_같은_배타_문구를_말한다() -> None:
    """세 옵션이 **서로 다른 부분집합**을 말하면 금지된 조합이 어디에도 안 적힌다.

    실제로 `--review-threshold 0.5 --review-top-k 5`가 exit 2인데 두 옵션의
    help 어디에도 그 금지가 없었다. 셋이 **같은 문구**를 쓰면 어느 것을
    읽어도 같은 제약을 만난다.

    **렌더가 아니라 click 파라미터를 본다.** `--help` 출력에 단언하면 rich가
    폭에 따라 문구를 접어 이 게이트가 폭 테스트가 된다 - 옵션 이름이 쪼개진
    사건과 같은 부류다. 렌더 폭은 `test_cli_progress.py`가 따로 잰다.
    """
    import typer

    translate = typer.main.get_command(app).commands["translate"]
    helps = {
        param.name: param.help
        for param in translate.params
        if param.name in {"review_budget", "review_threshold", "review_top_k"}
    }
    assert len(helps) == 3, helps
    suffixes = {text.split(". ")[-1] for text in helps.values()}
    # 하나라도 다른 상대를 나열하면 집합의 크기가 1이 아니다.
    assert len(suffixes) == 1, helps
    # 문구가 셋을 다 가리켜야 한다 - "둘 중 하나"로 좁히면 대칭이어도 거짓이다.
    suffix = suffixes.pop()
    for word in ("비율", "임계값", "개수"):
        assert word in suffix, suffix


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
    # **`output`이 아니라 `stderr`를 본다.** `output`은 stdout+stderr 합본이라
    # `_echo(..., err=True)`의 `err=True`를 지워도 통과한다 - 이 파일의 다른
    # 테스트가 전부 그렇다. 한 곳이라도 스트림을 갈라 봐야 "오류는 stderr로
    # 낸다"가 관례가 아니라 게이트가 된다.
    assert result.stdout == "", "사용법 오류가 stdout으로 샜다"
    # 두 옵션 이름이 모두 나와야 사용자가 무엇을 지울지 안다.
    assert "--review-budget" in result.stderr
    assert "--review-threshold" in result.stderr
    # **치지 않은 세 번째를 말하지 않는다**(설계 D7). 고정 문자열로 셋을
    # 나열하면 여기가 실패한다 - 사용자는 자기 명령줄에 없는 옵션을
    # 오류에서 읽고 그것을 지우려 든다.
    assert "--review-top-k" not in result.stderr


def test_예산_파싱_실패는_exit_2다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt", "--review-budget", "50"))

    assert result.exit_code == 2, result.output
    # 화면에도 개수 축의 옵션 이름이 나와야 한다 - 독스트링만 고치면
    # 사용자가 보는 문구는 그대로 "v0.1 범위 밖"에 머문다.
    assert "--review-top-k" in result.output


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
    assert "[fr] 경고" in result.output
    # 프로파일이 있는 언어는 걸러지지 않는다 - 이것이 전량 거부와 갈리는 지점이다.
    # **경고 유무만으로는 en이 실제로 처리됐는지 알 수 없어** 트리아지 출력과
    # 산출 파일을 함께 본다. 양성 단언(`"[en] 트리아지" in`)이 핵심이다 -
    # 부정 단언만으로는 en의 트리아지가 통째로 빠져도 통과한다.
    assert "[en] 트리아지" in result.output
    assert "[en] 경고: 규격 프로파일이 없어" not in result.output
    assert (tmp_path / "minimal.en.srt").exists()
    # 건너뛴 것은 트리아지이지 번역이 아니다 - "그 언어를 통째로 드롭"과 갈린다.
    assert (tmp_path / "minimal.fr.srt").exists()


@pytest.mark.parametrize(
    ("option", "value"),
    [("--review-budget", "10%"), ("--review-threshold", "0.7")],
)
def test_트리아지할_언어가_하나도_없으면_exit_2다(
    option: str, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling 3a + D13 — 요청이 통째로 무시되는 경우만 사용법 오류다.

    **프로바이더 호출 0회를 단언하는 것이 이 테스트의 요점이다.** exit 2만
    보면 "언제" 죽었는지 알 수 없어, LLM 비용을 쓴 뒤 죽는 구현도 통과한다.

    **두 옵션 모두로 돌린다.** `--review-budget`만 보면 `triage_requested`에서
    `or review_threshold is not None`을 지워도 전 스위트가 통과한다 - 임계값
    경로의 D13 비용 게이트가 통째로 비게 된다.
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
            option,
            value,
        ],
    )

    assert result.exit_code == 2, result.output
    assert "적용할 수 있는 대상 언어가 없다" in result.output
    assert provider.calls == [], "프로파일 검증 전에 번역을 호출했다"


@pytest.mark.parametrize("raw", ["nan", "1.5", "-0.1", "inf"])
def test_임계값이_범위를_벗어나면_exit_2다(
    raw: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--review-threshold`의 범위 검사가 **NaN까지** 막는지 고정한다.

    `nan`은 click의 `min`/`max`가 통과시킨다 - `lt(nan, 0.0)`도
    `gt(nan, 1.0)`도 False이기 때문이다. 그것이 새는 것을 여기서 잡지 않으면
    Task 3의 `select_by_threshold`가 던지는 ValueError가 미처리 traceback으로
    **exit 1**이 되고, exit 1은 이 CLI에서 "규격 위반 발견"이라 설정 실수가
    자막 결함으로 오보된다.

    `1.5`·`-0.1`·`inf`는 click이 잡는다. **누가 잡든 결과가 같아야 한다**는
    것이 이 테스트가 고정하는 계약이므로 넷을 같은 자리에서 본다 - click의
    동작이 바뀌어도 여기서 드러난다.

    **`nan`과 나머지 셋은 출력 경로가 다르다.** `nan`은 우리 가드가 잡아
    `_echo`로 평문을 내지만, 셋은 click이 잡아 **rich가 렌더한다.** 그래서
    옵션 이름 단언에 `normalize_rich_message`가 필요하다 - 아래 참고.
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt", "--review-threshold", raw))

    assert result.exit_code == 2, result.output
    # `cli.py`의 `min`/`max` 주석이 "오류 메시지가 옵션 이름을 말한다"를
    # 정당화의 근거로 든다. 그 약속을 게이트로 만든다.
    #
    # **원문에 그대로 단언하면 색이 켜진 환경에서 죽는다.** rich의
    # 하이라이터가 `--review-threshold`를 세 조각(`-`·`-review`·`-threshold`)
    # 으로 나눠 각각 style을 입히므로 토큰 **안쪽에** ANSI가 박힌다
    # (실측: `\x1b[1;36m-\x1b[0m\x1b[1;36m-review\x1b[0m\x1b[1;36m-threshold\x1b[0m`).
    # 부분 문자열 `--review-threshold`는 그 순간 사라진다.
    #
    # 로컬(Windows)은 색이 꺼져 통과하고 CI(Linux)는 켜져 죽었다 - 종료 코드는
    # 양쪽 다 2였으므로 **동작이 아니라 관측 방법이 플랫폼에 종속된 것**이다.
    # 재현: `FORCE_COLOR=1 pytest`.
    assert normalize_rich_message("--review-threshold") in normalize_rich_message(result.output)
    assert not list(tmp_path.glob("*.srt")), "검증 실패인데 번역 파일이 나왔다"


def test_대문자_언어_태그도_프로파일을_찾는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**플랫폼 의존을 막는다.** 접지 않으면 Windows 0 · Linux 2로 갈린다.

    `_LANG_TAG_RE`가 `[A-Za-z]`로 대문자를 허용하는데 `load_builtin`은
    `specs/<name>.yaml`을 파일로 찾는다. Windows는 파일명 대소문자를 구분하지
    않아 `EN`이 `en.yaml`을 찾아내지만 CI의 Linux(`ubuntu-latest`)는 구분해
    "프로파일 없음"이 되고, 유일한 대상이므로 exit 2가 된다.

    **이 테스트는 개발 플랫폼(Windows)에서는 접기 전에도 통과한다** - 잠기는
    곳은 CI다. 같은 이유로 `_resolve_profile`·`_output_path`가 이미 같은
    처리를 한다.
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt", "--review-budget", "10%"))
    assert result.exit_code == 0, result.output

    upper = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "EN",
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

    assert upper.exit_code == 0, upper.output
    assert "경고: 규격 프로파일이 없어" not in upper.output
    # 조회만 접는다 - 출력 파일명은 사용자가 준 태그를 그대로 쓴다.
    assert (tmp_path / "minimal.EN.srt").exists()
    # **여기가 라벨과 프로파일 이름이 갈리는 유일한 자리다.** 라벨은 사용자가 준
    # `EN`이고 프로파일은 접어서 찾은 `en`이다. 두 값이 같은 케이스에서만
    # 단언하면 `profile_name`을 `target_lang`으로 바꿔치기해도 전 스위트가
    # 통과한다(실측: 1089 passed, 리뷰 축B I2). 이 한 줄이 그것을 닫는다.
    assert "[EN] 트리아지 (예산 10%, 프로파일 en)" in upper.output


def test_대문자_언어_태그는_대소문자_구분_파일시스템에서도_찾는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**위 테스트는 개발 플랫폼에서 죽지 않는다.** 그것을 이 테스트가 메운다.

    Windows는 파일명 대소문자를 구분하지 않아 `.lower()`를 빼도 위 테스트가
    통과한다 - 개발 기계에서 실패시켜 볼 수 없는 게이트이고, 이 저장소가
    금지하는 "검사하지 않고 통과하는 게이트"에 해당한다. CI(`ubuntu-latest`)만
    잡아 주는 상태로 두지 않으려고 `load_builtin`을 대소문자 구분 버전으로
    감싸 Linux를 흉내 낸다.
    """
    real = load_builtin

    def case_sensitive(name: str) -> SpecProfile:
        # `Path.is_file()`이 대소문자를 구분하는 파일시스템의 동작이다.
        if name not in available_builtins():
            raise FileNotFoundError(
                f"'{name}' 프로파일이 없다. 사용 가능: {', '.join(available_builtins())}"
            )
        return real(name)

    monkeypatch.setattr("cuesift.cli.load_builtin", case_sensitive)
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "EN",
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
    assert "경고: 규격 프로파일이 없어" not in result.output


def test_정책이_없으면_기존_동작이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """하위 호환 - 두 옵션이 없으면 트리아지가 돌지 않는다."""
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt"))

    assert result.exit_code == 0, result.output
    assert "트리아지" not in result.output


def test_dry_run은_트리아지를_돌리지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """README가 "dry-run에는 트리아지가 반영되지 않는다"를 약속한다.

    **문서가 약속한 동작에 게이트가 없으면 이 저장소가 1급으로 금지하는
    상태다.** dry-run은 번역을 하지 않으므로 트리아지도 돌 것이 없는데,
    그 사실이 코드 어디에도 단언돼 있지 않아 `_dry_run_report` 뒤에
    트리아지를 얹는 변경이 조용히 통과한다.

    `--review-budget`을 **주고도** 요약이 없어야 한다는 것이 요점이다 -
    옵션을 안 주고 검사하면 `triage_requested`가 거짓이라 아무것도 증명하지
    못한다(그 경로는 `test_정책이_없으면_기존_동작이다`가 이미 본다).

    **낱말 `"트리아지"` 하나로 단언하면 안 된다.** pytest가 `tmp_path`를
    **테스트 함수 이름으로** 짓는데 그 이름에 "트리아지"가 들어 있고,
    dry-run 출력이 출력 경로를 찍으므로 임시 디렉터리 이름이 그대로 걸린다
    (실측: 이 테스트의 첫 판이 그렇게 실패했다). 요약 헤더의 고유한 모양인
    `"] 트리아지 ("`로 좁힌다 - 경로에는 이 조합이 나올 수 없다.
    """
    provider = EchoProvider()
    _patch_provider(monkeypatch, provider)

    result = runner.invoke(
        app, _args(tmp_path, "ten_cues.srt", "--review-budget", "10%", "--dry-run")
    )

    assert result.exit_code == 0, result.output
    assert "] 트리아지 (" not in result.output
    assert "검수 대상" not in result.output
    assert provider.calls == [], "dry-run이 프로바이더를 호출했다"


def test_dry_run에서도_프로파일_검증은_돈다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D13 — 프로파일 전량 검사는 `--dry-run` 분기보다 **앞**에 있다.

    README가 "옵션 조합이 맞는지 LLM을 부르기 전에 확인하는 용도로는 쓸 수
    있다"고 적은 근거다. 검사를 dry-run 뒤로 옮기면 `--to fr --dry-run`이
    exit 0으로 조용히 통과하고, 사용자는 **본 실행에서야** exit 2를 만난다 -
    dry-run의 존재 이유가 정확히 그것을 앞당기는 것인데.
    """
    provider = EchoProvider()
    _patch_provider(monkeypatch, provider)

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "ten_cues.srt"),
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
            "--dry-run",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "적용할 수 있는 대상 언어가 없다" in result.output
    assert provider.calls == []


def _risk_free(source: str) -> str:
    """Tier 0 신호를 **하나도** 내지 않는 번역문을 만든다.

    선별 정책 자체를 재는 테스트는 위험도가 0.0이어야 한다 - 신호가 끼면
    hard fail이 예산을 우회해(FR-6.2) 정책이 무엇을 했는지 안 보인다.
    `EchoProvider`의 기본 transform(`f"EN:{s}"`)은 한글 원문을 남겨
    `struct.untranslated`가 전량 hard fail을 낸다(실측).

    **숫자를 그대로 옮긴다.** 원문의 숫자를 번역문에서 빠뜨리면
    `struct.number_missing`이 hard fail을 낸다. 다만 이 함수를 쓰는 두
    테스트의 픽스처인 `ten_cues.srt`에는 **숫자가 하나도 없어** 그 경로가
    지금은 발동하지 않는다 - 결과는 10큐 전부 `"Line  of the talk"`라는
    **한 문자열**이다(실측). 숫자를 담은 트랙으로 바꿔도 죽지 않게 하는
    보험으로 남긴다.

    **`length.ratio`가 잠잠한 이유는 MAD가 0이어서가 아니다.**
    `ten_cues.srt`의 원문 폭이 7과 8 두 가지뿐이고 번역문이 전부 같으므로
    비율이 2.4286과 2.125 둘로만 갈리고, 그러면 **모든 편차가 MAD와 정확히
    같아진다.** 그래서 z가 전 큐에서 `_MAD_SCALE`(0.6745)에 고정되고
    임계 `_RATIO_Z_THRESHOLD`(3.5)의 **약 1/5**에 그친다
    (실측: median 2.2768 · MAD 0.1518 · z 집합 {0.6745}).
    분포가 안 서는 것이 아니라 **서긴 서는데 전부 같은 자리에 선다.**

    **원문 길이가 두 가지를 넘으면 이 여유가 줄어든다.** 편차가 갈리기
    시작하면 z가 0.6745에서 흩어지고, 하나라도 3.5를 넘는 순간 이 함수의
    이름이 거짓이 되어 정책 테스트가 **조용히** 오염된다 - 신호가 붙어도
    프로그램은 정상 종료하므로 종료 코드로는 알 수 없다.
    """
    return f"Line {''.join(c for c in source if c.isdigit())} of the talk"


def test_번역_실패분은_트리아지에서_빠진다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """설계 §3.6·D12 — 라이브러리가 계약으로 요구한다.

    산수(세그먼트 10건 · 실패 3건 · 예산 10%):

    | 구현 | 대상 | quota | hard fail | 선별 | 실제 비율 |
    | --- | --- | --- | --- | --- | --- |
    | 올바름 | 7 | ceil(0.7)=1 | 0 | 1 | **14.3%** |
    | 틀림 | 10 | ceil(1.0)=1 | 3 | 3 | **30.0%** |

    틀린 구현에서는 `struct.empty`가 quota를 소진한다
    (`remaining = max(0, 1-3) = 0`). 실측으로는 실패 20건에서 Recall@10%가
    0%까지 떨어진다(`TranslationResult` 독스트링).
    """
    _patch_provider(monkeypatch, blank_at({2, 5, 9}, 10))

    result = runner.invoke(app, _args(tmp_path, "ten_cues.srt", "--review-budget", "10%"))

    # 번역 실패가 있으면 75다. FR-2.6은 "실패 표시 후 진행"만 요구하고
    # 종료 코드를 말한 적이 없다 - 근거를 잘못 대고 있던 주석이다.
    assert result.exit_code == EXIT_TRANSLATION_FAILURE, result.output
    assert "대상 세그먼트 7개 (번역 실패 3건 제외)" in result.output
    assert "실제 14.3%" in result.output
    # **실패분이 애초에 안 들어왔다의 직접 증거다.** 비율만 보면 세그먼트
    # 수가 우연히 맞는 데이터에서 통과할 수 있다.
    assert "struct.empty" not in result.output


def test_전량_실패면_건너뛴다고_말한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ "검수 대상 0개"와 "판정 자체를 못 했다"는 다르다.

    전량 실패에서 `review_ratio`는 빈 목록 가드로 0.0을 내므로, 그냥 요약을
    찍으면 `대상 세그먼트 0개 / 검수 대상 0개 (실제 0.0%)`가 나온다 - 검수할
    것이 없다는 뜻으로 읽히지만 실제로는 **아무것도 판정하지 못한 것**이고
    처방이 정반대다(재검수가 아니라 재실행이다).
    """
    _patch_provider(monkeypatch, blank_at(set(range(10)), 10))

    result = runner.invoke(app, _args(tmp_path, "ten_cues.srt", "--review-budget", "10%"))

    assert result.exit_code == EXIT_TRANSLATION_FAILURE, result.output
    assert "번역된 세그먼트가 없어 건너뛴다 (전량 10건 실패)" in result.output
    # 0.0%를 찍으면 "볼 것이 없다"로 오독된다 - 그 문구가 나오지 않는 것이 요점이다.
    assert "실제 0.0%" not in result.output


def test_요청_예산과_실제_비율이_어긋난다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """설계 D8·§9.3 — `ceil` 하나로 어긋난다. hard fail이 필요하지 않다.

    `large.srt`는 세그먼트 26개다. 예산 10% → `quota = ceil(2.6) = 3` →
    실제 `3/26 = 11.5%`. 위험도가 전부 0.0이어도 `select_by_budget`은 정렬 후
    상위 3건을 선별한다(동점은 세그먼트 ID 순, `policy.py:52`).

    **`EchoProvider`의 기본 transform(`f"EN:{s}"`)을 쓸 수 없다.** 원문
    한글이 그대로 남아 `struct.untranslated`가 **26건 전부 hard fail**을
    내고, hard fail은 예산을 우회하므로 실제 비율이 100%가 된다(실측).
    그러면 이 테스트가 재려는 `ceil` 효과가 hard fail에 완전히 가려진다.
    `large.srt`의 원문은 `안녕하세요 N번째 대사입니다`라 숫자를 담고 있어
    번역문에 그 숫자를 남기지 않으면 `struct.number_missing`이 두 자리
    구간(10~26번)에서 hard fail 17건을 낸다(실측). 그래서 transform이
    **숫자를 보존한다** - 이 조합에서만 신호 0건·hard fail 0건이 된다.
    """
    _patch_provider(
        monkeypatch,
        EchoProvider(
            transform=lambda s: f"Line {''.join(c for c in s if c.isdigit())} of the talk"
        ),
    )

    result = runner.invoke(app, _args(tmp_path, "large.srt", "--review-budget", "10%"))

    assert result.exit_code == 0, result.output
    assert "예산 10%" in result.output
    assert "실제 11.5%" in result.output
    # **전제를 함께 단언한다.** 새 Tier 0 신호가 이 번역문에 발화하기
    # 시작하면 위 비율 단언이 먼저 깨지는데, 그것만으로는 "ceil이 틀렸다"와
    # "hard fail이 끼어들었다"가 구별되지 않는다.
    assert "hard fail 0개" in result.output


def test_배치_신호가_발화한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """설계 §9.1 — 최우선 게이트.

    `spec.overlap`은 `BatchCollector`라 트랙 전체를 봐야 판정된다
    (`spec/check.py:100-127`이 정렬 후 누적 `run_end`와 비교한다).
    `collect_all`에 세그먼트를 하나씩 넘기면 **신호가 발화하지 않고
    프로그램은 정상 종료한다** - 조용한 실패다.

    `overlap.vtt`는 큐 2개가 3000~4000ms에서 겹친다.
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "overlap.vtt", "--review-budget", "50%"))

    assert result.exit_code == 0, result.output
    assert "spec.overlap" in result.output


def test_실패분_제외가_배치_신호를_눈멀게_하지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**수집과 융합의 입력이 다르다** - 리뷰 축A I1이 실측한 결함이다.

    위 `test_배치_신호가_발화한다`는 **번역 실패가 0건인** 트랙만 본다.
    `collect_all`에 `kept`(실패분을 뺀 목록)를 넘기는 구현은 그 게이트를
    통과하면서도 여기서 죽는다 - §9.1이 막으려던 실패 모드가 다른 문으로
    돌아오는 것이다.

    `overlap.vtt`의 두 큐는 1000~4000ms와 3000~5000ms다. `check_overlaps`가
    **뒤 큐**(`00001`)에 신호를 붙이므로, 앞 큐(`00000`)를 번역 실패시키면:

    | 수집 입력 | 앞 큐를 보는가 | `spec.overlap` |
    | --- | --- | --- |
    | `kept` (틀림) | 아니오 | **사라진다** |
    | `translated.segments` (옳음) | 예 | 뒤 큐에 남는다 |

    **겹침 자체는 산출 파일에 그대로 남아 출고된다.** 요약만 침묵하고 exit는
    0이라 종료 코드로는 알 수 없다.
    """
    _patch_provider(monkeypatch, blank_at({0}, 2))

    result = runner.invoke(app, _args(tmp_path, "overlap.vtt", "--review-budget", "50%"))

    # 번역 실패 1건. 75는 구현 선택이지 FR-2.6이 요구한 값이 아니다.
    assert result.exit_code == EXIT_TRANSLATION_FAILURE, result.output
    assert "대상 세그먼트 1개 (번역 실패 1건 제외)" in result.output
    # 실패분을 **융합**에서는 여전히 뺀다 - D12는 유지된다.
    assert "struct.empty" not in result.output
    # 그러나 **수집**에서는 빼지 않았으므로 겹침은 살아 있다.
    assert "spec.overlap" in result.output


def test_실패가_없으면_제외_괄호를_내지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "ten_cues.srt", "--review-budget", "10%"))

    assert result.exit_code == 0, result.output
    assert "대상 세그먼트 10개" in result.output
    # **괄호까지 포함해 단언한다.** `_format_translate_summary`가 바로 위에서
    # "성공 10개 · 실패 0개"를 내므로 `"번역 실패"`만으로는 우연에 기댄다.
    assert "(번역 실패" not in result.output


@pytest.mark.parametrize(
    ("threshold", "selected", "ratio"),
    [
        # 위험도가 전부 0.0이므로 `0.0 >= 0.0`은 참, `0.0 >= 0.7`은 거짓이다.
        ("0.0", "검수 대상 10개", "실제 100.0%"),
        ("0.7", "검수 대상 0개", "실제 0.0%"),
    ],
)
def test_임계값_방식이_동작한다(
    threshold: str, selected: str, ratio: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-6.3 ② — `select_by_threshold`가 **실제로 선별을 결정하는지** 본다.

    **임계값 하나로는 게이트가 되지 않는다.** 이전 판은 `EchoProvider` 기본값
    (전량 hard fail)에 `0.7` 하나만 걸고 `policy_label`과 세그먼트 수만
    단언했다 - 임계값이 아무것도 결정하지 않아 `select_by_threshold` 배선을
    **통째로 지워도 전 스위트가 통과했다**(실측: 1089 passed).

    두 값을 걸어 결과가 갈리는 것을 본다. 이러면 미배선도, 예산 방식으로
    바꿔치기하는 것도 죽는다 - 어느 쪽도 이 두 줄을 동시에 만족시키지 못한다.
    """
    _patch_provider(monkeypatch, EchoProvider(transform=_risk_free))

    result = runner.invoke(app, _args(tmp_path, "ten_cues.srt", "--review-threshold", threshold))

    assert result.exit_code == 0, result.output
    assert f"임계값 {threshold}" in result.output
    assert "대상 세그먼트 10개" in result.output
    assert selected in result.output
    assert ratio in result.output


def test_언어별로_트리아지가_돈다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "ten_cues.srt"),
            "--to",
            "en,ja",
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
    assert "[en] 트리아지" in result.output
    assert "[ja] 트리아지" in result.output
    # **프로파일 이름까지 본다.** 위 두 단언은 언어 라벨만 보므로
    # `profiles[target]`에 **다른 언어의** 프로파일이 들어가도 통과한다 -
    # 라벨은 `target_lang`에서 오고 프로파일은 별개의 dict 조회이기 때문이다.
    # 실측: `en`에만 ko 프로파일을 꽂는 변이가 전 스위트 1089건을 **통과했다.**
    # 값을 화면에 내는 것만으로는 게이트가 되지 않고, 이 두 줄이 그것을 닫는다.
    assert "[en] 트리아지 (예산 10%, 프로파일 en)" in result.output
    assert "[ja] 트리아지 (예산 10%, 프로파일 ja)" in result.output


def _risk(seg_id: str, *, selected: bool, reasons: list[str] | None = None) -> SegmentRisk:
    """요약 포맷터에 먹일 최소 위험도. 신호 구현을 타지 않는다."""
    return SegmentRisk(
        segment_id=seg_id,
        signals=[],
        risk_score=0.0,
        hard_fail=False,
        selected=selected,
        reasons=reasons or [],
    )


def _outcome(risks: list[SegmentRisk], *, policy_label: str, excluded: int = 0) -> TriageOutcome:
    """`_format_triage_summary`가 받는 객체를 만든다.

    포매터는 `risks`·`policy_label`·`profile_name`·`excluded_failures`만 읽으므로
    나머지 필드는 이 테스트의 판정에 관여하지 않는다.
    """
    return TriageOutcome(
        source_lang="ko",
        target_lang="en",
        profile_name="en",
        policy_label=policy_label,
        policy_kind="budget",
        policy_value=0.1,
        risks=tuple(risks),
        # **`segments=()`를 쓸 수 없다.** 포매터는 이 필드를 읽지 않지만
        # `__post_init__`이 `risks`와 같은 세그먼트 집합을 요구하므로
        # (`report/models.py`) 빈 튜플은 `ValueError`로 거부된다.
        # 대응하는 더미를 만든다 - 값은 판정에 관여하지 않는다.
        segments=tuple(
            Segment(id=r.segment_id, index=i, start_ms=0, end_ms=1000, source_text="원문")
            for i, r in enumerate(risks)
        ),
        excluded_failures=excluded,
        usage=None,
    )


def test_검수_대상이_있으면_0퍼센트로_보이지_않는다() -> None:
    """`_format_ratio` 재사용을 잠근다 (리뷰 축B 변이 C).

    이 저장소는 "0이 아닌데 `0.0%`로 보이는 것"을 **1급 결함**으로 취급한다
    (`_format_ratio` 독스트링 - "검수자가 위반 목록을 눈앞에 두고 요약만 보면
    '0%니까 통과'로 읽는다"). 그래서 트리아지 요약도 `f"{x:.1%}"`가 아니라
    `_format_ratio`를 써야 하는데, **바꿔치기해도 전 스위트가 통과했다**
    (실측: 1089 passed) - CLI 경로의 비율이 전부 0.1% 이상이라 두 구현의
    차이가 드러나는 구간을 아무도 밟지 않았기 때문이다.

    2001건 중 1건이 정확히 그 구간이다: `1/2001*100 = 0.0499...`로
    `_format_ratio`의 절단 경계(`< 0.05`) 바로 아래다. `f"{x:.1%}"`는 이것을
    `0.0%`로 떨어뜨린다.

    **순수 함수를 직접 부른다** - 2001큐짜리 픽스처를 만들지 않기 위해서다.
    포맷터가 `_translate_one`에서 분리돼 있는 것이 이 테스트를 가능하게 한다.
    """
    risks = [_risk(f"{i:05d}", selected=(i == 0)) for i in range(2001)]

    lines = _format_triage_summary(_outcome(risks, policy_label="예산 0.1%"))

    assert "  검수 대상 1개 (실제 <0.1%)" in lines
    # 이 단언이 실질이다 - 위 줄만 보면 문자열을 손으로 만든 구현도 통과한다.
    assert not any("0.0%" in line for line in lines), lines


def test_신호별_적발은_선별되지_않은_것도_센다() -> None:
    """집계는 `risks` **전체**를 본다 (리뷰 축B 변이 D).

    선별분으로 좁혀도 전 스위트가 통과했다(실측). 그러나 §9.2의 실패분 제외
    게이트가 `"struct.empty" not in output`으로 판정하므로, 집계가 선별분만
    보면 **실패분이 입력에 섞여 있어도 선별되지 않았다는 이유로 조용히 통과**할
    수 있다 - 게이트가 재려던 것을 못 재게 된다.

    "신호별 적발"은 "무엇이 걸렸나"이지 "무엇이 뽑혔나"가 아니다. 예산 밖으로
    밀린 위험도 사용자가 알아야 다음 예산을 정한다.
    """
    risks = [
        _risk("00000", selected=True, reasons=["spec.violation"]),
        _risk("00001", selected=False, reasons=["struct.empty"]),
    ]

    lines = _format_triage_summary(_outcome(risks, policy_label="예산 50%"))

    assert "    spec.violation 1개" in lines
    assert "    struct.empty 1개" in lines, "선별되지 않은 세그먼트의 신호가 집계에서 빠졌다"


def test_트리아지가_던지면_exit_2다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """정책 오류가 **exit 1로 새지 않는다** (리뷰 축A M2).

    이 CLI에서 1은 "규격 위반 발견"이다(`cli.py` 머리말). 트리아지가 던진
    `ValueError`를 잡지 않으면 미처리 traceback이 exit 1이 되어 **설정 실수가
    자막 결함으로 오보되고** 사용자는 멀쩡한 자막을 고치려 든다 -
    `--review-threshold nan` 가드가 앞단에 있는 이유와 같다.

    번역 파일은 이미 나갔다는 것도 함께 고정한다. 트리아지만 못 돈 것이지
    번역이 실패한 것이 아니다.
    """

    def boom(*_args: object, **_kwargs: object) -> list[SegmentRisk]:
        raise ValueError("정책이 터졌다")

    monkeypatch.setattr("cuesift.cli.select_by_budget", boom)
    _patch_provider(monkeypatch, EchoProvider(transform=_risk_free))

    result = runner.invoke(app, _args(tmp_path, "ten_cues.srt", "--review-budget", "10%"))

    assert result.exit_code == 2, result.output
    assert "트리아지를 돌리지 못했다: 정책이 터졌다" in result.output
    assert (tmp_path / "ten_cues.en.srt").exists(), "번역까지 잃었다"


class _무음백엔드:
    """번역은 정상이지만 **usage를 안 내는** 백엔드 (요구사항정의서 §12 Q3).

    `_extract_usage`가 `usage` 키가 없거나 형식이 다른 응답을 전부 `(0, 0)`으로
    떨어뜨리므로 성공 호출은 세어지는데 토큰은 0으로 남는다. 로컬 Ollama 계열이
    실제로 이 모양이다.
    """

    name = "silent"

    def __init__(self) -> None:
        self._inner = EchoProvider()

    def complete(self, messages, *, temperature, max_tokens):  # type: ignore[no-untyped-def]
        completion = self._inner.complete(messages, temperature=temperature, max_tokens=max_tokens)
        return Completion(text=completion.text, usage=TokenUsage(0, 0, calls=1))

    def close(self) -> None:
        return None


def test_무음_계층은_트리아지_요약에도_실린다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**번역 요약의 경고만으로는 이 사람을 구하지 못한다.**

    그쪽은 `result.usage`, 즉 번역 계층만 본다. "번역은 상용 API·Tier 1만 로컬"인
    구성에서는 번역 줄이 정상으로 보이고, `review.json`은 `--review-out`이 있어야
    생기므로 기본 경로 사용자는 무음을 어디서도 못 본다(§12 Q3 · NFR-2).

    **실제 CLI를 돌려 잰다** - 포매터 단위 호출만으로는 `_run_triage`가
    `cost_unreported`를 실제로 채우는지 확인되지 않는다.
    """
    _patch_provider(monkeypatch, _무음백엔드())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt", "--review-budget", "50%"))

    assert result.exit_code == 0, result.output
    트리아지 = [line for line in result.output.splitlines() if "토큰 수치를 못 받은 계층" in line]
    assert len(트리아지) == 1, result.output
    assert "translation" in 트리아지[0]
    # 두 요약이 같은 말을 해야 한다 - 다르면 사용자가 다른 문제로 읽는다.
    assert result.output.count("백엔드가 토큰 수치를 내지 않았다") == 2


def test_토큰을_낸_백엔드는_트리아지_요약에_그_줄이_없다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """언제나 붙는 경고는 읽히지 않는다 - 무시되는 경고는 없는 경고와 같다."""
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt", "--review-budget", "50%"))

    assert result.exit_code == 0, result.output
    assert "토큰 수치를 못 받은 계층" not in result.output
    assert "백엔드가 토큰 수치를 내지 않았다" not in result.output


def test_트리아지_요약의_무음_줄은_계층을_나열한다() -> None:
    """Tier 1이 배선되면 계층이 둘이 된다 - 이름을 안 내면 어느 쪽인지 알 수 없다."""
    outcome = _outcome([_risk("00000", selected=True)], policy_label="예산 10%")
    무음 = replace(outcome, cost_includes=("translation", "tier1"), cost_unreported=("tier1",))

    줄 = [line for line in _format_triage_summary(무음) if "토큰 수치를 못 받은 계층" in line]

    assert len(줄) == 1
    assert "tier1" in 줄[0]
    assert "translation" not in 줄[0], "무음이 아닌 계층까지 나열하면 범인이 흐려진다"


# 개수 축 CLI (FR-6.3 ① · 설계 D1·D2·D6).


def test_top_k와_예산을_함께_주면_거부된다(tmp_path: Path) -> None:
    # D1·D4 - FR-6.3은 "두 방식으로 지정할 수 있다"이지 "동시에"가 아니다.
    result = runner.invoke(
        app, _args(tmp_path, "minimal.srt", "--review-budget", "10%", "--review-top-k", "5")
    )
    assert result.exit_code == 2, result.output


def test_top_k와_임계값을_함께_주면_거부된다(tmp_path: Path) -> None:
    result = runner.invoke(
        app, _args(tmp_path, "minimal.srt", "--review-threshold", "0.5", "--review-top-k", "5")
    )
    assert result.exit_code == 2, result.output


def test_top_k와_tier1은_함께_쓸_수_없다(tmp_path: Path) -> None:
    """D2 - `triage_with_tier1`이 `budget_ratio: float`를 필수로 받는다.

    **메시지가 대안을 말해야 한다.** `--review-threshold`의 같은 거부가
    이미 `(--review-budget을 쓴다)`를 달고 있고, 그것이 없으면 사용자는
    Tier 1을 포기해야 하는 줄 안다.
    """
    result = runner.invoke(app, _args(tmp_path, "minimal.srt", "--review-top-k", "5", "--tier1"))

    assert result.exit_code == 2, result.output
    assert normalize_rich_message("--review-budget") in normalize_rich_message(result.output)


def test_음수_top_k는_거부된다(tmp_path: Path) -> None:
    # `min=0`을 typer에 주므로 click이 막고, 메시지가 옵션 이름을 말한다.
    result = runner.invoke(app, _args(tmp_path, "minimal.srt", "--review-top-k", "-1"))

    assert result.exit_code == 2, result.output


def test_top_k가_정확히_k개를_고른다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """옵션을 받고도 배선이 없으면 조용히 무시된다 - 이 단언이 그것을 막는다.

    `_risk_free`가 신호를 하나도 내지 않으므로 hard fail이 0이고,
    따라서 선별 개수가 정확히 K다(D6의 잔여분이 발동하지 않는 조건).
    """
    _patch_provider(monkeypatch, EchoProvider(transform=_risk_free))

    result = runner.invoke(app, _args(tmp_path, "ten_cues.srt", "--review-top-k", "3"))

    assert result.exit_code == 0, result.output
    assert "검수 대상 3개" in result.output


def test_top_k_라벨이_화면에_나온다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 사용자가 준 값을 그대로 되돌려 준다. 파싱 결과를 찍으면 자기 입력을
    # 화면에서 못 찾는다.
    _patch_provider(monkeypatch, EchoProvider(transform=_risk_free))

    result = runner.invoke(app, _args(tmp_path, "ten_cues.srt", "--review-top-k", "2"))

    assert "상위 2개" in result.output


def test_k가_세그먼트_수보다_커도_동작한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # D5 - 오류로 만들면 세그먼트 수를 미리 아는 사람만 이 옵션을 쓸 수 있다.
    _patch_provider(monkeypatch, EchoProvider(transform=_risk_free))

    result = runner.invoke(app, _args(tmp_path, "ten_cues.srt", "--review-top-k", "100"))

    assert result.exit_code == 0, result.output
    assert "검수 대상 10개" in result.output


def test_hard_fail이_top_k를_넘으면_선별도_k를_넘는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D6 - hard fail은 검수 예산을 우회한다(FR-6.2).

    **`cli.py`가 이미 `검수 대상 N개 (실제 x%)`를 출력한다**(착수 조사 P2).
    새로 만들 표시가 아니라 고정할 그물이다 - 누군가 K로 자르는 "개선"을
    넣으면 이 단언이 죽는다.

    `EchoProvider`의 **기본** transform(`f"EN:{s}"`)은 한글 원문을 남겨
    `struct.untranslated`가 10큐 전부 hard fail을 낸다(실측, `_risk_free`
    독스트링). 그래서 `--review-top-k 1`인데 선별이 10개다.

    **종료 코드를 단언하지 않는다.** 이 테스트가 재려는 것은 선별 개수가
    K로 잘리지 않는다는 사실 하나이고, hard fail이 `translate`의 종료
    코드를 바꾸는지는 다른 테스트의 몫이다.
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "ten_cues.srt", "--review-top-k", "1"))

    assert "검수 대상 10개" in result.output
    assert "실제 100.0%" in result.output
