"""`cuesift translate` 배선 검증 (FR-8.1 · 설계 §6).

**네트워크를 타지 않는다.** `_build_provider`를 monkeypatch해 가짜를 꽂는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fakes.provider import EchoProvider
from typer.testing import CliRunner

from cuesift.cli import app
from cuesift.translate.provider import FatalProviderError, ProviderError

_FIXTURES = Path(__file__).parent / "fixtures" / "ingest"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """사용자 환경이 테스트 결과를 바꾸지 못하게 한다."""
    for name in ("CUESIFT_BASE_URL", "CUESIFT_MODEL", "CUESIFT_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: object) -> None:
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: provider)


def _args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "translate",
        str(_FIXTURES / "minimal.srt"),
        "--to",
        "en",
        "--out",
        str(tmp_path),
        "--base-url",
        "http://h/v1",
        "--model",
        "m1",
        "--cache-dir",
        str(tmp_path / "cache"),
        *extra,
    ]


def _no_cache_args(tmp_path: Path) -> list[str]:
    """`--cache-dir` 없이 `--no-cache`만 준다.

    `_args`는 `--cache-dir`을 항상 넣으므로 여기 쓰면
    `test_no_cache와_cache_dir을_함께_주면_exit_2다`가 못 박은 조합과
    부딪혀 exit 2로 즉시 끝난다 - 그러면 호출 수 단언이 "0 == 0"으로
    항상 참이 되어 이 테스트가 아무것도 검증하지 못한다.
    """
    return [
        "translate",
        str(_FIXTURES / "minimal.srt"),
        "--to",
        "en",
        "--out",
        str(tmp_path),
        "--base-url",
        "http://h/v1",
        "--model",
        "m1",
        "--no-cache",
    ]


def test_번역해서_파일을_낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path))

    assert result.exit_code == 0, result.output
    assert (tmp_path / "minimal.en.srt").exists()


def test_원문_언어_태그를_치환한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "ep01.ko.srt"
    source.write_bytes((_FIXTURES / "minimal.srt").read_bytes())
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(source),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--source-lang",
            "ko",
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "ep01.en.srt").exists()
    assert not (tmp_path / "ep01.ko.en.srt").exists()


def test_설정이_없으면_exit_2다(tmp_path: Path) -> None:
    # 기본값을 넣지 않는다. localhost를 기본값으로 넣으면 Ollama가 없는
    # 사람이 연결 실패를 받는데, 그것은 "설정을 안 했다"보다 진단이 어렵다.
    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en",
            "--out",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 2


def test_환경변수를_읽는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUESIFT_BASE_URL", "http://h/v1")
    monkeypatch.setenv("CUESIFT_MODEL", "m1")
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0, result.output


def test_없는_파일은_exit_2다(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "translate",
            str(tmp_path / "없다.srt"),
            "--to",
            "en",
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
        ],
    )

    assert result.exit_code == 2


def test_자막이_아니면_exit_66이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "not_subtitle.txt"),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 66


def test_치명적_프로바이더_실패는_exit_69다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 안 잡으면 traceback이 되어 exit 1("부분 실패")로 오보된다 (설계 §8).
    class Dead:
        name = "dead"

        def complete(self, messages, *, temperature, max_tokens):
            raise FatalProviderError("401 Unauthorized")

    _patch_provider(monkeypatch, Dead())

    result = runner.invoke(app, _args(tmp_path))

    assert result.exit_code == 69
    assert "401" in result.output


def test_부분_실패는_exit_1이고_원문이_남는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # garbage=True면 배치도 개별 폴백도 전부 파싱 실패한다.
    _patch_provider(monkeypatch, EchoProvider(garbage=True))

    result = runner.invoke(app, _args(tmp_path))

    assert result.exit_code == 1
    out = tmp_path / "minimal.en.srt"
    assert out.exists()  # 실패해도 파일은 나온다
    assert "00000" in result.output  # 실패한 세그먼트 ID를 나열한다


def test_잘못된_base_url은_exit_2다(tmp_path: Path) -> None:
    # 설정 오류는 명령줄이 틀린 것이다. ProviderError가 아니다.
    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://[::1",
            "--model",
            "m1",
        ],
    )

    assert result.exit_code == 2


def test_출력이_입력을_덮으면_거부한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 이것이 없으면 원본 자막이 번역문으로 덮여 되돌릴 수 없다.
    source = tmp_path / "ep01.en.srt"
    source.write_bytes((_FIXTURES / "minimal.srt").read_bytes())
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(source),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--source-lang",
            "en",
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 2


def test_두_번째_실행은_호출하지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # **재개 게이트다.** 호출 수를 센다.
    first = EchoProvider()
    _patch_provider(monkeypatch, first)
    assert runner.invoke(app, _args(tmp_path)).exit_code == 0
    calls_1 = len(first.calls)

    second = EchoProvider()
    _patch_provider(monkeypatch, second)
    assert runner.invoke(app, _args(tmp_path)).exit_code == 0

    assert calls_1 > 0
    assert len(second.calls) == 0


def test_no_cache는_매번_호출한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = EchoProvider()
    _patch_provider(monkeypatch, first)
    assert runner.invoke(app, _no_cache_args(tmp_path)).exit_code == 0
    calls_1 = len(first.calls)

    second = EchoProvider()
    _patch_provider(monkeypatch, second)
    assert runner.invoke(app, _no_cache_args(tmp_path)).exit_code == 0

    assert calls_1 > 0
    assert len(second.calls) == calls_1


def test_no_cache는_캐시_끔을_명시한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 설계 §4.3: "0개"와 "꺼져 있음"은 구별돼야 한다. --no-cache면 provider가
    # CachingProvider로 감싸이지 않아 hits/misses를 읽을 방법이 없는데,
    # 그것을 "실제 호출 0개"로 찍으면 네트워크를 15번 타고도 화면은
    # "안 탔다"고 거짓말한다.
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _no_cache_args(tmp_path))

    assert result.exit_code == 0, result.output
    assert "실제 호출 0개" not in result.output


def test_no_cache와_cache_dir을_함께_주면_exit_2다(tmp_path: Path) -> None:
    result = runner.invoke(app, [*_args(tmp_path), "--no-cache"])

    assert result.exit_code == 2


def test_review_budget은_경고한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 조용한 무시는 이 저장소가 1급으로 금지한 것이다 (--config 선례).
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--review-budget", "10%"])

    assert result.exit_code == 0, result.output
    assert "review-budget" in result.output


def test_용어집을_읽는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # FR-2.3이 CLI에서 도달 가능해지는 것을 고정한다.
    glossary = tmp_path / "g.yaml"
    glossary.write_text(
        "entries:\n  - source: 안녕\n    targets:\n      en: [Hello]\n",
        encoding="utf-8",
    )
    provider = EchoProvider()
    _patch_provider(monkeypatch, provider)

    result = runner.invoke(app, [*_args(tmp_path), "--glossary", str(glossary)])

    assert result.exit_code == 0, result.output


def test_망가진_용어집은_exit_66이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    glossary = tmp_path / "g.yaml"
    glossary.write_text("entries가 없다\n", encoding="utf-8")
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--glossary", str(glossary)])

    assert result.exit_code == 66


# ── Task 6 — `--dry-run` 실제 구현 (NFR-2) ──────────────────────────────
#
# Task 4까지는 "경고 후 무시"(--review-budget과 같은 임시 처리)였다 - 아래는
# 그 자리를 교체한 실제 구현을 고정한다.


def test_dry_run은_파일을_만들지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = EchoProvider()
    _patch_provider(monkeypatch, provider)

    result = runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert not (tmp_path / "minimal.en.srt").exists()


def test_dry_run은_네트워크를_타지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # "실행 전 추정"이라는 NFR-2의 전제가 여기 걸려 있다. `_build_provider`는
    # 부른다(identity를 얻으려고) - 여기서 재는 것은 "프로바이더를 만드는가"가
    # 아니라 "`complete()`를 부르는가"다. 전자는 네트워크를 타지 않는다
    # (`_dry_run_report`의 독스트링 참고).
    provider = EchoProvider()
    _patch_provider(monkeypatch, provider)

    runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert provider.calls == []


def test_dry_run이_배치_수를_낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert "배치" in result.output
    assert "호출 필요" in result.output


def test_dry_run이_캐시_히트를_센다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 실사용에서 가장 쓸모있는 정보다 - "몇 번 더 불러야 하나". 이 테스트는
    # dry-run의 identity 계산이 실제 실행과 어긋나지 않는다는 것도 함께
    # 고정한다 - 어긋나면 캐시 파일 이름이 달라져 여기서 반드시 "히트 0개"로
    # 떨어진다.
    _patch_provider(monkeypatch, EchoProvider())
    runner.invoke(app, _args(tmp_path))  # 먼저 실제로 돌려 캐시를 채운다

    result = runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert "캐시 히트 1개" in result.output
    assert "호출 필요 0개" in result.output


def test_dry_run은_토큰을_추정하지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 계수 출처가 없다 (§11 R8). 틀린 수치는 수치가 없는 것보다 나쁘다.
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert "토큰 추정" not in result.output
    assert "$" not in result.output


def test_dry_run_no_cache는_히트를_0으로_낸다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_provider(monkeypatch, EchoProvider())
    runner.invoke(app, _args(tmp_path))

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--no-cache",
            "--dry-run",
        ],
    )

    assert "캐시 히트 0개" in result.output


def test_dry_run의_identity가_실제와_같다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # dry-run은 identity를 손으로 다시 조립하지 않는다 - 실제 실행과 같은
    # `_cache_identity(provider)`를 그대로 불러 쓴다(cli.py의 `translate()`
    # 안 dry-run 분기 참고). 그래서 여기서 실제로 고정하는 것은 "dry-run이
    # real 실행과 같은 provider 인스턴스·같은 identity 계산 경로를 타는가"다.
    #
    # **끝 슬래시(rstrip) 자체는 이 테스트로 검증되지 않는다.** `_patch_provider`가
    # 꽂는 `EchoProvider`는 `base_url`을 받지 않고 `cache_identity`가 고정
    # 문자열("echo|fake|v1")이라 끝 슬래시가 fake에는 전달되지 않는다 - 브리프
    # 원안은 이 테스트로 rstrip을 겨냥했지만, 손조립 자체를 없앤 지금 구현에서는
    # 애초에 겨냥할 rstrip 코드가 cli.py에 없다. rstrip 정합성은
    # `OpenAICompatibleProvider.cache_identity` 자신의 계약이고 그 테스트는
    # translate/ 쪽(WP7a)이 이미 갖고 있다 - 이 태스크는 translate/를 고치지
    # 않는다.
    _patch_provider(monkeypatch, EchoProvider())
    runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1/",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1/",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--dry-run",
        ],
    )

    assert "캐시 히트 1개" in result.output


def test_캐시_경고에_언어_라벨이_있고_summary_line이_속지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 함께 처리할 것 (A)(B) - CachingProvider의 warn 콜백에 언어 라벨이 없으면
    # 캐시 쓰기 실패 경고가 언어 수만큼 무라벨로 반복된다[리뷰어 실측 3회,
    # WP7b Task 5 리뷰가 넘김]. (A)로 라벨을 붙이면 그 경고 줄도 `[en]`으로
    # 시작해 `_summary_line`이 진짜 헤더로 오인할 수 있다 - (B)로 다음 줄이
    # "  세그먼트 "인지까지 확인하게 고쳤다.
    #
    # cache_dir 자리에 이미 파일을 둬 store()의 mkdir(parents=True,
    # exist_ok=True)가 FileExistsError를 내게 만든다(실측: exist_ok는 "이미
    # 디렉터리"만 봐주고 "이미 파일"은 봐주지 않는다) - 캐시 경고 경로를
    # network 없이 결정적으로 재현하는 가장 싼 방법이다.
    _patch_provider(monkeypatch, EchoProvider())
    cache_as_file = tmp_path / "cache"
    cache_as_file.write_text("x", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(cache_as_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[en] 캐시를 쓰지 못했다" in result.output  # (A): 라벨이 붙는다
    assert "세그먼트 2개" in _summary_line(result.output, "en")  # (B): 진짜 헤더를 찾는다


# ── Task 6 리뷰 라운드 1 ─────────────────────────────────────────────────


def test_대상_언어_순서와_무관하게_용어집_실패를_잡는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # [Important 1] `load_glossary`의 대응어 타입 검사는 언어마다 다른 값을
    # 본다 - ja 대응어만 리스트가 아니면 target_lang="ja"로는 실패하고
    # target_lang="en"으로는 통과한다(실측: 리뷰어). upfront 검사가
    # `targets[0]`만 보면 `--to en,ja`(en이 먼저라 통과)와
    # `--to ja,en`(ja가 먼저라 실패)이 **같은 파일에 다른 종료 코드**를
    # 낸다 - dry-run을 CI 사전 점검으로 쓰면 실제 실행이 66으로 거절하는
    # 용어집에 순서 하나로 0을 돌려줄 수 있다.
    glossary = tmp_path / "g.yaml"
    glossary.write_text(
        "entries:\n  - source: 안녕\n    targets:\n      en: [Hello]\n      ja: 리스트가_아니다\n",
        encoding="utf-8",
    )
    _patch_provider(monkeypatch, EchoProvider())

    codes = {}
    for order in ("en,ja", "ja,en"):
        result = runner.invoke(
            app,
            [
                "translate",
                str(_FIXTURES / "minimal.srt"),
                "--to",
                order,
                "--out",
                str(tmp_path),
                "--base-url",
                "http://h/v1",
                "--model",
                "m1",
                "--glossary",
                str(glossary),
                "--dry-run",
            ],
        )
        codes[order] = result.exit_code

    assert codes["en,ja"] == codes["ja,en"] == 66, codes


def test_호출_필요_수는_하한임을_표시한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # [Important 2] "호출 필요 N개"는 배치가 그대로 성공할 때의 하한이다.
    # FR-2.6 배치 폴백(형식 위반 시 세그먼트별 개별 재호출)과 재시도가
    # 발동하면 실제 호출은 이보다 몇 배로 는다(리뷰어 실측: 12세그먼트·
    # 2배치에서 dry-run "2개" vs 실제 14회, 7배). "이상"을 붙여 하한임을
    # 드러낸다 - 정확한 수를 내려 들지 않는다(§11 R8, 출처 없는 추정 금지).
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert "호출 필요 1개 이상" in result.output


def test_dry_run은_파일도_캐시도_쓰지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # [Minor a] 기존 "파일을 만들지 않는다" 테스트는 출력 자막 파일 하나만
    # 본다 - 쓰기 계열 자체가 아니라 "특정 파일 하나가 없다"만 지켜서
    # cache_dir.mkdir() 같은 다른 쓰기가 섞여도 잡지 못했다(리뷰어 변이
    # F9, 0킬). 이 저장소가 실제로 쓰는 두 지점 - 출력 자막
    # (`cuesift.cli.write_subtitle`)과 캐시 저장(`cuesift.store.provider`가
    # 참조하는 `store`) - 을 직접 계측해 "불리면 죽는다"로 막는다.
    def _forbidden(*_a: object, **_kw: object) -> None:
        raise AssertionError("dry-run에서 쓰기 함수가 불렸다")

    monkeypatch.setattr("cuesift.cli.write_subtitle", _forbidden)
    monkeypatch.setattr("cuesift.store.provider.store", _forbidden)
    # [Task 7 (C)] 위 둘은 이름을 아는 쓰기만 잡는다 - `cache_dir.mkdir(...)`
    # 처럼 함수 밖에서 직접 부르는 다른 쓰기는 새어 나가도 죽지 않는다
    # (Task 6 리뷰어 실측: 이 변이가 기존 975건을 전부 통과했다).
    # `Path.mkdir` 자체를 계측해 이름과 무관하게 "디스크에 뭔가 만들었다"를 막는다.
    monkeypatch.setattr(Path, "mkdir", _forbidden)
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output


def test_dry_run이_다배치_다국어_용어집_맥락에서_실제_실행과_일치한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # [Important 3+4] 기존 dry-run 테스트 7개가 전부 minimal.srt(2세그먼트=
    # 1배치)·단일 언어·용어집 없음·work_context 없음 조합이었다. 배치
    # 크기·context_window·work_context·용어집 넷 중 하나를 dry-run만 다르게
    # 계산해도(리뷰어 변이 F6b·F11·F12·F13, 용어집을 targets[0]로만 읽는
    # F8) 어느 것도 안 죽었다 - 배치가 항상 1개라 배치 수 계산이 무엇이든
    # 1이 나왔고, 나머지 셋이 항상 기본값이라 dry-run이 빠뜨려도 결과가
    # 같았다(4번째로 "테스트 데이터가 미탐을 만든" 사례).
    #
    # large.srt(26세그먼트=3배치, size=10) · --to en,ja(대응어가 다른
    # 용어집) · --work-context로 넷을 한 번에 겨눈다. 한 번 실제로 돌려
    # en·ja 둘 다(각자의 대응어로) 캐시를 3배치씩 정확히 채운 뒤, dry-run이
    # "히트 3개 · 필요 0개 이상"을 예고하는지, 그리고 그 예고가 실제
    # 재실행의 "히트 3개 · 실제 호출 0개"와 같은지 확인한다.
    glossary = tmp_path / "g.yaml"
    glossary.write_text(
        "entries:\n"
        "  - source: 안녕\n"
        "    targets:\n"
        "      en: [Hi]\n"
        "      ja: [반갑습니다이것은아주긴대응어예시문장입니다]\n",
        encoding="utf-8",
    )
    _patch_provider(monkeypatch, EchoProvider())
    cache_dir = tmp_path / "cache"

    def _args_large(*, to: str) -> list[str]:
        return [
            "translate",
            str(_FIXTURES / "large.srt"),
            "--to",
            to,
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--glossary",
            str(glossary),
            "--work-context",
            "다큐멘터리",
            "--cache-dir",
            str(cache_dir),
        ]

    # 1) 두 언어를 한 번 실제로 돌려 3배치 × 2언어 캐시를 전부 채운다 - en·ja
    #    각자 자기 언어의 대응어로 채워진 올바른 캐시다.
    first = runner.invoke(app, _args_large(to="en,ja"))
    assert first.exit_code == 0, first.output

    # 2) 같은 조건으로 dry-run - 둘 다 3배치 전부 히트여야 한다.
    dry = runner.invoke(app, [*_args_large(to="en,ja"), "--dry-run"])
    assert dry.exit_code == 0, dry.output
    for lang in ("en", "ja"):
        assert "배치 3개" in _dry_run_line(dry.output, lang), (lang, dry.output)
        assert "캐시 히트 3개 · 호출 필요 0개 이상" in _dry_run_line(dry.output, lang, offset=2), (
            lang,
            dry.output,
        )

    # 3) 실제로 다시 돌려 dry-run이 예고한 수와 같은지 확인한다 - 이것이
    #    이 테스트의 핵심 단언이다.
    second = runner.invoke(app, _args_large(to="en,ja"))
    assert second.exit_code == 0, second.output
    for lang in ("en", "ja"):
        assert "캐시 히트 3개 · 실제 호출 0개" in _summary_line(second.output, lang, offset=2), (
            lang,
            second.output,
        )


# ── 리뷰 라운드 1 (Critical 2) — 용어집 예외 누수 4종 ──────────────────


def test_용어집_미종료_스칼라는_exit_66이다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # yaml.parser.ParserError - ValueError도 OSError도 아니라 그대로 샜다.
    glossary = tmp_path / "g.yaml"
    glossary.write_text('entries:\n  - source: "안녕\n', encoding="utf-8")
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--glossary", str(glossary)])

    assert result.exit_code == 66


def test_용어집_탭_들여쓰기는_exit_66이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # yaml.scanner.ScannerError - 위와 같은 축의 다른 예외 클래스다.
    glossary = tmp_path / "g.yaml"
    glossary.write_text("entries:\n\t- source: 안녕\n", encoding="utf-8")
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--glossary", str(glossary)])

    assert result.exit_code == 66


def test_용어집_entries가_리스트가_아니면_exit_66이다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # entries: 5는 유효한 YAML이라 safe_load는 성공한다. load_glossary의
    # enumerate(raw["entries"] or [])가 enumerate(5)가 되어 TypeError를 낸다.
    glossary = tmp_path / "g.yaml"
    glossary.write_text("entries: 5\n", encoding="utf-8")
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--glossary", str(glossary)])

    assert result.exit_code == 66


def test_용어집_targets가_dict가_아니면_exit_66이다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # targets: Hello는 유효한 YAML이지만 item.get("targets")가 문자열이 되어
    # 그 위의 .get(target_lang)에서 AttributeError를 낸다.
    glossary = tmp_path / "g.yaml"
    glossary.write_text("entries:\n  - source: 안녕\n    targets: Hello\n", encoding="utf-8")
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--glossary", str(glossary)])

    assert result.exit_code == 66


def test_dry_run과_실제_실행의_용어집_오류_종료코드가_같다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # [최종 리뷰 C2] `translate()`의 dry-run 분기(위 upfront 검사)가
    # `except Exception`으로 넓게 잡는 것은 옳다 - 문제는 그 넓이를
    # 지키는 게이트가 없었다는 것이다(실측: `(OSError, ValueError)`로
    # 좁히는 변이가 이 파일 975개 중 0킬). 좁혀진 상태로 돌리면 위 네
    # 테스트가 실제 실행에서 이미 exit_66으로 고정한 바로 그 용어집
    # 결함이 dry-run에서는 예외가 커맨드 본문을 빠져나가 exit 1(이
    # 저장소에서 1은 "규격 위반 발견")로 오보된다 - dry-run을 CI
    # 사전 점검으로 쓰면 "규격 위반"과 "용어집이 깨졌다"가 뒤섞인다.
    defects = [
        ("미종료 스칼라", 'entries:\n  - source: "안녕\n'),
        ("탭 들여쓰기", "entries:\n\t- source: 안녕\n"),
        ("entries가 리스트가 아님", "entries: 5\n"),
        ("targets가 dict가 아님", "entries:\n  - source: 안녕\n    targets: Hello\n"),
    ]
    _patch_provider(monkeypatch, EchoProvider())

    for i, (name, content) in enumerate(defects):
        glossary = tmp_path / f"g{i}.yaml"
        glossary.write_text(content, encoding="utf-8")

        dry = runner.invoke(app, _args(tmp_path, "--glossary", str(glossary), "--dry-run"))
        real = runner.invoke(app, _args(tmp_path, "--glossary", str(glossary)))

        assert dry.exit_code == 66, (name, "dry-run", dry.output)
        assert real.exit_code == 66, (name, "실제 실행", real.output)


# ── 리뷰 라운드 1 (Critical 3) — write_subtitle 예외 누수 2종 ──────────


def test_출력_디렉터리_자리가_이미_파일이면_exit_2다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --out에 file_okay=False를 걸어 typer가 본문 전에 거른다 - write_subtitle의
    # mkdir(parents=True, exist_ok=True)가 FileExistsError를 내기 전에 막는다.
    #
    # **가드가 회귀해도 네트워크를 타지 않도록 프로바이더를 패치한다.** 이
    # 파일의 모듈 독스트링이 "네트워크를 타지 않는다"고 선언한다 - 가드가
    # 무력화되면 패치 없는 테스트는 진짜 http://h/v1로 연결을 시도해 재시도
    # 백오프까지 기다린다(실측: WP7b Task 4 리뷰 라운드 3, 이 파일 3개 테스트
    # 소요가 7~8초에서 24~25초로 늘어남). 패치가 있으면 가드가 회귀해도
    # `_build_provider`까지 가지 않거나, 가더라도 EchoProvider는 즉시 응답해
    # 시간이 늘지 않는다.
    _patch_provider(monkeypatch, EchoProvider())

    out_as_file = tmp_path / "out"
    out_as_file.write_text("x", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en",
            "--out",
            str(out_as_file),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
        ],
    )

    assert result.exit_code == 2


def test_출력_경로_자리가_이미_디렉터리면_exit_66이다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --out 자체는 정상 디렉터리이지만 그 안의 언어별 파일 이름
    # (minimal.en.srt)이 이미 디렉터리로 존재하는 경우다 - --out 수준의
    # file_okay=False로는 못 막고 write_subtitle 자신의 try만 잡을 수 있다.
    (tmp_path / "minimal.en.srt").mkdir()
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, _args(tmp_path))

    assert result.exit_code == 66


# ── 리뷰 라운드 1 (Important 4) — --to 값 검증 ──────────────────────────


def test_유효하지_않은_언어_태그는_exit_2다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 'e:n'은 NTFS 대체 데이터 스트림으로 해석되고, 나머지 둘은 --out 밖에
    # 쓰거나 exit 1로 새는 경로 조작이다. 셋 다 검증 없이 파일 경로 조각이
    # 되기 전에 막아야 한다.
    #
    # **가드가 회귀해도 네트워크를 타지 않도록 프로바이더를 패치한다.**
    # 위 `test_출력_디렉터리_자리가_이미_파일이면_exit_2다`의 주석 참고 -
    # 이 파일의 "네트워크를 타지 않는다"는 선언은 mutant 상태에서도 지켜야
    # 계약이지, 정상 경로에서만 지켜지면 주석에 불과하다.
    _patch_provider(monkeypatch, EchoProvider())

    for bad in ("e:n", "x/../../ESCAPED", "../pwned"):
        result = runner.invoke(
            app,
            [
                "translate",
                str(_FIXTURES / "minimal.srt"),
                "--to",
                bad,
                "--out",
                str(tmp_path),
                "--base-url",
                "http://h/v1",
                "--model",
                "m1",
            ],
        )

        assert result.exit_code == 2, f"{bad!r} 가 거부되지 않았다: {result.output}"


def test_zh_Hans같은_서브태그는_받아들인다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "zh-Hans",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "minimal.zh-Hans.srt").exists()


# ── 리뷰 라운드 1 (Important 7) — 맨 ProviderError 최종 방어 ────────────


def test_맨_ProviderError도_exit_69로_막는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Provider 프로토콜은 "Fatal 또는 Retryable로만 던져라"라고 요구하지만
    # 런타임에 강제되지 않는다. 그 계약을 어기는 서드파티 구현이 traceback
    # 대신 진단 가능한 exit 코드를 받아야 한다.
    class Rude:
        name = "rude"

        def complete(self, messages, *, temperature, max_tokens):
            raise ProviderError("이상한 실패")

    _patch_provider(monkeypatch, Rude())

    result = runner.invoke(app, _args(tmp_path))

    assert result.exit_code == 69


# ── 리뷰 라운드 1 (함께 처리할 것 b) — 대소문자 무시 언어 태그 치환 ─────


# ── Task 5 — 여러 대상 언어 ────────────────────────────────────────────


def test_두_언어를_모두_낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en,ja",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "minimal.en.srt").exists()
    assert (tmp_path / "minimal.ja.srt").exists()


def test_언어별로_요약을_낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 뭉뚱그리면 "ja만 전부 실패"가 "2개 언어 6건 중 3건 실패"로 보인다.
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en,ja",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert "[en]" in result.output
    assert "[ja]" in result.output


def test_치명적_실패는_다음_언어를_돌지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 401을 언어 수만큼 반복하면 진짜 원인이 실패 더미 아래 묻힌다.
    class Dead:
        name = "dead"
        cache_identity = "dead|u|m"

        def __init__(self) -> None:
            self.calls: list[object] = []

        def complete(self, messages, *, temperature, max_tokens):
            self.calls.append(messages)
            raise FatalProviderError("401 Unauthorized")

    provider = Dead()
    _patch_provider(monkeypatch, provider)

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en,ja,th",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 69
    # 한 언어에서만 시도했다. 세 언어를 다 돌면 호출이 3배가 된다.
    assert len(provider.calls) == 1


def test_한_언어가_실패해도_나머지를_낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 부분 실패는 FR-2.6의 정신대로 계속 진행한다.
    _patch_provider(monkeypatch, EchoProvider(garbage=True))

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en,ja",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 1
    assert (tmp_path / "minimal.en.srt").exists()
    assert (tmp_path / "minimal.ja.srt").exists()


class _FirstLanguageFails:
    """처음 `break_calls`번의 호출만 깨뜨리고 이후는 EchoProvider처럼 정상 응답한다.

    **정정 (팀장 지시)**: 브리프 원안(`test_종료_코드는_가장_나쁜_것이다`)은
    en·ja 둘 다 용어집 오류(66)를 내는 형태였다 - 용어집은 언어와 무관한
    같은 파일이라 **둘 다** 실패하고, 결과가 66이어도 그것은 max()가
    "가장 나쁜 것"을 골랐다는 증거가 아니다. 코드가 하나뿐이면 max()든
    "마지막 것"이든 같은 답을 낸다.

    판별력을 가지려면 **첫 언어가 실패하고 뒤 언어가 성공해야** 한다.
    en(minimal.srt 2세그먼트, 배치 크기 10)은 배치 호출 1회가 깨지면 개별
    폴백 2회로 강등되어(engine.py `_fallback_individually`) 총 3회가
    "en의 모든 호출"이다 - 그 3회를 깨뜨려 두 세그먼트를 전부
    실패시킨다(부분 실패, exit 1). ja는 4번째 호출부터라 한 번도 깨지지
    않고 완전히 성공한다(exit 0). max(1, 0) == 1이 정답이고,
    `worst = code`(마지막 것)로 바꾸면 0이 나와 이 테스트가 죽는다.
    """

    name = "first-fails"
    cache_identity = "first-fails|fake|v1"

    def __init__(self, break_calls: int) -> None:
        self._break_calls = break_calls
        self._broken = EchoProvider(garbage=True)
        self._normal = EchoProvider()
        self.calls: list[object] = []

    def complete(self, messages, *, temperature, max_tokens):
        self.calls.append(messages)
        target = self._broken if len(self.calls) <= self._break_calls else self._normal
        return target.complete(messages, temperature=temperature, max_tokens=max_tokens)


def _summary_line(output: str, lang: str, offset: int = 1) -> str:
    """`_format_translate_summary`가 낸 `[lang]` 헤더 기준 `offset`번째 뒤 줄을 뽑는다.

    기본값 `offset=1`은 세그먼트 요약 줄이다. `offset=2`는 캐시 줄
    (`  캐시 히트 N개 · 실제 호출 M개`)을 뽑을 때 쓴다(WP7b Task 6 리뷰
    라운드 1 Important 3+4 - dry-run의 예고와 실제 실행의 결과를 나란히
    비교하려면 이 줄이 필요하다).

    리뷰 라운드 1 Important 1 - `exit_code == 1`만으로는 "en 실패·ja 성공"과
    "en·ja 둘 다 실패"를 구별하지 못한다(둘 다 exit 1). 언어별 요약 줄을
    직접 읽어야 시나리오가 실제로 의도한 형태(en만 실패)인지 확인된다.

    **함께 처리할 것 (B)**: `[lang]`로 시작하는 줄이 진짜 헤더만은 아니다 -
    `CachingProvider`의 캐시 경고도 (A) 수정 이후 `[lang] 캐시를 쓰지
    못했다...` 형태로 같은 접두어를 쓴다[리뷰어 실측]. `line.startswith`만
    보면 그 경고 줄을 헤더로 오인해 **바로 다음 줄(진짜 헤더)을 세그먼트
    요약으로 잘못 읽는다** - 방향은 안전하다(거짓 통과가 아니라 거짓
    실패다)지만 원인 추적이 어려워진다. 그래서 다음 줄이
    `"  세그먼트 "`로 시작하는 줄까지 확인해야 진짜 헤더를 고른다.
    """
    lines = output.splitlines()
    header = next(
        i
        for i, line in enumerate(lines)
        if line.startswith(f"[{lang}]")
        and i + 1 < len(lines)
        and lines[i + 1].startswith("  세그먼트 ")
    )
    return lines[header + offset]


def _dry_run_line(output: str, lang: str, offset: int = 1) -> str:
    """`_dry_run_report`가 낸 `[lang]` 헤더 기준 `offset`번째 뒤 줄을 뽑는다.

    `_summary_line`과 짝이지만 다음 줄 형태가 다르다 - dry-run 헤더 다음
    줄은 `"  배치 "`로 시작한다(실제 실행의 `"  세그먼트 "`와 다르다).
    `offset=1`은 배치 줄, `offset=2`는 캐시 히트/호출 필요 줄이다.
    """
    lines = output.splitlines()
    header = next(
        i
        for i, line in enumerate(lines)
        if line.startswith(f"[{lang}]")
        and i + 1 < len(lines)
        and lines[i + 1].startswith("  배치 ")
    )
    return lines[header + offset]


def test_종료_코드는_가장_나쁜_것이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # en은 부분 실패(1), ja는 성공(0)이다. max(1, 0) == 1이 정답이다.
    # 판별력의 근거는 _FirstLanguageFails의 독스트링 참고.
    provider = _FirstLanguageFails(break_calls=3)
    _patch_provider(monkeypatch, provider)

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en,ja",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 1, result.output
    # **리뷰 라운드 1 Important 1**: 위 exit_code 단언만으로는 이 시나리오가
    # "en 실패·ja 성공"인지 "en·ja 둘 다 실패"인지 구별하지 못한다(실측:
    # `break_calls`를 6으로 드리프트시키면 후자가 되는데도 exit 1은 그대로다).
    # en이 완전히 실패하고 ja가 완전히 성공했음을 직접 확인한다.
    assert "실패 2개" in _summary_line(result.output, "en")
    assert "실패 0개" in _summary_line(result.output, "ja")


class _SecondLanguageIsFatal:
    """en(첫 호출)은 성공시키고 ja(두 번째 호출부터)는 Fatal로 거부한다.

    **리뷰 라운드 1 Important 2**: 유일한 기존 Fatal 테스트
    (`test_치명적_실패는_다음_언어를_돌지_않는다`)는 Dead 프로바이더가
    **첫 호출부터** 죽어 "성공한 언어가 하나도 없다" - en이 이미 파일로
    나온 상태에서 ja가 죽는, 부분 출력이 남는 상황을 재현하지 못했다.
    en(minimal.srt 2세그먼트, 배치 크기 10)은 배치 호출 1회로 끝나므로
    두 번째 호출부터 깨뜨리면 정확히 ja에서 죽는다.
    """

    name = "second-fatal"
    cache_identity = "second-fatal|fake|v1"

    def __init__(self) -> None:
        self._normal = EchoProvider()
        self.calls: list[object] = []

    def complete(self, messages, *, temperature, max_tokens):
        self.calls.append(messages)
        if len(self.calls) == 1:
            return self._normal.complete(messages, temperature=temperature, max_tokens=max_tokens)
        raise FatalProviderError("401 Unauthorized")


def test_Fatal_중단은_남은_언어를_알린다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # en은 성공하고 ja에서 401이 나 중단된다. th는 아예 시도되지 않는다.
    # 화면이 "어느 언어에서 멈췄는가"와 "뭐가 안 됐는가"를 말해야
    # `--to en,ja,th`가 중단된 것과 `--to en`만 친 것을 구별할 수 있다
    # (리뷰 라운드 1 Important 2).
    provider = _SecondLanguageIsFatal()
    _patch_provider(monkeypatch, provider)

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en,ja,th",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 69
    assert (tmp_path / "minimal.en.srt").exists()  # en은 성공해 파일이 남는다
    assert not (tmp_path / "minimal.ja.srt").exists()
    assert not (tmp_path / "minimal.th.srt").exists()
    assert "[ja] 프로바이더가 요청을 거부했다" in result.output  # 어느 언어에서 멈췄는가
    assert "중단: 남은 대상 언어 th는 시도하지 않았다" in result.output  # 뭐가 안 됐는가


def test_용어집_실패_메시지에_언어_라벨이_있다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # **리뷰 라운드 1 Important 3**: 용어집은 언어 무관 단일 파일이라 en·ja
    # 둘 다에서 깨지면 실측으로 "똑같은 줄이 반복"됐다 - 어느 언어의 실패인지
    # 표시가 없어 `targets: {en: ...}`만 망가진 경우와 구별할 수 없었다.
    glossary = tmp_path / "g.yaml"
    glossary.write_text("entries가 없다\n", encoding="utf-8")
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en,ja",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--glossary",
            str(glossary),
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 66
    failure_lines = [line for line in result.output.splitlines() if "용어집을 읽지 못했다" in line]
    assert any(line.startswith("[en] ") for line in failure_lines)
    assert any(line.startswith("[ja] ") for line in failure_lines)


# ── Task 5 종료 ────────────────────────────────────────────────────────


def test_대문자_언어_태그도_치환한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "ep01.KO.srt"
    source.write_bytes((_FIXTURES / "minimal.srt").read_bytes())
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(
        app,
        [
            "translate",
            str(source),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--source-lang",
            "ko",
            "--base-url",
            "http://h/v1",
            "--model",
            "m1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "ep01.en.srt").exists()
    assert not (tmp_path / "ep01.KO.en.srt").exists()
