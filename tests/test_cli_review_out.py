"""`--review-out` CLI 표면 (FR-7.2 · 설계 §5).

이 파일이 고정하는 것은 넷이다 - 경로 규칙(`_review_path`) · 옵션 선언 ·
조합 검증 · **파일 쓰기 배선**(Task 6이 더했다).

**앞의 성공 경로 테스트들은 파일의 부재를 단언하지 않는다** - 배선이 붙기
전에 그렇게 했다면 Task 6이 반드시 지워야 하는 테스트가 됐을 것이고, 지워야
하는 게이트는 게이트가 아니다. **단언하는 유일한 예외**가
`test_예산만_주면_파일을_쓰지_않는다`인데, 그쪽은 `--review-out`이 아예
없어서 배선 이후에도 참이다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.fakes.provider import EchoProvider
from typer.testing import CliRunner

from conftest import blank_at, normalize_rich_message, scripted_at
from cuesift.cli import EXIT_TRANSLATION_FAILURE, _output_path, _review_path, app
from cuesift.report import json_report

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


@pytest.mark.parametrize(
    ("stem", "source_lang"),
    [
        # 평범한 경우.
        ("ep01.ko", "ko"),
        # **파일명**이 대문자 - `stem.casefold()`가 잠근다. Windows는 파일명
        # 대소문자를 구분하지 않아 `ep01.KO.srt`가 정상인 파일명이다.
        ("ep01.KO", "ko"),
        # **인자**가 대문자 - `suffix.casefold()`가 잠근다. 이 한 줄이
        # `_output_path`의 접기 구멍을 닫는다(아래 독스트링 참고).
        ("ep01.ko", "KO"),
        # 태그가 없어 덧붙이는 경우. 치환 분기를 **무조건 타는** 변이를 잡는다.
        ("ep01", "ko"),
    ],
)
def test_stem_규칙이_자막_출력과_같다(stem: str, source_lang: str) -> None:
    """설계 D2 - 고정 이름은 입력 파일 여럿을 같은 디렉터리로 낼 때 서로를 지운다.

    **이 테스트는 이름과 달리 `_output_path`를 부르지 않고 있었다.** 그래서
    "자막 출력과 같다"는 이름이 주장만 하고 아무것도 재지 않았고,
    `_output_path`의 `suffix.casefold()`만 제거하는 변이가 **전 스위트를
    통과했다**(실측: 브랜치 전체 리뷰 M2 생존). `_review_path`의 같은 변이는
    `test_대문자_source_lang_인자도_치환된다`가 단독으로 격추한다 - **자물쇠가
    한쪽에만 달려 있었다.**

    구멍은 **파일명**이 아니라 **인자**가 대문자인 방향이다. 기존 테스트는
    파일명 대문자만 봤다:

    | 입력 | 현재 | `_output_path` suffix fold 제거 시 |
    | --- | --- | --- |
    | `ep01.ko.srt --source-lang KO` | `ep01.en.srt` | **`ep01.ko.en.srt`** |
    | 같은 입력의 리포트 | `ep01.en.review.json` | `ep01.en.review.json` (안 바뀐다) |

    `_review_path`의 독스트링이 **"두 규칙이 갈라지면 같은 입력이
    `ep01.en.srt`와 `ep01.ko.en.review.json`을 내 짝을 눈으로 못 맞춘다"**고
    불변식을 선언한 바로 그 갈라짐이다. 아래 마지막 단언이 그것을 직접 잰다 -
    두 함수의 출력을 **서로** 비교하므로 어느 한쪽만 바뀌어도 죽는다.

    디렉터리를 `subs`/`reports`로 **다르게** 둔다 - 같게 두면 경로 결정이
    통째로 틀려도 이름 비교가 우연히 성립할 수 있다. 나머지 값(`a` · `ko` ·
    `en`)도 서로 다르다: 겹치면 그 축의 바꿔치기 변이가 살아남는다(출력
    이름에 `target_lang` 대신 `source_lang`을 쓰는 변이는 둘이 같으면 안
    잡힌다).
    """
    src = Path(f"a/{stem}.srt")

    subtitle = _output_path(src, Path("subs"), source_lang, "en", suffix=src.suffix)
    review = _review_path(src, Path("reports"), source_lang, "en")

    assert subtitle == Path("subs/ep01.en.srt")
    assert review == Path("reports/ep01.en.review.json")
    # **두 규칙이 갈라지는 순간을 직접 잡는다.** 위 두 단언은 각각 자기
    # 함수만 보므로, 둘이 함께 틀리는 미래의 변경은 통과시킨다.
    assert review.name == f"{subtitle.stem}.review.json", (
        f"자막({subtitle.name})과 리포트({review.name})의 stem 규칙이 갈라졌다"
    )


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


def test_두번째_값_조합도_같은_경로_규칙을_따른다() -> None:
    """**네 인자를 동시에 다른 값으로 바꾼다 - 상수 고정 변이를 잡는 유일한 자리다.**

    위 네 테스트는 축마다 값을 하나씩만 쓴다(`ep01` · `reports` · `ko`/`KO` ·
    `en`). 그래서 인자를 **리터럴로 굳히는** 변이가 전부 살아남는다 - 실측으로
    네 종이 전체 스위트를 통과했다:

    | 굳힌 것 | 굳혀도 통과하던 이유 |
    | --- | --- |
    | `target_lang` -> `"en"` | 모든 테스트가 `en`만 쓴다 |
    | `suffix` -> `".ko"` | 모든 테스트가 source `ko`만 쓴다 |
    | `stem` -> `"ep01"` | 모든 테스트가 `ep01`만 쓴다 |
    | `review_dir` -> `Path("reports")` | 모든 테스트가 `reports`만 쓴다 |

    **`stem` 고정이 가장 아프다.** 굳히면 어떤 입력을 줘도
    `ep01.en.review.json` 하나만 내는 코드가 되는데, 그것은 이 함수의
    독스트링이 막겠다고 선언한 사고 그 자체다 - "`ep01`과 `ep02`를 같은
    `--review-out`으로 돌리면 뒤엣것이 앞엣것을 조용히 지운다". **막겠다고
    적은 사고를 재현하는 구현이 게이트를 통과하고 있었다.**

    축을 하나만 바꾸면 그 축의 변이 하나만 죽는다. 넷을 동시에 바꿔야 한
    줄로 넷을 다 죽인다 - 그래서 stem·디렉터리·source·target을 전부 갈았다
    (`ep02` · `out` · `ja` · `ko`).

    **source(`ja`)와 target(`ko`)을 서로 다르게 둔 것도 필수다.** 같게 두면
    출력 이름에 `target_lang` 대신 `source_lang`을 쓰는 바꿔치기 변이가
    이 테스트에서도 살아남는다.
    """
    got = _review_path(Path("b/ep02.ja.srt"), Path("out"), "ja", "ko")

    assert got == Path("out/ep02.ko.review.json")


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


def test_정상_조합은_dry_run에서도_받아들인다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """위 테스트의 **양성 절반**이다. D11은 "실행 전에 알린다"이지 "dry-run을 막는다"가 아니다.

    바로 위가 실패 방향 하나뿐이라, **dry-run이면 정상 조합까지 거부하는**
    변이가 전체 스위트를 통과한다(실측). 그 변이는 `--dry-run`을 조합 확인
    수단으로 쓰는 사용법을 통째로 막는데, 실패 케이스만 보는 테스트는
    "조합이 틀렸을 때 거부한다"와 "dry-run이면 무조건 거부한다"를 구별하지
    못한다.

    `test_예산과_함께_주면_받아들인다`가 같은 조합을 dry-run **없이** 보므로,
    둘이 짝일 때만 "dry-run 여부가 판정을 바꾸지 않는다"가 고정된다.
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(tmp_path / "rp"),
            "--dry-run",
        ),
    )

    assert result.exit_code == 0, result.output


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


def _read_review(tmp_path: Path, name: str = "minimal.en.review.json") -> dict:
    return json.loads((tmp_path / "reports" / name).read_text(encoding="utf-8"))


# 한글이 그대로 남고 **줄이 셋**인 번역문. 두 신호를 동시에 낸다 -
# `struct.untranslated`(hard fail)와 `spec.violation`(줄 수 초과).
# 한 줄짜리 한글은 `spec.violation`을 내지 않으므로 이 둘을 가르는 것이
# **여러 줄이라는 성질**이다(실측: 아래 두 테스트의 `signal_hits` 비교).
_KO_MULTILINE = "첫째 줄입니다\n둘째 줄입니다\n셋째 줄입니다"


def test_화면_요약과_파일_수치가_일치한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**이 설계에서 가장 조용한 실패다** (D8 · 게이트 10.1).

    화면과 파일이 갈라져도 프로그램은 정상 종료하고 파일도 정상이며 종료 코드도
    바뀌지 않는다. 두 값을 **한 실행 안에서** 대조하는 것만이 이것을 잡고, 화면과
    파일이 함께 존재하는 자리는 여기뿐이다.

    **무엇을 잡고 무엇을 못 잡는지 정확히 적는다** (Task 6 실측 · 계획서 정정본).

    - **잡는다** — 화면이 **다른 프로퍼티**를 찍는 형태
      (`total_segments`·`selected`·`hard fail`을 서로 뒤바꾸기). 값이 실제로
      달라지므로 대조가 어긋난다.
    - **못 잡는다** — `total = len(outcome.risks)`. `triaged_segments`의 정의가
      **글자 그대로 `return len(self.risks)`**다(`report/models.py:85`).
    - **못 잡는다** — `selected = len([r for r in outcome.risks if r.selected])`.
      `selected_for_review`가 같은 식이다(`models.py:100`·`:104`).

    뒤의 둘은 Task 4의 Step 7b(grep 게이트)도 통과한다. **두 게이트 모두
    못 잡는 것이 맞다** - 오늘의 동작이 동일하기 때문이다. Step 7b가 지키는 것은
    "프로퍼티 정의가 나중에 바뀌면 굳어 있는 사본이 조용히 갈라진다"는 **미래의**
    위험이고(`models.py:91-93`이 "두 값이 지금 같다는 것은 우연이지 보장이
    아니다"라고 적어 두었다), 이 테스트가 지키는 것은 **오늘의** 갈라짐이다.
    둘은 보완 관계이지 대체 관계가 아니다 - **이 테스트를 근거로 Step 7b를
    지우면 안 된다.**

    **픽스처의 다섯 수치가 전부 다르다**(실측: total 10 · triaged 8 ·
    excluded 2 · selected 3 · hard fail 1). 이것이 이 게이트의 핵심이다 -
    브리프 ④의 "값이 서로 같다" 형태에 걸리면 두 값을 뒤바꾸는 변이가 **같은
    값을 내며 통과한다.** 실제로 초판은 hard fail이 0이라 `hard = 0` 변이가
    전 스위트를 빠져나갔다(리뷰 축B I2).

    수치가 이렇게 나오는 근거:

    - 빈칸 2건(`{2, 5}`)이 `empty_translation`으로 실패 -> excluded 2 · triaged 8
    - 한글 잔류 1건(`{0}`)이 `struct.untranslated`로 hard fail -> hard 1
    - 예산 30%에서 `quota = ceil(8 * 0.3) = 3`이고 hard 1이 quota를 소진하므로
      `selected = max(3, 1) = 3` (`policy.py:88-92`)

    실패 2건이 있으므로 종료 코드는 **1**이다(FR-2.6). 리포트는 그보다 먼저 나간다.
    """
    _patch_provider(monkeypatch, scripted_at({0: _KO_MULTILINE, 2: "   ", 5: "   "}, 10))

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "ten_cues.srt",
            "--review-budget",
            "30%",
            "--review-out",
            str(tmp_path / "reports"),
        ),
    )

    assert result.exit_code == EXIT_TRANSLATION_FAILURE, result.output
    summary = _read_review(tmp_path, "ten_cues.en.review.json")["summary"]

    # **반-퇴화 가드가 먼저다.** 아래 세 대조는 값이 서로 다를 때만 무언가를
    # 잰다 - 픽스처가 흘러가 값이 겹치면 대조가 `N == N`이 되어 조용히
    # 무력해진다. 그 상태를 게이트가 스스로 잡게 한다.
    counted = [
        summary["total_segments"],
        summary["triaged_segments"],
        summary["excluded_failures"],
        summary["selected_for_review"],
        summary["hard_fail_count"],
    ]
    assert len(set(counted)) == len(counted), (
        f"다섯 수치 중 겹치는 것이 있으면 뒤바꾸기 변이가 통과한다: {counted}"
    )
    assert summary["hard_fail_count"] > 0, "hard fail이 0이면 그 대조가 0 == 0이다"

    assert summary["triaged_segments"] == 8
    assert f"  대상 세그먼트 {summary['triaged_segments']}개" in result.output
    assert f"  검수 대상 {summary['selected_for_review']}개" in result.output
    assert f"  hard fail {summary['hard_fail_count']}개" in result.output


def test_신호별_적발_집계가_화면과_일치한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 10.1의 나머지 절반 — `signal_hits`는 **개수와 순서**를 함께 본다.

    **순서가 계약인 이유.** `signal_hits`는 `dict(sorted(...))`로 정렬해
    반환하고(`report/models.py:126`) 그 근거가 NFR-3 재현성이다 - `Counter`의
    순서는 삽입 순이라 세그먼트 순서가 바뀌면 화면과 파일이 달라진다.
    `cli.py`의 `_format_triage_summary`도 "정렬을 여기서 다시 하지 않는다"고
    적어 두 곳이 같은 출처를 쓴다. **그런데 그 계약을 어디서도 단언하지
    않았다** - 화면 신호 순서를 뒤집는 변이가 전 스위트를 빠져나갔다
    (리뷰 축B M1).

    **신호가 2종 이상이어야 순서가 존재한다.** 이전 픽스처(`EchoProvider()`)는
    `struct.untranslated` 1종뿐이라 루프가 정확히 한 번 돌았고, 그때 순서
    단언은 항상 참이다. 그래서 한글을 **여러 줄로** 남기는 번역문 하나와
    **한 줄로** 남기는 번역문 하나를 심는다:

    | 인덱스 | 번역문 | 나오는 신호 |
    | --- | --- | --- |
    | 0 | 한글 3줄 | `struct.untranslated` · `spec.violation` · `length.ratio` |
    | 1 | 한글 1줄 | `struct.untranslated` · `length.ratio` |
    | 나머지 | `EN{i}` | 없음 |

    실측 결과가 `{'length.ratio': 2, 'spec.violation': 1, 'struct.untranslated': 2}`다 -
    **3종이고 개수가 전부 같지도 않다.** 개수까지 모두 같으면 이름과 개수를
    뒤섞는 변이가 통과한다(브리프 ④ "값이 서로 같다").

    번역 실패가 없으므로 종료 코드는 **0**이다.
    """
    _patch_provider(monkeypatch, scripted_at({0: _KO_MULTILINE, 1: "둘째 줄입니다"}, 10))

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "ten_cues.srt",
            "--review-budget",
            "30%",
            "--review-out",
            str(tmp_path / "reports"),
        ),
    )

    assert result.exit_code == 0, result.output
    summary = _read_review(tmp_path, "ten_cues.en.review.json")["summary"]
    hits = summary["signal_hits"]

    # 반-퇴화 가드 둘. 아래 순서 대조는 신호가 2종 이상일 때만 무언가를 재고,
    # 개수 대조는 개수가 전부 같지 않을 때만 무언가를 잰다.
    assert len(hits) >= 2, f"신호가 1종이면 순서 대조가 항상 참이다: {hits}"
    assert len(set(hits.values())) > 1, f"개수가 전부 같으면 뒤섞기 변이가 통과한다: {hits}"

    # **순서까지 대조한다 - `in`으로 하나씩 찾으면 순서가 안 잡힌다.**
    # 화면의 신호 줄은 머리글 바로 아래에 연속으로 온다.
    lines = [line.rstrip() for line in result.output.splitlines()]
    head = lines.index("  신호별 적발")
    expected = [f"    {name} {count}개" for name, count in hits.items()]
    assert lines[head + 1 : head + 1 + len(expected)] == expected


def test_total이_triaged와_excluded의_합이다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 10.2 — 분모가 조용히 바뀌면 README 배수가 무너진다.

    **`excluded`가 0이면 `total == triaged + 0`이 항등식이 되어 아무것도 재지
    못한다** (사전 스캔 발견 B). `blank_at`으로 실패 3건을 만들어야 검산이
    성립한다.

    **실제 게이트는 하드코딩한 두 값(`== 3`·`== 10`)이다.** 셋째 단언은
    `total_segments`가 `return self.triaged_segments + self.excluded_failures`
    (`report/models.py:95`)라 모델 수준에서 자동으로 참이다 - 그 줄이 CLI
    경로에서도 유지되는지를 보는 회귀 확인이지 독립 게이트가 아니다
    (리뷰 축B M4).
    """
    _patch_provider(monkeypatch, blank_at({2, 5, 9}, 10))

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "ten_cues.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(tmp_path / "reports"),
        ),
    )

    assert result.exit_code == EXIT_TRANSLATION_FAILURE, result.output
    summary = _read_review(tmp_path, "ten_cues.en.review.json")["summary"]
    assert summary["excluded_failures"] == 3, "실패가 0이면 아래 검산이 항등식이 된다"
    assert summary["total_segments"] == 10
    assert summary["total_segments"] == summary["triaged_segments"] + summary["excluded_failures"]


def test_입력이_둘이면_파일이_서로를_지우지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 10.3 — 덮어쓰기는 종료 코드가 0이고 경고도 없다."""
    _patch_provider(monkeypatch, EchoProvider())
    reports = tmp_path / "reports"

    for fixture in ("minimal.srt", "ten_cues.srt"):
        result = runner.invoke(
            app, _args(tmp_path, fixture, "--review-budget", "50%", "--review-out", str(reports))
        )
        assert result.exit_code == 0, result.output

    assert sorted(p.name for p in reports.glob("*.review.json")) == [
        "minimal.en.review.json",
        "ten_cues.en.review.json",
    ]


def test_dry_run은_파일을_쓰지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D11 — dry-run은 트리아지를 돌리지 않으므로 낼 것이 없다.

    **이 테스트는 구조적으로 고립된 증거를 가질 수 없다** (리뷰 축B M3 실측).
    dry-run 경로를 깨는 변이(조기 `return` 제거)를 넣으면 이 테스트가 죽지만
    `test_cli_translate.py`·`test_cli_triage.py`의 dry-run 테스트 4개가 함께
    죽는다 - 살해자가 5개라 "리포트가 안 나온다"만 재는 단독 증거가 되지
    않는다.

    **화면 부재를 단언하지 않는다 - 이제 dry-run은 리포트 경로를 말한다**
    (브랜치 리뷰 코드 축 m1이 침묵을 결함으로 판정했다). 이전 판의 이 자리에는
    "`assert "리포트" not in result.output`을 붙여도 살해자가 늘지 않는다"고
    적혀 있었는데, 그 근거였던 **"`리포트` 줄은 `write_review` 성공 뒤에만
    나온다"가 더 이상 사실이 아니다.** 지금 그 단언을 붙이면 정상 동작을
    깨뜨린다. **이 테스트가 재는 것은 오직 파일의 부재다** - 예고와 산출은
    다르고, 아래 `test_dry_run이_리포트_경로를_예고한다`가 예고 쪽을 잰다.

    그래도 지운 게이트는 아니다 - "dry-run이 리포트를 **쓰지** 않는다"를
    이름으로 선언하는 자리가 여기뿐이고, 그 계약이 깨지면 살해자 명단에
    반드시 들어온다.
    """
    _patch_provider(monkeypatch, EchoProvider())
    reports = tmp_path / "reports"

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(reports),
            "--dry-run",
        ),
    )

    assert result.exit_code == 0, result.output
    assert not reports.exists() or list(reports.glob("*.review.json")) == []


def test_dry_run이_리포트_경로를_예고하고_그_경로가_본_실행과_같다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**dry-run이 `--review-out` 산출물을 한 줄도 말하지 않았다** (리뷰 코드 축 m1).

    자막 경로는 언어별로 찍으면서 리포트는 침묵했다. D11 주석이 "dry-run으로
    확인한 명령이 본 실행에서 처음 실패한다"를 막겠다고 선언했으므로 의도는
    dry-run/본실행 정합인데, **산출물 목록만 그 정합에서 빠져 있었다.**

    **하드코딩한 문자열과 비교하지 않는다.** 그러면 두 경로 규칙이 함께
    틀려도 통과한다. **실제 실행이 낸 파일의 경로**가 dry-run 화면에 있었는지를
    묻는 것이 이 테스트의 요점이다 - 그래서 `_review_path`를 dry-run 쪽에서만
    바꾸는 변이가 여기서 죽는다.

    **입력 파일 이름에 `.ko` 태그가 있어야 한다.** `minimal.srt`처럼 태그가
    없으면 `_review_path`(치환)와 손조립(`input.stem`을 그대로 씀)이 **같은
    답을 내서** 손조립 변이가 살아남는다(실측: 그 변이가 전 스위트를
    통과했다). 태그가 있으면 갈린다 - 정답 `ep01.en.review.json` ↔ 손조립
    `ep01.ko.en.review.json`. 그래서 픽스처를 `tmp_path`로 복사해 이름을 준다.

    `normalize_rich_message`를 양쪽에 통과시킨다 - `rich`가 긴 경로를 어디서
    접든 같은 결과를 내야 한다(그 함수의 독스트링).
    """
    _patch_provider(monkeypatch, EchoProvider())
    reports = tmp_path / "reports"
    source = tmp_path / "ep01.ko.srt"
    source.write_bytes((_FIXTURES / "minimal.srt").read_bytes())
    args = [
        "translate",
        str(source),
        "--to",
        "en",
        "--out",
        str(tmp_path / "subs"),
        "--base-url",
        "http://h/v1",
        "--model",
        "m1",
        "--no-cache",
        "--review-budget",
        "10%",
        "--review-out",
        str(reports),
    ]

    dry = runner.invoke(app, [*args, "--dry-run"])

    assert dry.exit_code == 0, dry.output
    dry_norm = normalize_rich_message(dry.output)
    # **`"리포트"`를 세지 않는다.** `tmp_path`가 테스트 이름에서 파생되므로
    # 이름에 그 낱말이 들어 있으면 경로마다 세어져 개수가 부풀었다(실측: 1이
    # 아니라 4). `.review.json`은 예고한 경로에만 나온다.
    assert dry_norm.count(".review.json") == 1, f"리포트 예고가 1건이 아니다\n{dry.output}"
    # dry-run은 예고만 한다 - 파일도 디렉터리도 만들지 않는다(README 조합 표).
    assert not reports.exists(), "dry-run이 디렉터리를 만들었다"

    real = runner.invoke(app, args)

    assert real.exit_code == 0, real.output
    written = sorted(reports.glob("*.review.json"))
    assert len(written) == 1, written
    # 태그 치환이 실제로 일어났는지도 함께 못 박는다 - 이것이 없으면 두
    # 규칙이 **함께** 손조립으로 바뀌는 변경이 통과한다.
    assert written[0].name == "ep01.en.review.json", written[0]
    assert normalize_rich_message(str(written[0])) in dry_norm, (
        f"예고한 경로와 실제 산출물이 다르다\ndry-run:\n{dry.output}\n실제: {written[0]}"
    )


def test_dry_run은_프로파일_없는_언어의_리포트를_예고하지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D7 — 프로파일이 없으면 본 실행도 리포트를 내지 않는다.

    **예고가 산출보다 넓으면 dry-run이 거짓말을 한다.** `--review-out`만 보고
    전 언어에 대해 줄을 찍는 구현은 `th`의 `review.json`을 예고하는데 본
    실행은 내지 않는다 - dry-run을 CI 사전 점검으로 쓰는 사용자가 없는
    파일을 기다린다. 규칙(`target in profiles`)이 호출자 한 곳에만 있어야
    하는 이유다.

    **`en`의 줄이 남아 있는 것을 함께 본다.** 없으면 "리포트 줄을 통째로
    지우는" 변이도 이 테스트를 통과한다.
    """
    _patch_provider(monkeypatch, EchoProvider())
    reports = tmp_path / "reports"

    args = _args(
        tmp_path, "minimal.srt", "--review-budget", "10%", "--review-out", str(reports), "--dry-run"
    )
    args[args.index("--to") + 1] = "en,th"
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    output = normalize_rich_message(result.output)
    # `"리포트"`가 아니라 `.review.json`을 센다 - 위 테스트의 주석 참고.
    assert output.count(".review.json") == 1, f"프로파일 없는 언어까지 예고했다\n{result.output}"
    assert "minimal.en.review.json" in output, result.output
    assert "minimal.th.review.json" not in output, "없을 파일을 예고했다"
    # 번역 자체는 두 언어 모두 예고돼야 한다 - 건너뛰는 것은 트리아지이지
    # 번역이 아니다(`load_builtin` 실패 경로의 주석).
    assert "[th]" in output, "th의 번역 예고까지 사라졌다"


def test_쓰기_실패는_exit_66이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D12 — 디스크 상태의 문제이지 명령줄 오류가 아니다.

    번역 파일은 이미 나갔다는 것도 함께 고정한다 - 리포트만 못 쓴 것이지
    번역이 실패한 것이 아니다(설계 §3.4).
    """
    _patch_provider(monkeypatch, EchoProvider())

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("디스크가 가득 찼다")

    monkeypatch.setattr("cuesift.cli.write_review", boom)

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(tmp_path / "reports"),
        ),
    )

    assert result.exit_code == 66, result.output
    assert (tmp_path / "subs" / "minimal.en.srt").exists(), "번역까지 잃었다"


@pytest.mark.parametrize(
    ("exc", "label"),
    [
        # `json.dumps`가 직렬화 불가 객체·`set`·tuple 키에서 내는 것.
        (TypeError("Object of type object is not JSON serializable"), "TypeError"),
        # **순환 참조의 실제 타입이다** - `TypeError`가 아니다(실측).
        # 이 케이스가 없으면 `except Exception`을 `except TypeError`로
        # 좁히는 변이가 살아남는다: 위 한 줄만으로는 넓은 catch와 좁은 catch가
        # 구별되지 않는다.
        (ValueError("Circular reference detected"), "ValueError"),
        # 깊은 중첩. `RecursionError`는 `RuntimeError` 하위라 `ValueError`
        # 계열을 추가로 열거하는 식의 부분 수정도 여기서 걸린다.
        (RecursionError("maximum recursion depth exceeded"), "RecursionError"),
    ],
)
def test_직렬화_실패는_예외_타입과_무관하게_exit_70이다(
    exc: Exception, label: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """설계 §8 — exit 1("규격 위반 발견")로 새면 내부 결함이 자막 결함으로 오보된다.

    **exit 1이 최악인 이유는 "구별되지 않는다"였다** (리뷰 계약 축 I1 실측).
    번역 부분 실패가 75로 갈라진 지금은 종료 코드로 구별은 되지만, 1은
    `check`의 "규격 위반 발견"이라 새어 나간 내부 결함이 자막 결함으로
    읽힌다. CI는 멀쩡한 자막을 고치려 들고 리포트는 영영 안 나온다.

    **세 타입을 모두 도는 것이 이 게이트의 핵심이다.** `TypeError` 하나만 보면
    `except Exception`을 `except TypeError`로 되돌리는 변이가 통과한다 -
    실측으로 `ValueError`(순환 참조)·`RecursionError`(깊은 중첩)·
    `UnicodeEncodeError`가 전부 그 그물을 빠져나가 exit 1 + traceback이 됐다.

    **traceback이 없다는 것도 함께 본다.** 미처리 예외로 죽으면 종료 코드가
    맞아도 사용자는 스택트레이스를 본다 - 그것은 "이 도구가 깨졌다"는 신호이지
    "리포트를 못 냈다"는 진단이 아니다.
    """
    _patch_provider(monkeypatch, EchoProvider())

    def boom(*_args: object, **_kwargs: object) -> None:
        raise exc

    monkeypatch.setattr("cuesift.cli.write_review", boom)

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(tmp_path / "reports"),
        ),
    )

    assert result.exit_code == 70, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"미처리 예외가 샜다: {result.exception!r}"
    )
    # **예외 타입명을 메시지에 넣는 계약**(넓은 catch를 택한 대가를 줄인다).
    # 없으면 `ValueError`(리포트 구조에 순환이 생겼다)와 `NameError`(버그를
    # 신고해야 한다)가 사용자에게 같은 모양으로 보인다.
    assert label in result.output, "예외 타입명이 없으면 진단을 구별할 수 없다"


def test_NaN이_섞이면_ValueError로_exit_70이_된다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**`allow_nan=False`가 만든 경로를 CLI 끝까지 고정한다.**

    위 세 케이스는 `write_review`를 통째로 가짜로 바꿔 "예외가 나면 70"만
    잰다. 여기는 **진짜 `write_review`를 그대로 두고** `json.dumps`가 실제로
    `ValueError`를 내게 한다 - `allow_nan=False`를 지우는 변이는 위 셋을
    전부 통과하고 여기서만 죽는다.

    **왜 문서를 오염시키는가.** 오늘 `NaN`의 도달 경로는 0이다(`Signal`의
    범위 검사·`_parse_review_budget`·`math.isnan` 가드·정수 나눗셈이 4중으로
    막는다 - 리뷰어 실측). 열려 있는 자리는 `detail` 하나인데 그것을 채우는
    것은 신호 구현이라, 여기서 신호를 흉내 내면 **이 태스크가 만들지 않은
    미래의 신호**를 픽스처로 삼는 셈이 된다. 그래서 마지막 공통 통로인
    `build_review`의 반환 문서에 한 값을 심는다 - 재는 것은 "누가 넣었나"가
    아니라 **"넣으면 파일로 나가는가"**다.

    **파일이 남지 않아야 한다는 것을 함께 본다.** `NaN`은 파이썬
    `json.loads`가 되읽으므로, 파일이 나가면 이 저장소의 테스트로는 영영
    안 잡히고 `jq`·JS `JSON.parse`를 쓰는 검수자 쪽에서만 깨진다.
    """
    _patch_provider(monkeypatch, EchoProvider())
    reports = tmp_path / "reports"
    real_build = json_report.build_review

    def poisoned(outcome: object) -> dict:
        doc = real_build(outcome)  # type: ignore[arg-type]
        doc["summary"]["signal_hits"]["qe.dummy"] = float("nan")
        return doc

    monkeypatch.setattr("cuesift.report.json_report.build_review", poisoned)

    result = runner.invoke(
        app,
        _args(tmp_path, "minimal.srt", "--review-budget", "10%", "--review-out", str(reports)),
    )

    assert result.exit_code == 70, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"미처리 예외가 샜다: {result.exception!r}"
    )
    assert "ValueError" in result.output, "예외 타입명이 없으면 진단을 구별할 수 없다"
    assert not (reports / "minimal.en.review.json").exists(), "규격 위반 JSON이 파일로 나갔다"
    assert (tmp_path / "subs" / "minimal.en.srt").exists(), "번역까지 잃었다"


def test_언어별로_파일이_나오고_프로파일이_각각_다르다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 10.4-4 — 프로파일 이름이 값 검증의 유일한 수단이다.

    `profiles[target] = load_builtin("ko")` 변이가 전 스위트를 통과한 전례가 있다
    (Task 2 리뷰 축A I4) - 키 집합만 검증되고 값은 검증되지 않았다.
    """
    _patch_provider(monkeypatch, EchoProvider())
    reports = tmp_path / "reports"

    args = _args(tmp_path, "minimal.srt", "--review-budget", "10%", "--review-out", str(reports))
    args[args.index("--to") + 1] = "en,ja"
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    en = json.loads((reports / "minimal.en.review.json").read_text(encoding="utf-8"))
    ja = json.loads((reports / "minimal.ja.review.json").read_text(encoding="utf-8"))

    assert en["summary"]["profile"] == "en"
    assert ja["summary"]["profile"] == "ja"
    assert en["summary"]["target_lang"] == "en"
    assert ja["summary"]["target_lang"] == "ja"


def test_전량_실패해도_파일이_사실을_말한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """번역이 전량 실패해도 리포트를 낸다. 소비자가 "왜 비었나"를 알아야 한다.

    파일이 아예 없으면 "실행이 안 됐다"와 "번역이 전량 실패했다"를 구분하지 못한다.

    `garbage=True`면 **배치도 개별 폴백도 전부 파싱 실패한다**
    (`tests/test_cli_translate.py:215`의 주석과 같은 성질). 그때 종료 코드는
    **1**(실패한 세그먼트가 있다)이고 리포트는 그보다 먼저 나간다.
    """
    _patch_provider(monkeypatch, EchoProvider(garbage=True))

    result = runner.invoke(
        app,
        _args(
            tmp_path,
            "minimal.srt",
            "--review-budget",
            "10%",
            "--review-out",
            str(tmp_path / "reports"),
        ),
    )

    assert result.exit_code == EXIT_TRANSLATION_FAILURE, result.output
    doc = _read_review(tmp_path)
    assert doc["summary"]["triaged_segments"] == 0
    assert doc["summary"]["excluded_failures"] > 0
    assert doc["summary"]["total_segments"] == doc["summary"]["excluded_failures"]
    assert doc["segments"] == []


def test_프로파일_없는_언어는_리포트도_내지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**설계 공백을 여기서 닫는다** (Task 5 리뷰 우려 ③).

    `--to en,th`에서 th 프로파일은 없다. 기존 동작은 **경고 후 그 언어의
    트리아지만 건너뛰고 exit 0**이다(D7 — 전량 거부하면 프로파일이 있는 언어의
    트리아지까지 잃는다). 그러면 th는 `triage_profile=None`이라 트리아지 블록
    자체가 돌지 않고, 따라서 낼 `TriageOutcome`이 없다.

    **리포트도 내지 않는 것이 옳다** — 트리아지를 하지 않았는데 파일을 내면
    소비자는 "th를 검수했고 걸린 것이 없다"로 읽는다. 실제로는 **판정 자체를
    못 한 것**이고, 그 구별이 전량 실패 경로에서 이미 1급 요구다
    (`_run_triage`의 "번역된 세그먼트가 없어 건너뛴다"와 같은 논리).

    이 테스트가 없으면 그 사실이 어디에도 고정되지 않아, 나중에 빈 리포트를
    내도록 바뀌어도 아무 게이트가 울리지 않는다.
    """
    _patch_provider(monkeypatch, EchoProvider())
    reports = tmp_path / "reports"

    args = _args(tmp_path, "minimal.srt", "--review-budget", "10%", "--review-out", str(reports))
    args[args.index("--to") + 1] = "en,th"
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    # **`"th" in output`으로는 아무것도 재지 못한다** (리뷰 축B I1 실측).
    # th는 번역이 되므로 `[th] ...\minimal.th.srt` 줄이 **경고와 무관하게 항상**
    # 있다 - 경고에서 언어 이름을 지우는 변이가 이 테스트를 그대로 통과했다.
    # 경고 **문구**를 봐야 한다. `normalize_rich_message`를 통과시키는 것은
    # 이 메시지가 언젠가 rich 경로로 옮겨질 때를 대비한 것이고(같은 파일의
    # `--review-out` 단언과 같은 이유), 그 함수가 공백을 전부 지우므로
    # **needle도 공백 없는 형태여야 한다.**
    assert "규격프로파일이없어" in normalize_rich_message(result.output), (
        "프로파일이 없다는 경고가 나가야 한다"
    )
    assert sorted(p.name for p in reports.glob("*.review.json")) == ["minimal.en.review.json"]
