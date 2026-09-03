"""설정 파일의 CLI 배선 (FR-8.4 · 설계 §4).

**`result.output`은 stdout과 stderr가 섞인 스트림이다**(typer 0.27의
`StreamMixer` 실측). 산출물과 진단을 나눠 봐야 하는 곳에서는 `.stdout`·
`.stderr`를 따로 쓴다 - `test_cli_check.py`가 같은 관례를 갖고 있다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fakes.provider import EchoProvider
from typer.testing import CliRunner

from conftest import normalize_rich_message
from cuesift import cli
from cuesift.cli import app

runner = CliRunner()

_VIOLATIONS = Path(__file__).parent / "fixtures" / "ingest" / "check_violations.ass"


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cuesift.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _violation_lines(output: str) -> int:
    """위반 목록 줄 수. rich 장식에 흔들리지 않게 접두로 센다."""
    return sum(1 for line in output.splitlines() if line.lstrip().startswith("#"))


def _check(cfg: Path, *after: str):
    """`--config`는 그룹 옵션이라 `check` **앞**에, 나머지는 뒤에 온다."""
    return runner.invoke(app, ["--config", str(cfg), "check", str(_VIOLATIONS), *after])


def _srt(tmp_path: Path) -> Path:
    path = tmp_path / "a.srt"
    path.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n", encoding="utf-8")
    return path


def _translate(cfg: Path, target: Path, *after: str):
    """`--dry-run`으로 도는 `translate`. LLM 접속은 CLI로 채워 exit 2 원인을 하나로 줄인다."""
    return runner.invoke(
        app,
        [
            "--config",
            str(cfg),
            "translate",
            str(target),
            "--to",
            "en",
            "--dry-run",
            "--base-url",
            "http://x/v1",
            "--model",
            "m",
            *after,
        ],
    )


# 우선순위 진리표 (설계 D3 · §8).
#
# **프로바이더가 필요 없는 `check`로 잰다.** `--limit`은 출력 줄 수를,
# `--fail-on`은 종료 코드를 바꾸므로 관측이 확실하다. 실측 기준값
# (`check_violations.ass` · `--spec ko`): 기본 exit 1 · 위반줄 4,
# `--limit 1`이면 위반줄 1, `--fail-on none`이면 exit 0.


def test_진리표_설정도_CLI도_없으면_기본값이다(tmp_path: Path) -> None:
    result = _check(_config(tmp_path, "spec:\n  profile: ko\n"))
    assert _violation_lines(result.stdout) == 4
    assert result.exit_code == 1


def test_진리표_설정만_있으면_설정이_이긴다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "spec:\n  profile: ko\n  limit: 2\n  fail_on: none\n")
    result = _check(cfg)
    assert _violation_lines(result.stdout) == 2
    assert result.exit_code == 0


def test_진리표_CLI만_있으면_CLI가_이긴다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "spec:\n  profile: ko\n")
    result = _check(cfg, "--limit", "1", "--fail-on", "none")
    assert _violation_lines(result.stdout) == 1
    assert result.exit_code == 0


def test_진리표_둘_다_있으면_CLI가_이긴다(tmp_path: Path) -> None:
    # **이 한 건이 FR-8.4 본문의 후반절 전체다.**
    cfg = _config(tmp_path, "spec:\n  profile: ko\n  limit: 2\n  fail_on: hard\n")
    result = _check(cfg, "--limit", "1", "--fail-on", "none")
    assert _violation_lines(result.stdout) == 1
    assert result.exit_code == 0


# 상호배타 쌍과 우선순위 (FR-8.4 후반절 · 설계 D3).
#
# **값의 존재만 보는 상호배타 검사는 설정 파일을 이길 방법을 없앤다.**
# `cuesift.yaml`에 `triage.review_threshold`를 적어 두면 `--review-budget`을
# 친 사람이 exit 2를 받는데, 그 사람은 명령줄에 `--review-threshold`를 쓴 적이
# 없다. FR-8.4 본문의 "CLI 인자가 설정 파일보다 우선한다"가 이 두 쌍에서만
# 통째로 뒤집힌다.


def _spy_dry_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """dry-run 리포트가 실제로 받은 인자를 붙잡는다 (FR-8.4 · 설계 D3).

    **`exit_code == 0`만 보는 상호배타 회귀는 양보가 뒤집힌 것을 못 잡는다.**
    해소된 `cache_dir`은 어디에도 출력되지 않으므로 두 갈래가 같은 화면과 같은
    종료 코드를 낸다 - 실측으로 호출부를 `in`에서 `==`로 되돌려도 이 파일의
    회귀가 전부 통과했다. 그래서 화면이 아니라 인자를 본다.
    """
    seen: dict[str, object] = {}
    original = cli._dry_run_report

    def spy(**kwargs: object):
        seen.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(cli, "_dry_run_report", spy)
    return seen


def test_CLI_예산이_설정의_임계값을_이긴다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "triage:\n  review_threshold: 0.5\n")
    result = _translate(cfg, _srt(tmp_path), "--review-budget", "10%")
    assert result.exit_code == 0, result.output


def test_CLI_임계값이_설정의_예산을_이긴다(tmp_path: Path) -> None:
    # 반대 방향도 본다. 한쪽만 고치면 다른 쪽이 그대로 남는다.
    #
    # **설정의 예산을 범위 밖 값으로 둔다.** `"10%"`로 두면 양보가 반대로
    # 뒤집혀 예산이 살아남아도 정상 값이라 exit 0이 그대로 나온다 - 관측이
    # 없는 단언이다. `"500%"`는 `_parse_review_budget`이 거부하므로 예산이
    # 살아남는 순간 exit 2가 되어 뒤집힘이 종료 코드로 드러난다.
    cfg = _config(tmp_path, 'triage:\n  review_budget: "500%"\n')
    result = _translate(cfg, _srt(tmp_path), "--review-threshold", "0.5")
    assert result.exit_code == 0, result.output


def test_CLI_캐시_끄기가_설정의_캐시_경로를_이긴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _spy_dry_run(monkeypatch)
    cfg = _config(tmp_path, "cache:\n  dir: .c\n")
    result = _translate(cfg, _srt(tmp_path), "--no-cache")
    assert result.exit_code == 0, result.output
    # `--no-cache`가 이겼으므로 설정의 `.c`가 아니라 `None`이 실려야 한다.
    assert seen["cache_dir"] is None


def test_CLI_캐시_경로가_설정의_캐시_끄기를_이긴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _spy_dry_run(monkeypatch)
    cfg = _config(tmp_path, "cache:\n  enabled: false\n")
    result = _translate(cfg, _srt(tmp_path), "--cache-dir", str(tmp_path / "c"))
    assert result.exit_code == 0, result.output
    # 명령줄의 경로가 이겼으므로 캐시가 켜진 채 그 경로를 봐야 한다.
    # 양보가 뒤집히면 `no_cache`가 살아남아 여기가 `None`이 된다.
    assert seen["cache_dir"] == tmp_path / "c"


def test_설정끼리의_상호배타는_여전히_오류다(tmp_path: Path) -> None:
    # **양보를 넓히지 않는다.** 둘 다 설정에서 왔으면 설정 파일 자체가
    # 모순이고, 어느 쪽을 버려도 사용자가 적은 정책 하나가 조용히 사라진다.
    cfg = _config(tmp_path, 'triage:\n  review_budget: "10%"\n  review_threshold: 0.5\n')
    result = _translate(cfg, _srt(tmp_path))
    assert result.exit_code == 2
    assert normalize_rich_message("설정 파일") in normalize_rich_message(result.stderr)


def test_설정끼리의_캐시_상호배타도_여전히_오류다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "cache:\n  dir: .c\n  enabled: false\n")
    result = _translate(cfg, _srt(tmp_path))
    assert result.exit_code == 2
    assert normalize_rich_message("설정 파일") in normalize_rich_message(result.stderr)
    # 2자 쌍은 여전히 "둘 다"다. 개수 문구를 `names`로 세면서 이쪽이
    # 깨지면 3자 일반화가 기존 쌍의 메시지를 망친 것이다.
    assert normalize_rich_message("둘 다") in normalize_rich_message(result.stderr)


def test_설정에_두_정책만_있으면_문구가_둘_다다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, 'triage:\n  review_budget: "10%"\n  review_threshold: 0.5\n')
    result = _translate(cfg, _srt(tmp_path))
    assert result.exit_code == 2
    stderr = normalize_rich_message(result.stderr)
    assert normalize_rich_message("둘 다") in stderr
    # **주지 않은 세 번째를 말하지 않는다**(설계 D7).
    assert "--review-top-k" not in stderr


def test_명령줄끼리의_상호배타는_여전히_오류다(tmp_path: Path) -> None:
    # 양보가 출처를 보지 않고 늘 한쪽을 버리면 이 두 건이 조용히 통과한다.
    cfg = _config(tmp_path, "source_lang: ko\n")
    budget = _translate(cfg, _srt(tmp_path), "--review-budget", "10%", "--review-threshold", "0.5")
    assert budget.exit_code == 2
    assert normalize_rich_message("설정 파일") not in normalize_rich_message(budget.stderr)
    cache = _translate(cfg, _srt(tmp_path), "--no-cache", "--cache-dir", str(tmp_path / "c"))
    assert cache.exit_code == 2


def test_CLI_자막이_설정의_media를_이긴다(tmp_path: Path) -> None:
    """`input`/`media` 쌍에도 같은 양보가 걸린다 (FR-8.4 후반절).

    **이 쌍만 회귀 테스트가 없었다.** 위치 인자는 `default_map`에 실리지
    않으므로 `_from_config("input")`은 늘 거짓이고, 따라서 설정에서 온
    `media`가 양보 대상이 된다. 양보가 죽으면 설정 파일을 쓰는 사용자가
    명령줄로 준 자막을 잃는다 - FR-8.3의 리뷰가 HIGH로 잡았던 실패다.
    """
    cfg = _config(tmp_path, "input:\n  media: 없는영상.mp4\n")
    result = _translate(cfg, _srt(tmp_path))
    # 자막이 이겼으므로 없는 영상 파일에 닿지 않고 정상 종료한다.
    assert result.exit_code == 0, result.output


def test_명령줄_자막과_media는_여전히_오류다(tmp_path: Path) -> None:
    # 둘 다 명령줄이면 원래의 사용법 오류다. 양보를 넓히면 이것이 통과한다.
    cfg = _config(tmp_path, "source_lang: ko\n")
    media = tmp_path / "v.mp4"
    media.write_bytes(b"\x00")
    result = _translate(cfg, _srt(tmp_path), "--media", str(media))
    assert result.exit_code == 2
    assert normalize_rich_message("함께 줄 수 없다") in normalize_rich_message(result.stderr)


def test_설정이_필수_옵션을_만족시킨다(tmp_path: Path) -> None:
    # `--spec`은 필수다. 설정이 그것을 채우지 못하면 설정 파일로 '모든 옵션'을
    # 지정한다는 FR-8.4 본문이 성립하지 않는다(설계 P3).
    #
    # **부정 단언이라 rich 정규화가 필수다.** 강제 개행이 `Missing option`
    # 사이에 떨어지면 배선이 없어도 이 단언이 통과한다.
    result = _check(_config(tmp_path, "spec:\n  profile: ko\n"))
    assert normalize_rich_message("Missing option") not in normalize_rich_message(result.output)
    assert result.exit_code == 1


def test_설정에_넣은_input은_무시된다(tmp_path: Path) -> None:
    """설계 D13 - 위치인자는 설정 대상이 아니다.

    `input`이 매핑표에 있으면 다른 파일을 검수하고도 통과한다.

    **FR-8.3이 `input.media`를 더해 진단 문구가 바뀌었다.** `input`은 이제
    중간 노드라 "모르는 키"가 아니라 "값이 매핑이 아니다"로 거부된다 -
    거부된다는 사실과 종료 코드 2는 그대로이고 안내가 더 정확해진 것이다.
    **`input.media`가 `translate.media` 옵션으로 가는 것과 위치 인자
    `input`은 다른 것이며, 후자는 여전히 설정으로 채울 수 없다.**
    """
    cfg = _config(tmp_path, "spec:\n  profile: ko\ninput: 아무거나.srt\n")
    result = _check(cfg)
    assert result.exit_code == 2
    stderr = normalize_rich_message(result.stderr)
    assert normalize_rich_message("'input'의 값이 매핑이 아니다") in stderr
    # 가능한 키가 `input.media` 하나뿐인 것이 "위치 인자는 없다"의 표현이다.
    assert normalize_rich_message("input.media") in stderr


def test_설정을_읽으면_출처가_stderr에_나간다(tmp_path: Path) -> None:
    # 설계 D7 - click의 오류가 `Invalid value for '--review-format'`이라
    # 그 옵션을 친 적 없는 사용자가 명령줄을 노려보게 된다.
    cfg = _config(tmp_path, "source_lang: ko\n")
    result = runner.invoke(app, ["--config", str(cfg), "check", "--help"])
    assert str(cfg) in result.stderr
    assert str(cfg) not in result.stdout, "출처는 산출물이 아니다"


def test_config_파일이_없으면_종료_코드_2다(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--config", str(tmp_path / "없다.yaml"), "check", "--help"])
    assert result.exit_code == 2


def test_모르는_키는_종료_코드_2다(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "triage:\n  review_budgt: 10%\n")
    result = runner.invoke(app, ["--config", str(cfg), "check", "--help"])
    assert result.exit_code == 2
    assert normalize_rich_message("모르는 키") in normalize_rich_message(result.stderr)


def test_틀린_값은_click이_종료_코드_2로_낸다(tmp_path: Path) -> None:
    """설계 D5·P4 - 로더가 아니라 click이 `default_map` 값을 변환·검증한다.

    **종료 코드만 보면 이 테스트는 아무것도 재지 않는다.** 초판은
    `--base-url`·`--model`을 주지 않아 **같은 명령이 그 결핍만으로도 exit 2**
    였다 - `schema.py`의 `review.format` 행을 통째로 지워도 초록이었다(변이
    실측). D5·P4를 재는 유일한 게이트가 그런 상태였다.

    그래서 다른 exit 2 원인을 지우고(`--dry-run` + 접속 정보) click이 낸
    **값 오류 메시지**까지 함께 단언한다.
    """
    cfg = _config(tmp_path, "review:\n  format: xml\n")
    result = _translate(cfg, _srt(tmp_path))
    assert result.exit_code == 2
    message = normalize_rich_message(result.output)
    assert normalize_rich_message("Invalid value for '--review-format'") in message
    assert normalize_rich_message("xml") in message


# 자동 탐색 3건 (설계 D2).
#
# **`설정_자동_탐색` fixture를 요청해야 진짜 탐색이 돈다.** `conftest.py`의
# autouse가 나머지 전체에서 탐색을 끄기 때문이다(리포 루트의 `cuesift.yaml`
# 한 줄이 로컬에서만 81건을 깨뜨린 실측). 그 차단이 이 3건까지 덮으면
# D2를 재는 게이트가 통째로 사라진다.


def test_현재_디렉터리의_cuesift_yaml을_자동으로_읽는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, 설정_자동_탐색: None
) -> None:
    _config(tmp_path, "source_lang: ja\n")
    monkeypatch.chdir(tmp_path)
    # **전제를 먼저 단언한다.** 이 테스트는 프로세스 전역 cwd와 방금 쓴 파일의
    # `is_file()`에 동시에 걸려 있어 산발 실패가 가능하다(7회 중 1회 관측).
    # 이 줄이 없으면 그때 실패 메시지가 "설정을 안 읽었다"로 나와 자동 탐색 회귀와
    # 구분되지 않는다 - 무엇이 없었는지를 실패가 직접 말하게 한다.
    assert Path("cuesift.yaml").is_file()
    result = runner.invoke(app, ["check", "--help"])
    assert "cuesift.yaml" in result.stderr


def test_상위_디렉터리는_읽지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, 설정_자동_탐색: None
) -> None:
    # 설계 D2 - 사용자가 존재를 모르는 파일이 검수 기준을 바꾸는 것을 막는다.
    # **반대 방향 회귀 테스트다.** 위 테스트만 두면 상위 탐색을 넣어도 초록이다.
    _config(tmp_path, "source_lang: ja\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    result = runner.invoke(app, ["check", "--help"])
    assert "cuesift.yaml" not in result.stderr
    assert result.exit_code == 0


def test_설정이_없으면_조용히_넘어간다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, 설정_자동_탐색: None
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["check", str(_VIOLATIONS), "--spec", "ko", "--fail-on", "none"])
    assert result.exit_code == 0
    assert result.stderr.strip() == ""


def test_미구현_경고가_사라졌다(tmp_path: Path) -> None:
    # 설계 §4.3 함정 ③ - 남겨 두면 이번에는 반대 방향의 거짓말이 된다.
    cfg = _config(tmp_path, "source_lang: ko\n")
    result = runner.invoke(app, ["--config", str(cfg), "check", "--help"])
    assert normalize_rich_message("아직 구현되지") not in normalize_rich_message(result.output)


def test_help에서_미구현_문구가_사라졌다() -> None:
    result = runner.invoke(app, ["--help"], color=True, env={"FORCE_COLOR": "1"})
    # 색이 켜진 CI에서만 rich가 옵션 이름을 쪼갠 실측이 있다. 정규화로 막는다.
    assert normalize_rich_message("아직 구현되지") not in normalize_rich_message(result.output)


# 환경변수 3층 (설계 D3 · §4.3 함정 ①).
#
# `_resolve_llm`의 `base_url or os.environ.get(...)`을 그대로 두면
# `default_map`이 채운 값이 `or`의 왼쪽에서 참이 되어 **설정 파일이
# 환경변수를 이긴다.** 값은 어느 쪽이 이기든 나오고 종료 코드는 0이라
# 이 테스트들이 없으면 결함이 절대 드러나지 않는다.


class _FakeCtx:
    """`get_parameter_source`만 흉내 낸다. 값의 출처를 고정해 준다.

    `typer._click`을 임포트하지 않는다 - 벤더링된 private 경로라
    typer 업그레이드가 위치를 바꾼다. 판정은 이름 문자열로 한다.
    """

    def __init__(self, source_name: str) -> None:
        self._source_name = source_name

    def get_parameter_source(self, name: str) -> object:
        return type("Src", (), {"name": self._source_name})()


def test_설정보다_환경변수가_우선한다(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuesift.cli import _resolve_llm

    monkeypatch.setenv("CUESIFT_BASE_URL", "http://env")
    monkeypatch.setenv("CUESIFT_MODEL", "m")
    base, _model, _key = _resolve_llm(_FakeCtx("DEFAULT_MAP"), "http://config", None)
    assert base == "http://env"


def test_CLI가_환경변수를_이긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuesift.cli import _resolve_llm

    monkeypatch.setenv("CUESIFT_BASE_URL", "http://env")
    monkeypatch.setenv("CUESIFT_MODEL", "m")
    base, _model, _key = _resolve_llm(_FakeCtx("COMMANDLINE"), "http://cli", None)
    assert base == "http://cli"


def test_환경변수가_없으면_설정을_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuesift.cli import _resolve_llm

    monkeypatch.delenv("CUESIFT_BASE_URL", raising=False)
    monkeypatch.setenv("CUESIFT_MODEL", "m")
    base, _model, _key = _resolve_llm(_FakeCtx("DEFAULT_MAP"), "http://config", None)
    assert base == "http://config"


def test_ctx가_없어도_동작한다(monkeypatch: pytest.MonkeyPatch) -> None:
    # 기존 호출부와의 호환. ctx를 모르면 설정에서 온 값이 아니라고 본다.
    from cuesift.cli import _resolve_llm

    monkeypatch.setenv("CUESIFT_BASE_URL", "http://env")
    monkeypatch.setenv("CUESIFT_MODEL", "m")
    base, _model, _key = _resolve_llm(None, "http://cli", None)
    assert base == "http://cli"


def test_model도_같은_양보를_한다(monkeypatch: pytest.MonkeyPatch) -> None:
    # `base_url`만 고치면 `model`이 반대 순서로 남는다. 두 줄이 같은
    # 헬퍼를 쓰는지를 여기서 본다.
    from cuesift.cli import _resolve_llm

    monkeypatch.setenv("CUESIFT_BASE_URL", "http://env")
    monkeypatch.setenv("CUESIFT_MODEL", "env-model")
    _base, model, _key = _resolve_llm(_FakeCtx("DEFAULT_MAP"), "http://config", "config-model")
    assert model == "env-model"


def test_실제_translate에서도_환경변수가_설정을_이긴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**단위 테스트만으로는 배선이 지켜지지 않는다.**

    `_resolve_llm`이 옳아도 `translate`가 `ctx`를 넘기지 않으면 `None`이
    들어가 설정이 다시 환경변수를 이긴다. 그 경우 위 다섯은 전부 초록이다.
    `--dry-run` 요약이 실제로 쓰이는 base_url을 찍으므로 여기서 관측한다.
    """
    target = tmp_path / "a.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n", encoding="utf-8")
    cfg = _config(tmp_path, "llm:\n  base_url: http://from-config\n  model: config-model\n")
    monkeypatch.setenv("CUESIFT_BASE_URL", "http://from-env")
    monkeypatch.setenv("CUESIFT_MODEL", "env-model")

    result = runner.invoke(
        app,
        ["--config", str(cfg), "translate", str(target), "--to", "en", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "http://from-env" in result.stdout
    assert "http://from-config" not in result.stdout
    assert "env-model" in result.stdout


def test_실제_translate에서_설정의_base_url이_도착한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**환경변수를 치우고 설정만 남긴 e2e.**

    바로 위 두 건은 환경변수가 이기는 것을 재므로, `ctx.default_map` 대입을
    지워도 환경변수 값이 그대로 나와 **초록으로 남는다**(변이 실측). 설정의
    값이 `translate` 본문까지 실제로 도달하는지는 여기서만 관측된다.
    """
    monkeypatch.delenv("CUESIFT_BASE_URL", raising=False)
    monkeypatch.delenv("CUESIFT_MODEL", raising=False)
    cfg = _config(tmp_path, "llm:\n  base_url: http://from-config\n  model: config-model\n")
    target = _srt(tmp_path)

    result = runner.invoke(
        app,
        ["--config", str(cfg), "translate", str(target), "--to", "en", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "http://from-config" in result.stdout
    assert "config-model" in result.stdout


def test_실제_translate에서_CLI가_환경변수를_이긴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 양보를 너무 넓게 잡으면(출처를 안 보고 늘 환경변수를 쓰면) 이 건이 깨진다.
    target = tmp_path / "a.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n", encoding="utf-8")
    cfg = _config(tmp_path, "llm:\n  base_url: http://from-config\n  model: config-model\n")
    monkeypatch.setenv("CUESIFT_BASE_URL", "http://from-env")
    monkeypatch.setenv("CUESIFT_MODEL", "env-model")

    result = runner.invoke(
        app,
        [
            "--config",
            str(cfg),
            "translate",
            str(target),
            "--to",
            "en",
            "--dry-run",
            "--base-url",
            "http://from-cli",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "http://from-cli" in result.stdout
    assert "http://from-env" not in result.stdout


# --- 진행 표시의 우선순위 4층 (FR-8.5 · 설계 D5) ---
#
# **`_resolve_llm`의 4층 테스트(위쪽)와 같은 형식을 쓴다.** 형식을 새로
# 만들면 두 게이트가 서로 다른 것을 재게 된다.


def test_진행_CLI가_환경변수를_이긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuesift.cli import _prefer_env_bool

    monkeypatch.setenv("CUESIFT_PROGRESS", "1")
    got = _prefer_env_bool(_FakeCtx("COMMANDLINE"), "progress", False, "CUESIFT_PROGRESS")
    assert got is False


def test_진행_환경변수가_설정_파일을_이긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    # `DEFAULT_MAP`은 설정 파일에서 온 값이다. `value or env`로 짜면
    # 설정의 True가 환경변수의 False를 이겨 `--no-progress`의 존재 이유가
    # 사라진다 (설계 D5).
    from cuesift.cli import _prefer_env_bool

    monkeypatch.setenv("CUESIFT_PROGRESS", "0")
    got = _prefer_env_bool(_FakeCtx("DEFAULT_MAP"), "progress", True, "CUESIFT_PROGRESS")
    assert got is False


def test_진행_설정_파일이_자동_감지를_이긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuesift.cli import _prefer_env_bool

    monkeypatch.delenv("CUESIFT_PROGRESS", raising=False)
    got = _prefer_env_bool(_FakeCtx("DEFAULT_MAP"), "progress", False, "CUESIFT_PROGRESS")
    assert got is False


def test_진행_아무것도_없으면_감지에_맡긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    # `None`이어야 `resolve_style`이 감지로 내려간다. `False`를 내면
    # 감지가 영영 안 돈다.
    from cuesift.cli import _prefer_env_bool

    monkeypatch.delenv("CUESIFT_PROGRESS", raising=False)
    assert _prefer_env_bool(None, "progress", None, "CUESIFT_PROGRESS") is None


def test_진행_False가_falsy라서_삼켜지지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    # `_prefer_env`의 문자열 판본을 그대로 베끼면 `value or env`가
    # `--no-progress`를 조용히 무시한다. **이 한 건이 불리언 형제를
    # 따로 만든 이유 전체다.**
    from cuesift.cli import _prefer_env_bool

    monkeypatch.setenv("CUESIFT_PROGRESS", "1")
    assert _prefer_env_bool(None, "progress", False, "CUESIFT_PROGRESS") is False


# --- FR-6.3 ① · FR-8.4: 설정 키 `triage.review_top_k` (설계 D3) ---
#
# **선별이 실제로 도는 실행으로 잰다.** 위의 `_translate`는 `--dry-run`이라
# 트리아지를 아예 부르지 않아 정책 라벨이 화면에 나오지 않는다(실측).
# 종료 코드만 단언하면 설정 키를 받고도 조용히 무시되는 배선 누락이 통과한다.

_TEN_CUES = Path(__file__).parent / "fixtures" / "ingest" / "ten_cues.srt"


def _risk_free(source: str) -> str:
    """Tier 0 신호를 **하나도** 내지 않는 번역문 (`test_cli_triage.py`와 같은 규칙).

    신호가 하나라도 끼면 hard fail이 검수 예산을 우회해(FR-6.2) 정책이 무엇을
    했는지 화면에서 안 보인다 - 그러면 이 절의 개수 단언이 정책이 아니라
    신호 개수를 재게 된다. 근거는 `test_cli_triage.py::_risk_free`에 있다.
    """
    return f"Line {''.join(c for c in source if c.isdigit())} of the talk"


def _triage(cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *after: str):
    """트리아지가 **끝까지 도는** `translate`. 프로바이더만 가짜로 바꾼다.

    `ten_cues.srt`(10큐)를 쓰므로 `50%`는 5개, `--review-top-k 3`은 3개로
    갈린다 - 두 정책이 같은 개수를 내면 어느 쪽이 이겼는지 화면으로 알 수 없다.
    """
    monkeypatch.setattr(
        "cuesift.cli._build_provider", lambda **_: EchoProvider(transform=_risk_free)
    )
    return runner.invoke(
        app,
        [
            "--config",
            str(cfg),
            "translate",
            str(_TEN_CUES),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--no-cache",
            *after,
        ],
    )


def test_CLI_top_k가_설정의_예산을_이긴다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 세 방식이 대등하다. 한 쌍만 고치면 나머지가 그대로 남는다.
    cfg = _config(tmp_path, 'triage:\n  review_budget: "50%"\n')
    result = _triage(cfg, tmp_path, monkeypatch, "--review-top-k", "3")
    assert result.exit_code == 0, result.output
    # 양보가 뒤집히면 설정의 5개가 그대로 남는다. 라벨과 개수 둘 다 본다.
    assert normalize_rich_message("상위 3개") in normalize_rich_message(result.output)
    assert "검수 대상 3개" in result.output


def test_CLI_예산이_설정의_top_k를_이긴다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path, "triage:\n  review_top_k: 3\n")
    result = _triage(cfg, tmp_path, monkeypatch, "--review-budget", "50%")
    assert result.exit_code == 0, result.output
    assert normalize_rich_message("예산 50%") in normalize_rich_message(result.output)
    assert "검수 대상 5개" in result.output


def test_설정에_세_정책이_전부_있으면_오류다(tmp_path: Path) -> None:
    # 전부 설정에서 왔으면 설정 파일 자체가 모순이다. 어느 쪽을 버려도
    # 사용자가 적은 정책 하나가 조용히 사라진다.
    cfg = _config(
        tmp_path,
        'triage:\n  review_budget: "10%"\n  review_threshold: 0.5\n  review_top_k: 3\n',
    )
    result = _translate(cfg, _srt(tmp_path))
    assert result.exit_code == 2
    stderr = normalize_rich_message(result.stderr)
    assert normalize_rich_message("설정 파일") in stderr
    # **"둘 다"가 아니라 "셋 다"다.** 셋이 있는데 "둘"이라고 말하면
    # 사용자는 지우지 않은 세 번째 키를 찾지 못한다.
    assert normalize_rich_message("셋 다") in stderr
    for flag in ("--review-budget", "--review-threshold", "--review-top-k"):
        assert normalize_rich_message(flag) in stderr


def test_설정의_top_k만으로_트리아지가_돈다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """설정 경유 경로를 끝까지 돌리는 유일한 게이트다 (§8.2 · FR-8.4).

    `click`의 옵션 타입 검증은 명령줄 인자만이 아니라 `default_map`
    (= `cuesift.yaml`)이 채운 값에도 걸린다. 그래서 설정에서 온 값은 본문
    로직보다 **먼저** 터질 수 있고, 그 부류는 로더만 부르는 문서 테스트로는
    잡히지 않는다. 배선이 없으면 키를 받고도 조용히 무시된다.
    """
    cfg = _config(tmp_path, "triage:\n  review_top_k: 2\n")
    result = _triage(cfg, tmp_path, monkeypatch)
    assert result.exit_code == 0, result.output
    assert normalize_rich_message("상위 2개") in normalize_rich_message(result.output)
    assert "검수 대상 2개" in result.output


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        # click의 `IntRange`가 `int(2.5) == 2`로 **먼저** 변환해 버리므로
        # 이 넷은 전부 exit 0으로 돌았다. `0.9`와 `false`는 `k=0`이 되어
        # **트리아지가 켜진 채 빈 검수 큐**를 냈다 - 사용자는 "정책을 껐다"고
        # 생각한 자리에서 조용히 아무것도 받지 못한다.
        ("2.5", "2.5"),
        ("0.9", "0.9"),
        ("true", "True"),
        ("false", "False"),
    ],
)
def test_설정의_top_k가_정수가_아니면_exit_2다(tmp_path: Path, raw: str, shown: str) -> None:
    cfg = _config(tmp_path, f"triage:\n  review_top_k: {raw}\n")
    result = _translate(cfg, _srt(tmp_path))
    assert result.exit_code == 2, result.output
    stderr = normalize_rich_message(result.stderr)
    # 어느 키가 어떤 값이라 거부됐는지 둘 다 나와야 원인을 안다.
    assert normalize_rich_message("triage.review_top_k") in stderr
    assert normalize_rich_message(shown) in stderr


def test_설정의_top_k는_0이면_그대로_통과한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """엄격 검사가 정상 값까지 막지 않는지 본다. `0`은 "hard fail만 보기"다(D4)."""
    cfg = _config(tmp_path, "triage:\n  review_top_k: 0\n")
    result = _triage(cfg, tmp_path, monkeypatch)
    assert result.exit_code == 0, result.output
    assert normalize_rich_message("상위 0개") in normalize_rich_message(result.output)
    assert "검수 대상 0개" in result.output
