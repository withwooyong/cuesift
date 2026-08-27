"""`--tier1` 표면과 조합 검증 (FR-4.3 · 설계 D1~D4·D9).

**옵션이 조용히 무시되는 것이 가장 나쁘다.** "켰다고 믿는 실행"은 종료 코드도
0이고 파일도 정상이라 어떤 게이트에도 걸리지 않는다.

**여기의 단언은 종료 코드만 봐서는 안 된다.** `translate`의 입력 인자가
`exists=True`이고 typer의 파일 존재 검사는 본문보다 **먼저** 돈다. 그 검사도
조합 검증과 똑같이 exit 2를 내므로, 입력 파일이 없거나 검증이 구현돼 있지
않아도 `exit_code == 2`만 보는 테스트는 초록이 된다 - 실측(옵션 미구현 ·
존재하지 않는 입력 경로): 7개 중 3개가 그렇게 통과했다.

**같은 함정이 합법 조합에도 있다.** 검증을 통째로 지워도 뒤의 `_resolve_llm`이
`--base-url` 없음으로 exit 2를 낸다. 그래서 이 파일의 단언은 종료 코드가 아니라
**어느 층이 거부했는지**를 본다.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
import typer.main
from typer.testing import CliRunner

from cuesift.cli import (
    _TIER1_COST_LIMIT,
    _TIER1_DEFAULT_MAX_RATIO,
    _TIER1_DEFAULT_SAMPLES,
    _TIER1_DEFAULT_TEMPERATURE,
    app,
)
from cuesift.tier1 import triage_with_tier1

runner = CliRunner()

# 조합 검증은 파일을 읽기 전에 끝나므로 내용은 무엇이든 된다. 그래도 실제
# 자막으로 두는 것은 검증이 뒤로 밀렸을 때 인제스트 오류가 아니라 조합 오류로
# 실패하게 하려는 것이다.
_SRT = "1\n00:00:01,000 --> 00:00:03,000\n안녕하세요\n"

# `_resolve_llm`이 내는 문구의 조각. **이것이 보이면 조합 검증을 통과했다는
# 뜻이다** - Tier 1 층은 여기까지 오지 못하게 막는 것이 임무다.
_다음_층 = "--base-url"


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """사용자 환경이 테스트 결과를 바꾸지 못하게 한다.

    **양성 테스트가 이것에 의존한다.** `CUESIFT_BASE_URL`이 설정된 기계에서는
    `_resolve_llm`이 성공해 합법 조합이 **실제 번역으로 진행**한다 - 네트워크를
    타고, 단언은 엉뚱한 이유로 무너진다.
    """
    for name in ("CUESIFT_BASE_URL", "CUESIFT_MODEL", "CUESIFT_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def input_srt(tmp_path: Path) -> Path:
    """**실재하는** 입력 파일을 만든다.

    파일명을 지어내기만 하면(예: `"샘플.srt"`) typer의 `exists=True`가 본문
    앞에서 exit 2를 내고, 아래 테스트들은 조합 검증 코드에 **닿지도 못한 채**
    종료 코드만 맞아 통과한다.

    `tmp_path`에 쓰는 이유는 격리다 - 체크인된 `tests/fixtures/`를 쓰면
    검증이 빠졌을 때 `--out` 기본값(입력 파일과 같은 디렉터리)이 리포 안에
    번역 산출물을 떨군다.
    """
    path = tmp_path / "sample.srt"
    path.write_text(_SRT, encoding="utf-8")
    return path


def _args(input_srt: Path, *extra: str) -> list[str]:
    """기존 `tests/test_cli_translate.py::_args`의 최소 형태를 따른다."""
    return ["translate", str(input_srt), "--to", "en", *extra]


@pytest.mark.parametrize(
    ("option", "value"),
    [
        # **falsy 값을 반드시 넣는다.** `tier1_options_given`이 `is None`이
        # 아니라 truthy로 판정하도록 바뀌면 `0`을 준 경우만 조용히 새어 나가고,
        # truthy 값만 시험하는 스위트는 전원 초록이다(실측: 변이 1건 생존).
        ("--tier1-max-ratio", "0"),
        ("--tier1-max-ratio", "0.05"),
        ("--tier1-samples", "2"),
        ("--tier1-samples", "5"),
        ("--tier1-temperature", "0"),
        ("--tier1-temperature", "1.0"),
    ],
)
def test_tier1_없이_tier1_옵션을_주면_거부한다(input_srt: Path, option: str, value: str) -> None:
    """**세 옵션을 모두 시험한다.** `tier1_options_given`은 3항 OR인데 한 항만
    시험하면 나머지 두 항을 지워도 스위트가 초록이다(실측).

    **메시지까지 본다.** `"--tier1" in output`만으로는 typer가 낸
    `No such option: --tier1-samples`도 통과시킨다 - 부분 문자열이라 옵션이
    아예 없는 상태와 구별이 안 된다(실측).
    """
    result = runner.invoke(app, _args(input_srt, option, value))
    assert result.exit_code == 2
    assert "--tier1과 함께 써야 한다" in result.output


def test_tier1은_review_threshold와_함께_쓸_수_없다(input_srt: Path) -> None:
    """`triage_with_tier1`이 `select_by_budget`을 고정으로 쓴다(D9).

    회색지대 개념 자체가 예산 선별의 부산물이라 threshold에서는 정의가 안 선다.
    """
    result = runner.invoke(app, _args(input_srt, "--tier1", "--review-threshold", "0.7"))
    assert result.exit_code == 2
    assert "--review-threshold" in result.output


def test_tier1은_트리아지_정책을_요구한다(input_srt: Path) -> None:
    result = runner.invoke(app, _args(input_srt, "--tier1"))
    assert result.exit_code == 2
    assert "--review-budget" in result.output


def test_tier1_max_ratio_0은_모순이라_거부한다(input_srt: Path) -> None:
    """라이브러리가 `0.0`을 '껐다'로 정의한다 - 스위치와 정면으로 어긋난다.

    **메시지 단언이 있어야 한다.** 종료 코드만 보면 click의 `min=0.0`이 0을
    통과시키고 검증이 통째로 빠져도 (입력 파일이 없던 시절엔) 초록이었다.
    """
    result = runner.invoke(
        app, _args(input_srt, "--tier1", "--review-budget", "10%", "--tier1-max-ratio", "0")
    )
    assert result.exit_code == 2
    assert "끄는 값" in result.output


def test_비용_한도를_넘는_조합을_거부한다(input_srt: Path) -> None:
    """배수 = samples x max_ratio x DEFAULT_BATCH_SIZE. 한도는 §4의 3배다."""
    result = runner.invoke(
        app,
        _args(
            input_srt,
            "--tier1",
            "--review-budget",
            "10%",
            "--tier1-samples",
            "10",
            "--tier1-max-ratio",
            "0.1",
        ),
    )
    assert result.exit_code == 2
    # **곱과 한도를 둘 다 말해야 한다.** 어느 쪽을 줄여야 하는지 알 수 없으면
    # 사용자는 임의로 고른다.
    assert "1.0" in result.output or "1.00" in result.output
    assert "0.3" in result.output


@pytest.mark.parametrize(
    ("samples", "max_ratio"),
    [
        # 셋 다 **정확히 3.0배**다. 이진 부동소수 표현만 다르다:
        # `3 * 0.1 = 0.30000000000000004` · `2 * 0.15 = 0.3` · `30 * 0.01 = 0.3`.
        # `>` 비교는 첫 줄만 거부하고 나머지 둘을 통과시킨다(실측) -
        # **같은 배수가 반대 판정을 받는 것이 버그다.**
        ("3", "0.1"),
        ("2", "0.15"),
        ("30", "0.01"),
    ],
)
def test_한도에_정확히_닿는_조합도_거부한다(input_srt: Path, samples: str, max_ratio: str) -> None:
    """설계 D3과 `tier1.py`가 "`max_ratio=0.10`이 한도에 **정확히** 걸린다"고
    못 박았다. 3.0배는 §4의 "감당 불가"라 거부가 의도다.

    `30 x 0.01`이 특히 위험하다 - 비용 배수는 한도와 같으면서 세그먼트당
    **30회**를 부른다.
    """
    result = runner.invoke(
        app,
        _args(
            input_srt,
            "--tier1",
            "--review-budget",
            "10%",
            "--tier1-samples",
            samples,
            "--tier1-max-ratio",
            max_ratio,
        ),
    )
    assert result.exit_code == 2
    assert "닿거나 넘는다" in result.output
    # 표현이 달라도 사용자에게 보이는 곱은 셋 다 같아야 한다.
    assert "0.30" in result.output
    assert _다음_층 not in result.output


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--tier1-max-ratio", "nan"),
        ("--tier1-temperature", "nan"),
        ("--tier1-temperature", "inf"),
    ],
)
def test_유한하지_않은_수는_거부한다(input_srt: Path, option: str, value: str) -> None:
    """**click의 `min`/`max`는 NaN을 통과시킨다** - `--review-threshold`가 겪은
    것과 같은 구멍이다(`cli.py`의 `math.isnan` 주석).

    막지 않으면 비용 한도 검사가 `nan > 0.3`(False)으로 **조용히 통과**한다 -
    검사하지 않고 통과하는 게이트는 없는 게이트보다 나쁘다.

    **메시지를 단언한다.** 종료 코드만 보면 검증을 빼도 뒤의 `_resolve_llm`이
    `--base-url` 없음으로 exit 2를 내 초록이 된다.
    """
    result = runner.invoke(
        app, _args(input_srt, "--tier1", "--review-budget", "10%", option, value)
    )
    assert result.exit_code == 2
    assert f"{option}를 숫자로 읽지 못했다" in result.output


@pytest.mark.parametrize(
    "extra",
    [
        # 기본값 조합. 3 x 0.05 = 0.15 로 한도의 절반이다.
        (),
        # 한도 **바로 아래**. 2 x 0.14 = 0.28. 위 `2 x 0.15`(= 0.30, 거부)와
        # 짝을 이뤄 경계가 어디인지를 양쪽에서 고정한다.
        ("--tier1-samples", "2", "--tier1-max-ratio", "0.14"),
    ],
)
def test_합법_조합은_조합_검증을_통과한다(input_srt: Path, extra: tuple[str, ...]) -> None:
    """**거부만 증명하는 스위트는 절반짜리다.**

    조합 검증을 통째로 지워도 이 명령은 exit 2로 끝난다 - `_resolve_llm`이
    `--base-url` 없음으로 거부하기 때문이다. 그래서 종료 코드가 아니라
    **어느 층이 거부했는지**를 본다: `--base-url` 문구가 보이면 Tier 1 검증을
    빠져나온 것이고, Tier 1 문구가 보이면 통과하지 못한 것이다.

    이 테스트가 없으면 "전부 거부"로 바꾼 구현이 스위트를 전원 통과시킨다.
    """
    result = runner.invoke(app, _args(input_srt, "--tier1", "--review-budget", "10%", *extra))
    assert _다음_층 in result.output
    assert "--tier1" not in result.output


def test_기본값_조합은_한도_안이다() -> None:
    """3 x 0.05 = 0.15 <= 0.3. 이것이 D3의 근거다."""
    assert _TIER1_DEFAULT_SAMPLES * _TIER1_DEFAULT_MAX_RATIO <= _TIER1_COST_LIMIT


def test_기본값_상수가_라이브러리_기본값과_같다() -> None:
    """**부등식만으로는 드리프트가 안 잡힌다.** `_TIER1_DEFAULT_SAMPLES`를
    3에서 4로 바꿔도 `0.20 <= 0.3`이라 위 테스트는 초록이다(실측: 전체 1234개
    중 0개 사망).

    갈라지면 CLI의 비용 한도 검사와 실제 호출이 **서로 다른 수**를 쓴다 -
    한도를 통과한 조합이 한도를 넘는 비용을 쓰게 되는 것이다.

    `triage_with_tier1`이 이 값들을 모듈 상수가 아니라 **시그니처 기본값**으로
    들고 있어(단일 출처) import가 아니라 `inspect`로 읽는다.
    """
    파라미터 = inspect.signature(triage_with_tier1).parameters
    assert 파라미터["samples"].default == _TIER1_DEFAULT_SAMPLES
    assert 파라미터["temperature"].default == _TIER1_DEFAULT_TEMPERATURE


def test_temperature_0은_거부한다(input_srt: Path) -> None:
    """0이면 재번역이 전부 같아 자가일관성 점수가 **항상 0.0**이 된다 -
    신호가 죽었는데 '안전'으로 보고되는 무음 열화다(Q3).

    **메시지 단언이 있어야 한다.** click의 `min=0.0`은 경계를 포함하므로 0이
    그대로 본문까지 온다 - 종료 코드만 보면 검증이 없어도 잡히지 않는다.
    """
    result = runner.invoke(
        app, _args(input_srt, "--tier1", "--review-budget", "10%", "--tier1-temperature", "0")
    )
    assert result.exit_code == 2
    assert "0보다 커야 한다" in result.output


def test_도움말에_네_옵션이_모두_있다() -> None:
    """**색을 켠 채로 돈다.** rich 하이라이터가 긴 옵션 이름을 조각내는 사례가
    이 저장소에서 관측됐다 - 폭이 아니라 색이 원인이라 색을 끄면 안 잡힌다.

    `--tier1`은 나머지 셋의 **접두사**라 부분 문자열로 세면 언제나 있는 것으로
    나온다. 뒤에 이름 문자가 오지 않는 자리만 세어 스위치 자체를 확인한다.
    """
    result = runner.invoke(app, ["translate", "--help"], color=True)
    assert result.exit_code == 0
    for name in ("--tier1-max-ratio", "--tier1-samples", "--tier1-temperature"):
        assert name in result.output
    assert re.search(r"--tier1(?![\w-])", result.output) is not None


def test_도움말의_기본값이_상수와_같다() -> None:
    """**도움말이 조용히 거짓말하는 것을 막는다.** 기본값이 리터럴이면 상수만
    고쳐졌을 때 화면은 옛 값을 계속 말하고, 사용자는 그 값으로 비용을 계산한다.

    rich가 도움말을 줄바꿈하므로 렌더된 화면이 아니라 **선언된 help 문자열**을
    읽는다 - 폭에 따라 깨지는 단언은 게이트가 아니다.
    """
    translate_cmd = typer.main.get_command(app).commands["translate"]  # type: ignore[attr-defined]
    helps = {param.name: (getattr(param, "help", "") or "") for param in translate_cmd.params}
    assert f"기본 {_TIER1_DEFAULT_MAX_RATIO}" in helps["tier1_max_ratio"]
    assert f"기본 {_TIER1_DEFAULT_SAMPLES}" in helps["tier1_samples"]
    assert f"기본 {_TIER1_DEFAULT_TEMPERATURE}" in helps["tier1_temperature"]
