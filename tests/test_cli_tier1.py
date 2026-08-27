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
import json
import re
from collections.abc import Sequence
from pathlib import Path

import pytest
import typer.main
from tests.fakes.provider import EchoProvider
from typer.testing import CliRunner, Result

from cuesift import cli as cli_module
from cuesift.cli import (
    _TIER1_BOUND_PREFIX,
    _TIER1_COST_LIMIT,
    _TIER1_DEFAULT_MAX_RATIO,
    _TIER1_DEFAULT_SAMPLES,
    _TIER1_DEFAULT_TEMPERATURE,
    _TIER1_WARN_PREFIX,
    app,
)
from cuesift.tier1 import triage_with_tier1
from cuesift.translate import (
    ChatMessage,
    Completion,
    CountingProvider,
    FatalProviderError,
    ProviderError,
)

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


# --- 배선 테스트 (Task 6 - FR-4.3 · FR-7.4) ---
#
# 위쪽 조합 검증과 달리 여기는 **실제로 파이프라인을 끝까지 돌린다.** 가짜
# 프로바이더를 꽂아 네트워크를 타지 않는다 - 실물 LLM에 의존하는 테스트는
# CI에서 못 돈다.

_FIXTURES = Path(__file__).parent / "fixtures" / "ingest"

# 10큐에서 `floor(10 x 0.14) = 1` 후보 x 샘플 2 = **LLM 2회**. 곱이 0.28이라
# 비용 한도(0.3) 바로 아래다.
#
# **기본값(0.05 x 3)을 쓰면 안 된다.** `floor(10 x 0.05) = 0`이라 Tier 1이
# 통째로 안 돌고, 토큰 단언이 `0 == 0`으로 조용히 통과한다.
_TIER1_RUNS = ("--tier1-max-ratio", "0.14", "--tier1-samples", "2")


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: object) -> None:
    """`tests/test_cli_translate.py`·`tests/test_cli_review_out.py`와 같은 수단.

    `_build_provider`를 통째로 바꾸므로 **번역과 Tier 1이 같은 가짜를 쓴다.**
    """
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: provider)


def _clean_echo() -> EchoProvider:
    """번역문에 원문을 남기지 않는 가짜.

    **`EchoProvider()` 기본값을 그대로 쓰면 안 된다.** 기본 변환(`EN:{원문}`)은
    한글을 그대로 남겨 `llm.untranslated`가 10큐 **전부**를 hard fail로 만든다 -
    그러면 회색지대가 비어 Tier 1 후보가 언제나 0건이 되고, 토큰 단언이
    `0 == 0`으로 통과한다(실측: 기본 변환에서 hard_fail 10 · gray_zone 0,
    깨끗한 변환에서 hard_fail 0 · gray_zone 9 · 후보 1건).
    """
    return EchoProvider(transform=lambda _: "Hello there")


# `ten_cues.srt`의 인덱스 2·5·9. **번역문이 아니라 원문으로 찍는다** -
# `conftest.blank_at`은 `ScriptedProvider`라 응답이 **하나뿐**이고, Tier 1이
# 실제로 돌면 "대본이 소진됐다"로 죽는다. Tier 1을 돌리는 테스트는 계속 답하는
# 가짜가 필요하다.
_실패시킬_원문 = frozenset({"셋째 줄입니다", "여섯째 줄입니다", "열째 줄입니다"})


def _echo_failing_three() -> EchoProvider:
    """세 큐만 공백 번역으로 답해 번역 실패 3건을 만든다.

    공백 번역은 `engine.py`가 `reason="empty_translation"`으로 실패 처리한다.
    Tier 1 후보는 실패분에서 제외되므로(설계 D5) 재번역 요청은 이 집합에
    닿지 않는다 - 남은 일곱만 `Hello there`를 받는다.
    """
    return EchoProvider(transform=lambda s: "   " if s in _실패시킬_원문 else "Hello there")


def _assert_tier1_ran(fake: EchoProvider, *, targets: int = 1) -> None:
    """Tier 1이 **실제로 LLM을 불렀는지** 확인한다.

    **이것이 없으면 대부분의 단언이 무연산 위에서 초록이 된다.** 후보 0건이면
    `triage_with_tier1`이 LLM을 한 번도 안 부르고 조기 반환하는데, 그때도
    `cost.includes`는 `["translation", "tier1"]`이고 종료 코드도 파일 형태도
    똑같다 - Ruling P12가 `warn`을 필수 인자로 만든 이유가 이것이다.

    **부재 단언이 아니라 긍정 단언이다.** 이전 판은 `"Tier 1:" not in output`
    으로 경고 줄의 **부재**를 봤는데, 그러면 접두 문구를 바꾸는 것만으로 단언이
    조용히 항상 참이 된다(리뷰 축2 실측: 사망 0건 · 도달성 프로브 사망 8건에서
    4건으로 반토막). 프로바이더가 실제로 몇 번 불렸는지는 문구와 무관하다.

    **분모가 `targets`인 근거**: 이 파일의 픽스처는 10큐이고
    `DEFAULT_BATCH_SIZE`가 10이라 번역은 **대상 언어당 배치 1회**로 끝난다.
    따라서 그보다 많이 불렸다면 그 초과분은 Tier 1 재번역뿐이다. 픽스처가
    배치 하나를 넘게 커지면 이 단언이 **거짓 실패**로 그 사실을 알린다 -
    조용히 통과하는 것보다 낫다.
    """
    번역호출 = targets
    assert len(fake.calls) > 번역호출, (
        f"프로바이더가 {len(fake.calls)}회 불렸다 - 번역({번역호출}회) 말고는 "
        "아무것도 나가지 않았다. Tier 1 후보가 0건이면 이 스위트의 단언 대부분이 "
        "무연산 위에서 초록이 된다"
    )


def _full_args(
    tmp_path: Path,
    *extra: str,
    fixture: str = "ten_cues.srt",
    cache_dir: Path | None = None,
    to: str = "en",
) -> list[str]:
    """끝까지 도는 실행의 인자. `tests/test_cli_review_out.py::_args`를 따른다.

    자막은 `subs/`, 리포트는 `reports/`로 **나눈다** - 같은 디렉터리에 두면
    경로 결정이 통째로 틀려도 뭔가가 찾아져 통과한다.

    `cache_dir`가 `None`이면 `--no-cache`다. **기본을 캐시 켜짐으로 두면 안
    된다** - `DEFAULT_CACHE_DIR`(`.cuesift/cache`)가 리포 안에 캐시를 떨군다.
    """
    cache = ["--no-cache"] if cache_dir is None else ["--cache-dir", str(cache_dir)]
    return [
        "translate",
        str(_FIXTURES / fixture),
        "--to",
        to,
        "--out",
        str(tmp_path / "subs"),
        "--base-url",
        "http://h/v1",
        "--model",
        "m1",
        "--review-budget",
        "10%",
        "--review-out",
        str(tmp_path / "reports"),
        *cache,
        *extra,
    ]


def _read_review(tmp_path: Path, name: str = "ten_cues.en.review.json") -> dict:
    return json.loads((tmp_path / "reports" / name).read_text(encoding="utf-8"))


def _run_plain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
    provider: object | None = None,
    cache_dir: Path | None = None,
) -> Result:
    _patch_provider(monkeypatch, _clean_echo() if provider is None else provider)
    return runner.invoke(app, _full_args(tmp_path, *extra, cache_dir=cache_dir))


def _run_tier1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
    provider: object | None = None,
    cache_dir: Path | None = None,
    to: str = "en",
) -> Result:
    _patch_provider(monkeypatch, _clean_echo() if provider is None else provider)
    return runner.invoke(app, _full_args(tmp_path, "--tier1", *extra, cache_dir=cache_dir, to=to))


def test_tier1을_켜면_cost_includes에_tier1이_실린다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-7.4. 이것이 없으면 파일이 '번역만 셌다'고 말하면서 실제로도 번역만 센다."""
    fake = _clean_echo()

    result = _run_tier1(tmp_path, monkeypatch, *_TIER1_RUNS, provider=fake)

    assert result.exit_code == 0, result.output
    # **`includes`만 보면 후보 0건에서도 통과한다** - `_TIER1_RUNS`가 장식이
    # 된다(리뷰 축2 실측: 후보를 0건으로 강제해도 이 테스트가 살았다).
    _assert_tier1_ran(fake)
    assert _read_review(tmp_path)["summary"]["cost"]["includes"] == ["translation", "tier1"]


def test_tier1을_끄면_cost_includes가_그대로다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기존 거동 불변 - 켜야만 섞인다(D2).

    **`None`과 `TokenUsage(0, 0, calls=N)`은 다르다.** 꺼진 계층에 후자를
    넘기면 `includes`에 실리면서 `unreported`까지 타 "돌았는데 계측이 죽었다"로
    보고된다 - 사용자가 없는 비용을 의심한다.
    """
    result = _run_plain(tmp_path, monkeypatch)

    assert result.exit_code == 0, result.output
    summary = _read_review(tmp_path)["summary"]
    assert summary["cost"]["includes"] == ["translation"]
    # 꺼진 계층에 `TokenUsage(0, 0, 0)`을 넘기면 `includes`에 실리고,
    # `layer_tokens_reported`가 `calls == 0`을 참으로 보므로 이 키는 그대로
    # `True`다 - 위 `includes` 단언만이 그 거짓말을 잡는다.
    assert summary["cost"]["tokens_reported"] is True


def test_tier1이_쓴_토큰이_usage에_더해진다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`CountingProvider`가 위임만 하고 누적하지 않으면 이 값이 안 는다.

    **`calls`만이 아니라 토큰까지 본다.** 호출 수는 늘고 토큰은 그대로인 배선
    (누적을 `calls` 한 줄로만 하는 변이)이 `calls` 단언만으로는 통과한다.
    """
    fake = _clean_echo()
    plain = _run_plain(tmp_path / "a", monkeypatch)
    tier1 = _run_tier1(tmp_path / "b", monkeypatch, *_TIER1_RUNS, provider=fake)

    assert plain.exit_code == 0, plain.output
    assert tier1.exit_code == 0, tier1.output
    _assert_tier1_ran(fake)
    일반 = _read_review(tmp_path / "a")["summary"]["cost"]
    티어1 = _read_review(tmp_path / "b")["summary"]["cost"]
    assert 티어1["calls"] > 일반["calls"]
    assert 티어1["completion_tokens"] > 일반["completion_tokens"]


def test_후보_0건이면_사유가_화면에_나온다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**유료 계층이 통째로 안 돌아도 반환값의 형태가 같다**(Ruling P12).

    `warn`을 침묵시키면 알아챌 다른 수단이 없다. 기본 `max_ratio`(0.05)와
    10큐에서 `floor(10 x 0.05) = 0`이라 후보가 없다.
    """
    result = _run_tier1(tmp_path, monkeypatch)

    assert result.exit_code == 0, result.output
    # **접두 문구를 상수로 못 박는다.** `_assert_tier1_ran`이 예전에 이 문구의
    # **부재**로 후보 유무를 판정했는데, 접두를 코드에서만 바꾸면 그 단언이
    # 조용히 항상 참이 됐다(리뷰 축2 실측: 사망 0건). 지금은 판정을 호출 수로
    # 옮겼지만, 사용자가 보는 줄이 실제로 이 모양인지는 **여기서만** 잰다 -
    # 이 단언이 없으면 사유 보고가 통째로 사라져도 스위트가 초록이다.
    assert f"[en] {_TIER1_WARN_PREFIX}" in result.output, result.output
    assert "max_ratio" in result.output


def test_번역_실패분이_있어도_분모가_같다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**D5의 CLI 쪽 게이트다**(설계 §3.1).

    `excluded_ids`를 안 넘기면 `triage_with_tier1`이 실패분까지 담은 목록을
    돌려주는데 `segments`는 `kept`(7건)라 `TriageOutcome`의 id 집합 불변식이
    `ValueError`를 던진다 - exit 2가 되고 `review.json`이 아예 안 나온다.

    **실패가 0이면 아무것도 재지 못한다** - 세 큐를 공백 번역으로 만든다.

    **`_TIER1_RUNS`를 쓰지 않는다.** 실패 3건을 빼면 `kept`가 7이라
    `floor(7 x 0.14) = 0`으로 후보가 0건이 된다 - 그러면 이 테스트 위에서
    Tier 1 경로의 변이가 전부 무연산이 된다(리뷰 축2 실측). `0.145`는
    `floor(7 x 0.145) = 1`을 내고 곱은 `2 x 0.145 = 0.29`로 한도 아래다.
    """
    실패조합 = ("--tier1-max-ratio", "0.145", "--tier1-samples", "2")
    fake = _echo_failing_three()
    plain = _run_plain(tmp_path / "a", monkeypatch, provider=_echo_failing_three())
    tier1 = _run_tier1(tmp_path / "b", monkeypatch, *실패조합, provider=fake)

    assert tier1.exit_code == plain.exit_code, tier1.output
    _assert_tier1_ran(fake)
    일반 = _read_review(tmp_path / "a")["summary"]
    티어1 = _read_review(tmp_path / "b")["summary"]
    assert 일반["excluded_failures"] == 3, "실패가 0이면 아래 검산이 항등식이 된다"
    assert 티어1["excluded_failures"] == 일반["excluded_failures"]
    assert 티어1["triaged_segments"] == 일반["triaged_segments"]


def test_계측이_캐시_안쪽에_놓인다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """설계 D7 - `CachingProvider(CountingProvider(raw))`여야 한다.

    **순서를 뒤집어도 종료 코드도 파일도 정상이다.** 캐시 히트는 토큰을 쓰지
    않는데 계측을 바깥에 두면 그것까지 세어져 `cost`가 부풀고, Recall@Budget
    배수의 분모가 오염된다.

    `triage_with_tier1`에 **실제로 넘어간 객체**를 본다 - 조립부의 지역 변수를
    믿지 않는다.

    **캐시를 켠 채로 돈다.** `--no-cache`면 순서를 뒤집는 변이가 무연산이 되어
    (감쌀 캐시가 없다) 이 단언이 통과한다 - 실측: `--no-cache`로 두었을 때
    "계측을 캐시 바깥으로" 변이에서 이 테스트가 살아남았고 죽인 것은 아래
    두 번 돌리는 테스트뿐이었다.
    """
    fake = _clean_echo()
    캡처: dict[str, object] = {}
    진짜 = cli_module.triage_with_tier1

    def spy(*args: object, **kwargs: object) -> object:
        캡처["provider"] = kwargs["provider"]
        return 진짜(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("cuesift.cli.triage_with_tier1", spy)

    result = _run_tier1(
        tmp_path, monkeypatch, *_TIER1_RUNS, provider=fake, cache_dir=tmp_path / "cache"
    )

    assert result.exit_code == 0, result.output
    # 이 단언들은 후보 0건에서도 성립한다(넘긴 객체는 후보 판정 전에 정해진다).
    # 그래도 도달성을 함께 못 박는 것은, 이 테스트가 조용히 Tier 1을 안 돌리는
    # 조합으로 흘러가면 위 `cache_dir`의 의미가 사라지기 때문이다.
    _assert_tier1_ran(fake)
    counting = 캡처["provider"]
    assert isinstance(counting, CountingProvider)
    # **`inner`까지 확인한다.** `CountingProvider(CachingProvider(raw))`는 위
    # `isinstance`를 통과하면서 캐시 히트를 세고, 게다가 Tier 1 안쪽에서 한 번
    # 더 감싸여 이중 캐시가 된다.
    assert counting.inner is fake


def test_같은_자막을_두_번_돌려도_tier1_토큰이_두_배가_되지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D7이 지키려는 것을 **의미로** 잰다 - 구조가 아니라 청구서를 본다.

    캐시가 붙은 둘째 실행에서 Tier 1은 `complete`를 한 번도 부르지 않으므로
    그 계층의 기여가 0이 된다. `CountingProvider`가 캐시 **바깥**에 있으면
    히트까지 세어 둘째 실행도 첫 실행과 같은 수를 낸다.

    번역 계층은 `CachingProvider`가 **저장된 usage를 그대로 내므로**
    (`store/provider.py`) 두 실행에서 같은 값이다 - 줄어드는 것은 Tier 1
    몫뿐이고, 그래서 이 차이가 곧 Tier 1의 기여다.
    """
    cache = tmp_path / "cache"

    first = _run_tier1(tmp_path / "a", monkeypatch, *_TIER1_RUNS, cache_dir=cache)
    second = _run_tier1(tmp_path / "b", monkeypatch, *_TIER1_RUNS, cache_dir=cache)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    처음 = _read_review(tmp_path / "a")["summary"]["cost"]
    두번째 = _read_review(tmp_path / "b")["summary"]["cost"]
    assert 두번째["calls"] < 처음["calls"], (
        "둘째 실행이 첫 실행과 같은 호출 수를 냈다 - 계측이 캐시 바깥에 있다"
    )
    assert 두번째["completion_tokens"] < 처음["completion_tokens"]


def test_샘플마다_다른_캐시_키를_쓴다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """설계 D7 + Task 1(`CacheRequest.attempt`)을 **함께** 지키는 게이트다.

    자가일관성은 같은 문장을 N번 다시 번역해 **흩어짐**을 재는 것이므로, N개
    샘플이 서로 다른 캐시 키를 써야 성립한다. `_provider_factory`가
    `attempt=i`로 그것을 보장하는데, cli가 **이미 캐시로 감싼** 프로바이더를
    넘기면 안쪽 캐시는 `attempt=0` 고정이라 **N개 샘플이 같은 엔트리를 맞는다**
    - 분산이 0이 되어 Tier 1 신호가 통째로 죽는다. 종료 코드도 파일도 정상이다.

    **Task 1이 막은 것과 같은 방에 다른 문으로 들어가는 결함이다**
    (`key(None) == key(0)`).

    **실측(번역 1회 포함, 캐시 켬 · 후보 1건 · 샘플 2회):**

    | | raw 호출 | 캐시 엔트리 | `cost.calls` |
    | --- | --- | --- | --- |
    | 정상 `CachingProvider(CountingProvider(raw))` | **3** | 3 | **3** |
    | 순서만 뒤집음 | **2** | 3 | **3** |

    **캐시 엔트리 개수로는 이 결함을 볼 수 없다 - 구조적으로 불가능하다.**
    뒤집힌 배치에서 안쪽 캐시는 `attempt=0` 고정인데 바깥 캐시의 첫 샘플도
    `attempt=0`이라 **둘이 같은 키를 쓴다** - 두 번의 쓰기가 같은 파일에
    떨어져 개수가 안 는다. 결함의 정체가 "키가 뭉친다"인데 그 뭉침을 키 수로
    재려 한 것이 잘못이었다(이전 판의 오류 - 표에 2 vs 1을 적었으나 실측은
    3 vs 3이다).

    그래서 각도 둘은 이렇다.

    | 각도 | 무엇을 재나 |
    | --- | --- |
    | `len(fake.calls)` | 샘플 N개가 **실제로 N번 나갔나** |
    | `cost.calls == len(fake.calls)` | 청구서가 **실제 나간 수와 같은가** |

    구조 단언(`counting.inner is fake`)과 **독립**이다 - 그 한 줄을 지워도
    이 테스트가 남는다. 리뷰 축2가 "그 줄이 유일한 게이트"라고 실측했다.
    """
    fake = _clean_echo()
    cache = tmp_path / "cache"

    result = _run_tier1(tmp_path, monkeypatch, *_TIER1_RUNS, provider=fake, cache_dir=cache)

    assert result.exit_code == 0, result.output
    _assert_tier1_ran(fake)
    # ① 번역 배치 1회 + Tier 1 후보 1건 x 샘플 2회.
    assert len(fake.calls) == 3, (
        f"샘플이 실제로 나간 횟수가 다르다: {len(fake.calls)}회. "
        "2회여야 할 Tier 1 재번역이 1회로 뭉쳤다면 샘플이 같은 캐시 키를 쓴 것이다"
    )
    # ② 청구서와 실제가 같은가. 계측기가 캐시 **바깥**에 있으면 히트까지 세어
    # `cost.calls`가 실제로 나간 호출보다 커진다 - 실측으로 뒤집으면 raw는 2회인데
    # 청구서는 3회를 적는다.
    #
    # **찬 캐시에서만 성립한다.** 데운 캐시에서는 번역 계층이 저장된 usage를
    # 그대로 replay하므로 raw를 안 부르고도 `cost.calls`가 는다(`COST_BASIS`의
    # `cached-included`). `tmp_path` 밑이라 매번 찬 상태로 시작한다.
    cost = _read_review(tmp_path)["summary"]["cost"]
    assert cost["calls"] == len(fake.calls), (
        f"청구서 {cost['calls']}회 vs 실제 {len(fake.calls)}회 - "
        "계측기가 캐시 바깥에서 히트를 세고 있다"
    )


def test_대상_언어마다_계측기가_분리된다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`CountingProvider`는 누적기다 - 언어끼리 공유하면 뒤 언어가 앞 언어의
    토큰까지 싣는다.

    **`--to en` 하나로는 도달 불가한 결함이다**(리뷰 축2 실측: 조립을 루프
    밖으로 옮기는 변이가 0건 사망). Recall 분모는 무사하고 `cost`만 오염되므로
    종료 코드도 파일 형태도 정상이다.

    두 언어의 실행이 대칭이라(같은 가짜 · 같은 자막 · 같은 응답) 분리돼 있으면
    두 파일의 `calls`가 **같아야** 한다. 공유되면 ja가 en의 Tier 1 몫을 한 번
    더 실어 커진다.
    """
    캡처: list[object] = []
    진짜 = cli_module.triage_with_tier1

    def spy(*args: object, **kwargs: object) -> object:
        캡처.append(kwargs["provider"])
        return 진짜(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("cuesift.cli.triage_with_tier1", spy)

    fake = _clean_echo()
    result = _run_tier1(tmp_path, monkeypatch, *_TIER1_RUNS, to="en,ja", provider=fake)

    assert result.exit_code == 0, result.output
    # 대상이 둘이라 번역만으로 배치 2회다 - 분모를 1로 두면 번역만 돌아도 통과한다.
    _assert_tier1_ran(fake, targets=2)
    en = _read_review(tmp_path, "ten_cues.en.review.json")["summary"]["cost"]
    ja = _read_review(tmp_path, "ten_cues.ja.review.json")["summary"]["cost"]
    assert en["includes"] == ["translation", "tier1"]
    assert ja["calls"] == en["calls"], (
        f"ja({ja['calls']})가 en({en['calls']})보다 크면 앞 언어의 Tier 1 토큰을 "
        "함께 싣고 있는 것이다"
    )
    assert ja["completion_tokens"] == en["completion_tokens"]
    # 수치가 갈라진 **원인**을 함께 못 박는다 - 위 단언만으로는 우연히 같아지는
    # 변경(예: 양쪽 다 0)이 통과한다.
    assert len(캡처) == 2
    assert 캡처[0] is not 캡처[1]


def test_tier1_temperature가_샘플러까지_간다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**옵션이 조용히 무시되는 것이 이 파일의 주제다.**

    `temperature`를 하드코딩으로 바꿔도, `effective_temperature` 조립을 항상
    기본값으로 바꿔도 **한 건도 죽지 않았다**(리뷰 축2 실측). 도움말 테스트는
    help 문자열만 읽으므로 종단 경로를 재지 못한다.

    0이면 샘플이 전부 같아져 신호가 죽는다는 것이 이 옵션의 존재 이유이므로,
    실제로 샘플러까지 닿는지가 곧 그 방어의 유효성이다.

    **번역 호출의 온도와 다른 값을 고른다.** 같은 값을 고르면 Tier 1이 번역
    기본값을 그대로 쓰는 배선도 통과한다.
    """
    fake = _clean_echo()
    온도 = 0.55

    result = _run_tier1(
        tmp_path, monkeypatch, *_TIER1_RUNS, "--tier1-temperature", str(온도), provider=fake
    )

    assert result.exit_code == 0, result.output
    _assert_tier1_ran(fake)
    온도들 = [t for t, _ in fake.kwargs]
    # 후보 1건 x 샘플 2회. 개수까지 보는 이유는 "한 번은 닿았다"가 "N번 다
    # 닿았다"를 뜻하지 않기 때문이다.
    assert 온도들.count(온도) == 2, f"실제 온도 목록: {온도들}"
    assert 온도 not in 온도들[:1], "번역 호출이 이미 이 온도를 쓰면 아무것도 재지 못한다"


def test_번역이_전량_실패하면_tier1이_범위에서_빠진다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`resolve_cost_scope`의 계약 - `None`은 "이 실행에서 안 돌았다"다.

    전량 실패는 `triage_with_tier1`보다 **앞**에서 조기 반환하므로 Tier 1은 한
    번도 안 돈다. 그때 `tier1.counting.usage`(= `TokenUsage(0, 0, 0)`)를 넘기면
    `includes`가 "Tier 1을 셌다"고 말한다 - `calls == 0`이라 `unreported`에도
    안 실려 **화면 어디에도 신호가 없는 거짓말**이 된다.
    """
    전량실패 = EchoProvider(transform=lambda _: "   ")

    result = _run_tier1(tmp_path, monkeypatch, *_TIER1_RUNS, provider=전량실패)

    # **exit 1이다** - 전량 실패는 규격 위반으로 보고된다. 코드를 단언하지 않으면
    # 실행이 엉뚱한 이유로 죽어도 아래 파일 읽기가 먼저 터져 원인이 가려진다.
    assert result.exit_code == 1, result.output
    summary = _read_review(tmp_path)["summary"]
    assert summary["excluded_failures"] == 10, "전량 실패가 아니면 아무것도 재지 못한다"
    assert summary["triaged_segments"] == 0
    assert summary["cost"]["includes"] == ["translation"]


# --- Tier 1 프로바이더 예외 그물 (설계 D14 · 스펙 §3.4) ---------------------


class _FailsAfterTranslation:
    """번역은 성공시키고 **Tier 1 호출에서만** 던지는 가짜.

    **번역에서 던지면 이 태스크를 검증하지 못한다** - `_translate_one`의 기존
    `except FatalProviderError`가 잡아 똑같은 69를 내므로, 새 그물을 한 번도
    밟지 않은 채 종료 코드 단언이 초록이 된다. 층을 가르는 것은 종료 코드가
    아니라 화면 문구(`Tier 1`)와 `_assert_tier1_ran`이다.

    호출 횟수로 층을 가른다. 이 파일의 픽스처는 10큐이고 `DEFAULT_BATCH_SIZE`가
    10이라 번역은 **배치 1회**로 끝나므로, 그 다음 호출부터는 Tier 1뿐이다.
    픽스처가 배치 하나를 넘게 커지면 번역 도중에 던지게 되는데, 그때는 번역
    산출물 단언과 문구 단언이 함께 거짓 실패로 그 사실을 알린다.

    시그니처는 `Provider` 프로토콜과 글자 그대로 같아야 한다
    (`tests/fakes/provider.py` 머리말) - 기본값 하나만 붙여도 이탈이다.
    """

    name = "echo"
    cache_identity = "echo|fake|v1"

    def __init__(self, error: ProviderError, *, healthy_calls: int = 1) -> None:
        self._inner = _clean_echo()
        self._error = error
        self._healthy_calls = healthy_calls
        self.calls: list[list[ChatMessage]] = []
        self.kwargs: list[tuple[float, int | None]] = []

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> Completion:
        # **던지기 전에 기록한다.** `_assert_tier1_ran`은 이 목록으로 "Tier 1이
        # 프로바이더까지 닿았나"를 판정하는데, 예외로 끝난 호출도 닿은 것이다.
        # 기록을 예외 뒤로 미루면 실패 경로에서 그 판정이 언제나 거짓이 되어
        # "그물이 아니라 다른 이유로 69가 났다"를 구별할 수단이 사라진다.
        self.calls.append(list(messages))
        self.kwargs.append((temperature, max_tokens))
        if len(self.calls) > self._healthy_calls:
            raise self._error
        return self._inner.complete(messages, temperature=temperature, max_tokens=max_tokens)


def _assert_died_in_tier1(result: Result, fake: _FailsAfterTranslation, tmp_path: Path) -> None:
    """69가 **Tier 1 층에서** 났는지 확인한다.

    **종료 코드만 단언하면 안 된다.** 번역 경로도 같은 둘을 69로 내므로,
    가짜가 번역에서 먼저 죽어도 코드는 69다 - 이 스위트에서 종료 코드만 보는
    단언이 엉뚱한 층에서 만족된 사례가 세 번 있었다. 셋을 함께 본다.

    1. 번역 산출물이 남았다 = 번역은 끝까지 갔다
    2. 프로바이더가 번역 몫보다 더 불렸다 = Tier 1이 실제로 LLM에 닿았다
    3. 화면 문구에 `Tier 1`이 있다 = 번역 경로의 그물이 아니다
    """
    assert result.exit_code == 69, result.output
    assert (tmp_path / "subs" / "ten_cues.en.srt").exists(), "번역 단계에서 죽었다"
    _assert_tier1_ran(fake)
    assert "[en] Tier 1 프로바이더가 요청을 거부했다" in result.output


def test_tier1의_프로바이더_실패는_69다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**exit 1이 되면 설정 실수가 '규격 위반 발견'으로 오보된다**(설계 D14).

    번역 경로(`cli.py`의 `_translate_one` 호출부)는 이미 같은 둘을 69로 낸다 -
    여기가 그 대칭이다. 그물이 없으면 `SelfConsistency`가 다시 던지는
    `FatalProviderError`가 미처리 traceback이 되어 exit 1이 되는데, 이 파일
    머리말의 표에서 1은 "규격 위반 발견"이라 사용자는 멀쩡한 자막을 고치려 든다.
    """
    fake = _FailsAfterTranslation(FatalProviderError("HTTP 401: invalid api key"))

    result = _run_tier1(tmp_path, monkeypatch, *_TIER1_RUNS, provider=fake)

    _assert_died_in_tier1(result, fake, tmp_path)
    # **원인이 화면에 있어야 한다.** 없으면 사용자는 무엇을 고쳐야 할지 모른 채
    # 69만 본다 - 자격증명 오류와 죽은 엔드포인트가 같은 화면이 된다.
    assert "401" in result.output


def test_tier1의_맨_ProviderError도_69다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """계약을 어기는 서드파티 구현이 파이프라인을 죽이는 것보다 낫다(NFR-5 · §12 Q3).

    번역 경로의 '마지막 그물'과 대칭이다. `openai_compat.py`는 자손 둘 중
    하나만 던지므로 오늘은 도달 불가지만, NFR-5가 "코드 수정 없이 프로바이더
    추가"를 요구하는 한 계약 위반은 traceback이 아니라 69여야 한다.
    """
    fake = _FailsAfterTranslation(ProviderError("계약을 어긴 서드파티 구현"))

    result = _run_tier1(tmp_path, monkeypatch, *_TIER1_RUNS, provider=fake)

    _assert_died_in_tier1(result, fake, tmp_path)
    assert "계약을 어긴 서드파티 구현" in result.output


# ---------------------------------------------------------------------------
# `--dry-run`의 Tier 1 호출 상한 (설계 D10)
#
# **추정이 아니라 상한이다.** `floor(n x max_ratio) x samples`는 실제 후보가
# 회색지대 크기에 눌려 이보다 적을 수는 있어도 많을 수는 없다 - 요구사항정의서
# §11 R8이 금지하는 것은 출처 없는 **추정**이고, 상한은 산식에서 나온다.
# ---------------------------------------------------------------------------


def _timecode(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d},000"


def _srt_with_cues(path: Path, count: int) -> Path:
    """`count`개 큐를 가진 SRT를 쓴다.

    **체크인된 픽스처로는 상한 산식을 시험할 수 없다.** 가장 큰 `large.srt`가
    26큐라 기본값에서 `floor(26 x 0.05) x 3 = 3`이고, 그 `3`은 같은 줄의
    `샘플 3`과 글자가 겹쳐 "곱을 빠뜨린" 변이가 살아남는다(실측). 큐 수를
    테스트가 정하면 내림·곱셈·기본값 셋을 각각 다른 수로 갈라 볼 수 있다.

    자막 본문에 숫자를 넣지 않는 것은 화면 단언 때문이다 - dry-run은 본문을
    찍지 않지만, 넣으면 나중에 본문을 찍도록 바뀌는 순간 숫자 단언이 우연히
    만족될 수 있다.
    """
    cues = [
        f"{i + 1}\n{_timecode(i * 4)} --> {_timecode(i * 4 + 3)}\n안녕하세요 여러분\n"
        for i in range(count)
    ]
    path.write_text("\n".join(cues), encoding="utf-8")
    return path


def _dry_run_args(input_path: Path, tmp_path: Path, *extra: str, to: str = "en") -> list[str]:
    """`_full_args`와 같은 골격이되 입력이 `tmp_path`에 만든 파일이다.

    `--review-budget`을 빼면 안 된다 - `--tier1`이 트리아지 정책을 요구하므로
    조합 검증이 exit 2로 끊어 dry-run 분기에 **닿지도 못한다.**
    """
    return [
        "translate",
        str(input_path),
        "--to",
        to,
        "--out",
        str(tmp_path / "subs"),
        "--base-url",
        "http://h/v1",
        "--model",
        "m1",
        "--review-budget",
        "10%",
        "--no-cache",
        "--dry-run",
        *extra,
    ]


def _bound_lines(output: str) -> list[str]:
    """상한 줄만 뽑는다.

    **`"15" in output`으로 세지 않는 이유**: 같은 화면에 세그먼트 수·배치
    수·호출 필요 수·프롬프트 문자 수가 함께 있고 문자 수는 천 단위 쉼표가
    붙은 네 자리 이상이라, 상한과 무관하게 부분 문자열이 만족될 수 있다
    (실측: 100큐 실행의 `프롬프트 문자 system 1,151`이 `"15"`를 품는다).
    줄 단위로 뽑아 두면 단언이 상한 줄 하나만 본다.
    """
    return [line for line in output.splitlines() if _TIER1_BOUND_PREFIX in line]


def test_dry_run이_tier1_상한을_말한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """dry-run이 Tier 1을 침묵하면 "켠 줄 알았는데 안 돌았다"를 실행 전에 알
    수단이 없다(설계 D10).

    `--dry-run`의 존재 이유는 비용 추정이고 `_TIER1_COST_LIMIT`의 존재 이유는
    비용 통제인데, 상한 줄이 없으면 둘이 서로 말을 하지 않는다 - 가장 비싼
    계층이 빠진 호출 수를 보고 사용자가 실행을 결정한다.
    """
    src = _srt_with_cues(tmp_path / "hundred.srt", 100)
    _patch_provider(monkeypatch, _clean_echo())

    result = runner.invoke(app, _dry_run_args(src, tmp_path, "--tier1"))

    assert result.exit_code == 0, result.output
    # floor(100 x 0.05) x 3 = 15. **줄 전체를 못 박는다** - 화면 문구가
    # "예상"으로 바뀌면 상한이 추정으로 오해되므로(§11 R8) 그 변경은 조용히
    # 지나가면 안 된다.
    assert _bound_lines(result.output) == [
        f"  {_TIER1_BOUND_PREFIX}15회 (후보 상한 비율 0.05 · 샘플 3)"
    ]


def test_상한은_올림이_아니라_내림이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`select_tier1_candidates`가 내림을 쓴다 - 여기서 올림하면 dry-run이
    실행보다 **큰** 수를 말해 상한이 상한이 아니게 된다.

    **97큐인 이유**: `97 x 0.05 = 4.85`라 내림 4 · 올림 5 · 반올림 5로 셋이
    전부 갈린다. 위 테스트의 100큐는 곱이 정확히 `5.0`이라 내림과 올림이 같은
    값을 내므로 이 변이를 못 잡는다(실측: `floor`를 `ceil`로 바꿔도 사망 0건).
    """
    src = _srt_with_cues(tmp_path / "ninety_seven.srt", 97)
    _patch_provider(monkeypatch, _clean_echo())

    result = runner.invoke(app, _dry_run_args(src, tmp_path, "--tier1"))

    assert result.exit_code == 0, result.output
    # floor(97 x 0.05) x 3 = 12. 올림·반올림이면 15다.
    assert _bound_lines(result.output) == [
        f"  {_TIER1_BOUND_PREFIX}12회 (후보 상한 비율 0.05 · 샘플 3)"
    ]


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        # floor(100 x 0.02) x 3 = 6. `max_ratio`를 상수로 굳히면 15가 된다.
        (
            ("--tier1-max-ratio", "0.02"),
            f"  {_TIER1_BOUND_PREFIX}6회 (후보 상한 비율 0.02 · 샘플 3)",
        ),
        # floor(100 x 0.05) x 5 = 25. `samples`를 안 곱하면 5가 된다.
        (("--tier1-samples", "5"), f"  {_TIER1_BOUND_PREFIX}25회 (후보 상한 비율 0.05 · 샘플 5)"),
    ],
)
def test_상한이_명시된_값을_그대로_쓴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: tuple[str, ...], expected: str
) -> None:
    """**기본값만 시험하면 두 인자가 상수로 굳어도 스위트가 초록이다.**

    Tier 1은 후보 하나마다 `samples`회를 **개별 호출**로 낸다(§12 Q3 - `n>1`
    단일 호출은 백엔드에 따라 조용히 사라진다). 곱을 빠뜨리면 화면은 후보 수를
    호출 수라고 말한다.
    """
    src = _srt_with_cues(tmp_path / "hundred.srt", 100)
    _patch_provider(monkeypatch, _clean_echo())

    result = runner.invoke(app, _dry_run_args(src, tmp_path, "--tier1", *extra))

    assert result.exit_code == 0, result.output
    assert _bound_lines(result.output) == [expected]


def test_dry_run은_tier1_호출을_내지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """상한을 계산할 뿐 LLM을 부르지 않는다.

    **`_assert_tier1_ran`을 여기 쓰면 안 된다.** 그 헬퍼는 프로바이더가
    실제로 불렸음을 보는 긍정 단언이라 dry-run에서는 정반대다. 대신 도달은
    상한 줄의 존재로 확인한다 - 줄을 안 보면 `calls == []`는 "dry-run이
    아무것도 안 했다"거나 "인자가 틀려 조기 종료했다"와 구별이 안 된다.
    """
    src = _srt_with_cues(tmp_path / "hundred.srt", 100)
    fake = _clean_echo()
    _patch_provider(monkeypatch, fake)

    result = runner.invoke(app, _dry_run_args(src, tmp_path, "--tier1"))

    assert result.exit_code == 0, result.output
    assert len(_bound_lines(result.output)) == 1, result.output
    assert fake.calls == []


def test_tier1_없이는_상한_줄이_없다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--tier1`을 안 켠 실행이 Tier 1 비용을 예고하면 화면이 거짓말을 한다.

    **부재 단언이라 짝이 필요하다.** 문구가 바뀌면 이 단언은 조용히 항상 참이
    되는데, 같은 상수를 쓰는 위 존재 단언들이 그때 먼저 깨진다 - 이 스위트에서
    `"Tier 1:" not in output`이 정확히 그 방식으로 무력해진 전례가 있다.
    """
    src = _srt_with_cues(tmp_path / "hundred.srt", 100)
    _patch_provider(monkeypatch, _clean_echo())

    result = runner.invoke(app, _dry_run_args(src, tmp_path))

    assert result.exit_code == 0, result.output
    assert _bound_lines(result.output) == []


def test_상한_줄은_대상_언어마다_난다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """언어 루프 **밖**에 놓으면 `--to en,ja`가 한 줄만 내면서 실제 호출은 두
    배다 - Tier 1 계측기는 대상 언어마다 분리돼 있다(D7)."""
    src = _srt_with_cues(tmp_path / "hundred.srt", 100)
    _patch_provider(monkeypatch, _clean_echo())

    result = runner.invoke(app, _dry_run_args(src, tmp_path, "--tier1", to="en,ja"))

    assert result.exit_code == 0, result.output
    assert len(_bound_lines(result.output)) == 2, result.output


def test_tier1_조합_오류는_dry_run에서도_난다(input_srt: Path) -> None:
    """조합 오류는 실행 전에 알아야 한다.

    dry-run이야말로 "돌리기 전에 확인하는" 명령인데 거기서 조합 검증이 빠지면
    사용자는 exit 0을 보고 본 실행에 들어가서야 exit 2를 만난다.

    **메시지까지 본다.** typer의 파일 존재 검사가 조합 검증과 **같은 exit 2**를
    내므로 종료 코드만 보는 단언은 검증 코드에 닿지 못한 채 초록이 된다(이
    파일 머리말).
    """
    result = runner.invoke(
        app, _args(input_srt, "--tier1", "--review-threshold", "0.7", "--dry-run")
    )

    assert result.exit_code == 2
    assert "--review-threshold" in result.output
