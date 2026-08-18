"""`cuesift translate`의 트리아지 배선 검증 (FR-6.3 · 설계 §5·§7).

**네트워크를 타지 않는다.** `_build_provider`를 monkeypatch해 가짜를 꽂는다 -
`test_cli_translate.py`와 같은 방식이다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.fakes.provider import EchoProvider, ScriptedProvider
from typer.testing import CliRunner

from cuesift.cli import _parse_review_budget, app
from cuesift.spec import SpecProfile, available_builtins, load_builtin

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
    # **`output`이 아니라 `stderr`를 본다.** `output`은 stdout+stderr 합본이라
    # `_echo(..., err=True)`의 `err=True`를 지워도 통과한다 - 이 파일의 다른
    # 테스트가 전부 그렇다. 한 곳이라도 스트림을 갈라 봐야 "오류는 stderr로
    # 낸다"가 관례가 아니라 게이트가 된다.
    assert result.stdout == "", "사용법 오류가 stdout으로 샜다"
    # 두 옵션 이름이 모두 나와야 사용자가 무엇을 지울지 안다.
    assert "--review-budget" in result.stderr
    assert "--review-threshold" in result.stderr


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
    """
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "minimal.srt", "--review-threshold", raw))

    assert result.exit_code == 2, result.output
    # `cli.py`의 `min`/`max` 주석이 "오류 메시지가 옵션 이름을 말한다"를
    # 정당화의 근거로 든다. 그 약속을 게이트로 만든다.
    assert "--review-threshold" in result.output
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


def _blank_at(indices: set[int], count: int) -> ScriptedProvider:
    """지정한 인덱스만 **공백 번역**으로 답하는 가짜.

    공백 번역은 `engine.py:419`가 `reason="empty_translation"`으로 실패
    처리한다 - 응답 형식은 올바르므로 개별 폴백이 개입하지 않아 호출이
    배치 1회로 끝난다. `EchoProvider(drop_last=True)`는 이 목적에 쓸 수
    없다: 배치가 개수 불일치로 실패하면 폴백이 개별 호출로 재시도하고
    거기서는 `len(items) > 1`이 거짓이라 **전부 성공한다**.
    """
    items = [{"id": i, "text": "   " if i in indices else f"EN{i}"} for i in range(count)]
    return ScriptedProvider([json.dumps({"translations": items}, ensure_ascii=False)])


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
    _patch_provider(monkeypatch, _blank_at({2, 5, 9}, 10))

    result = runner.invoke(app, _args(tmp_path, "ten_cues.srt", "--review-budget", "10%"))

    assert result.exit_code == 1, result.output  # 번역 실패가 있으면 1이다 (FR-2.6)
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
    _patch_provider(monkeypatch, _blank_at(set(range(10)), 10))

    result = runner.invoke(app, _args(tmp_path, "ten_cues.srt", "--review-budget", "10%"))

    assert result.exit_code == 1, result.output
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


def test_임계값_방식이_동작한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-6.3 ② — `select_by_threshold`를 부른다."""
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path, "ten_cues.srt", "--review-threshold", "0.7"))

    assert result.exit_code == 0, result.output
    assert "임계값 0.7" in result.output
    assert "대상 세그먼트 10개" in result.output


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
