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
from tests.fakes.provider import EchoProvider, ScriptedProvider
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


def _blank_at(indices: set[int], count: int) -> ScriptedProvider:
    """지정한 인덱스만 **공백 번역**으로 답하는 가짜 (`test_cli_triage.py:459`에서 옮김).

    공백 번역은 `engine.py:419`가 `reason="empty_translation"`으로 실패 처리한다 -
    응답 형식은 올바르므로 개별 폴백이 개입하지 않아 호출이 배치 1회로 끝난다.
    `EchoProvider(drop_last=True)`는 이 목적에 쓸 수 없다: 배치가 개수 불일치로
    실패하면 폴백이 개별 호출로 재시도하고 거기서는 `len(items) > 1`이 거짓이라
    **전부 성공한다.**
    """
    items = [{"id": i, "text": "   " if i in indices else f"EN{i}"} for i in range(count)]
    return ScriptedProvider([json.dumps({"translations": items}, ensure_ascii=False)])


def test_화면_요약과_파일_수치가_일치한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**이 설계에서 가장 조용한 실패다** (D8 · 게이트 10.1).

    **Task 4의 Step 7b가 못 잡는 것을 여기가 잡는다.** 그 grep 게이트는
    `len(outcome.risks)`·리스트 컴프리헨션 같은 재도입 형태를 통과시킨다(실측:
    변이 둘이 게이트와 전 스위트를 모두 빠져나갔다). 화면과 파일이 한 실행에
    함께 존재하는 이 자리라야 값을 직접 대조할 수 있다.

    갈라져도 프로그램은 정상 종료하고 파일도 정상이며 종료 코드도 0이다.
    화면에서 파싱한 값과 `summary`를 대조하는 것만이 이것을 잡는다.

    **`_blank_at`을 쓰는 것이 이 게이트의 핵심이다** (사전 스캔 발견 A).
    기본 `EchoProvider()`는 한글 원문을 남겨 `struct.untranslated`가 **전량
    hard fail**을 내고, 그러면 `selected == triaged`가 되어 두 값을 뒤바꾸는
    변이가 **같은 값을 내며 통과한다** - 게이트가 통과하면서 아무것도 재지
    못하는 상태다. `_blank_at({2,5,9}, 10)`이면 `triaged=7`이고 예산 10%에서
    `quota=ceil(7*0.1)=1`이라 `selected=1 != triaged=7`로 갈린다.

    실패 3건이 있으므로 종료 코드는 **1**이다(FR-2.6). 리포트는 그보다 먼저 나간다.
    """
    _patch_provider(monkeypatch, _blank_at({2, 5, 9}, 10))

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

    assert result.exit_code == 1, result.output
    summary = _read_review(tmp_path, "ten_cues.en.review.json")["summary"]

    assert summary["triaged_segments"] == 7
    assert summary["selected_for_review"] != summary["triaged_segments"], (
        "두 값이 같으면 이 게이트가 아무것도 재지 못한다"
    )
    assert f"  대상 세그먼트 {summary['triaged_segments']}개" in result.output
    assert f"  검수 대상 {summary['selected_for_review']}개" in result.output
    assert f"  hard fail {summary['hard_fail_count']}개" in result.output


def test_신호별_적발_집계가_화면과_일치한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 10.1의 나머지 절반 — `signal_hits` 대조는 집계가 비지 않아야 한다.

    **여기서는 기본 `EchoProvider()`를 일부러 쓴다.** 한글 원문이 남아
    `struct.untranslated`가 전량 hard fail을 내므로 `signal_hits`가 채워진다.
    위 테스트의 `_blank_at`은 번역문이 `EN0`·`EN1`이라 신호가 적거나 없을 수
    있고, 집계가 비면 아래 루프가 **한 번도 돌지 않아 아무것도 재지 못한다.**
    """
    _patch_provider(monkeypatch, EchoProvider())

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

    assert summary["signal_hits"], "집계가 비면 아래 루프가 아무것도 재지 못한다"
    for name, count in summary["signal_hits"].items():
        assert f"    {name} {count}개" in result.output


def test_total이_triaged와_excluded의_합이다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 10.2 — 분모가 조용히 바뀌면 README 배수가 무너진다.

    **`excluded`가 0이면 `total == triaged + 0`이 항등식이 되어 아무것도 재지
    못한다** (사전 스캔 발견 B). `_blank_at`으로 실패 3건을 만들어야 검산이
    성립한다.
    """
    _patch_provider(monkeypatch, _blank_at({2, 5, 9}, 10))

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

    assert result.exit_code == 1, result.output
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
    """D11 — dry-run은 트리아지를 돌리지 않으므로 낼 것이 없다."""
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


def test_직렬화_실패는_exit_70이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """설계 §8 — exit 1("규격 위반 발견")로 새면 내부 결함이 자막 결함으로 오보된다."""
    _patch_provider(monkeypatch, EchoProvider())

    def boom(*_args: object, **_kwargs: object) -> None:
        raise TypeError("Object of type object is not JSON serializable")

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

    assert result.exit_code == 1, result.output
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
    assert "th" in result.output, "프로파일이 없다는 경고가 나가야 한다"
    assert sorted(p.name for p in reports.glob("*.review.json")) == ["minimal.en.review.json"]
