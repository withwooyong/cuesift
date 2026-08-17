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


# ── 리뷰 라운드 1 (Critical 1) ──────────────────────────────────────────


def test_dry_run은_경고하고_실제로_실행한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --dry-run이 아무것도 하지 않으면 사용자는 견적만 뽑았다고 믿는데
    # 실제로는 프로바이더가 호출되고 파일이 덮인다. Task 6 전까지는 최소한
    # "안 됐다"를 큰 소리로 말해야 한다.
    _patch_provider(monkeypatch, EchoProvider())

    result = runner.invoke(app, [*_args(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output
    assert (tmp_path / "minimal.en.srt").exists()  # 조용히 무시되지 않고 실제로 실행됐다


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


# ── 리뷰 라운드 1 (Critical 3) — write_subtitle 예외 누수 2종 ──────────


def test_출력_디렉터리_자리가_이미_파일이면_exit_2다(tmp_path: Path) -> None:
    # --out에 file_okay=False를 걸어 typer가 본문 전에 거른다 - write_subtitle의
    # mkdir(parents=True, exist_ok=True)가 FileExistsError를 내기 전에 막는다.
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


def test_유효하지_않은_언어_태그는_exit_2다(tmp_path: Path) -> None:
    # 'e:n'은 NTFS 대체 데이터 스트림으로 해석되고, 나머지 둘은 --out 밖에
    # 쓰거나 exit 1로 새는 경로 조작이다. 셋 다 검증 없이 파일 경로 조각이
    # 되기 전에 막아야 한다.
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
