"""`cuesift translate --media` 배선 검증 (FR-8.3 · 설계 §5.2).

**네트워크를 타지 않는다.** `_build_provider`(번역)와 `_build_stt_provider`
(전사)를 둘 다 monkeypatch한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fakes.provider import EchoProvider
from tests.fakes.stt import FakeSttProvider
from typer.testing import CliRunner

from cuesift.cli import app

_FIXTURES = Path(__file__).parent / "fixtures" / "ingest"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CUESIFT_BASE_URL",
        "CUESIFT_MODEL",
        "CUESIFT_API_KEY",
        "CUESIFT_STT_BASE_URL",
        "CUESIFT_STT_MODEL",
        "CUESIFT_STT_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def media(tmp_path: Path) -> Path:
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"not really a video")
    return path


def _patch_both(monkeypatch: pytest.MonkeyPatch, stt: object) -> None:
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: EchoProvider())
    monkeypatch.setattr("cuesift.cli._build_stt_provider", lambda **_: stt)


def _args(media: Path, *extra: str) -> list[str]:
    return [
        "translate",
        "--media",
        str(media),
        "--to",
        "en",
        "--base-url",
        "http://localhost:11434/v1",
        "--model",
        "m",
        "--stt-base-url",
        "http://localhost:9000/v1",
        "--stt-model",
        "whisper-1",
        *extra,
    ]


def test_두_파일이_나간다(monkeypatch: pytest.MonkeyPatch, media: Path) -> None:
    """G2. 원형은 `talk.ko.mp4`·`talk.en.mp4`를 낸다."""
    _patch_both(monkeypatch, FakeSttProvider([(0.0, 1.0, "안녕하세요")]))

    result = runner.invoke(app, _args(media))

    assert result.exit_code == 0, result.output
    assert (media.parent / "talk.ko.srt").is_file()
    assert (media.parent / "talk.en.srt").is_file()
    assert not (media.parent / "talk.ko.mp4").exists()
    assert not (media.parent / "talk.en.mp4").exists()


def test_없는_자막_경로는_여전히_종료_코드_2다() -> None:
    """G3. `exists=True`를 본문 검증으로 옮기다가 66으로 흘리면 죽는다.

    **여기가 어긋나면 CI에서 경로 오타가 "파일 사정(66)"으로 보고되고**
    사용자는 멀쩡한 자막을 고치려 든다.
    """
    result = runner.invoke(
        app,
        [
            "translate",
            "없는파일.srt",
            "--to",
            "en",
            "--base-url",
            "http://localhost:11434/v1",
            "--model",
            "m",
        ],
    )

    assert result.exit_code == 2, result.output


def test_자막과_media를_함께_주면_종료_코드_2다(media: Path) -> None:
    result = runner.invoke(app, _args(media, str(_FIXTURES / "minimal.srt")))

    assert result.exit_code == 2


def test_둘_다_없으면_종료_코드_2다() -> None:
    result = runner.invoke(
        app,
        ["translate", "--to", "en", "--base-url", "http://localhost:11434/v1", "--model", "m"],
    )

    assert result.exit_code == 2


def test_dry_run과_media는_함께_쓸_수_없다(media: Path) -> None:
    """**`--dry-run`이 네트워크를 타지 않는다는 계약에 예외를 두지 않는다**(NFR-2).

    전사 없이는 세그먼트 수를 셀 수 없고, 전사하면 dry-run이 돈을 쓴다 -
    사용자가 무료라고 믿고 반복 호출하는 바로 그 명령이다.
    """
    result = runner.invoke(app, _args(media, "--dry-run"))

    assert result.exit_code == 2
    # 대안을 안내한다. 막기만 하면 사용자는 무엇을 해야 하는지 모른다.
    assert "transcribe" in result.stderr


def test_기존_자막_입력_경로가_그대로_동작한다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R1. 회귀 범위가 STT가 아니라 **기존 번역 경로 전체**다."""
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: EchoProvider())

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
            "http://localhost:11434/v1",
            "--model",
            "m",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "minimal.en.srt").is_file()


def test_전사_자막을_재사용하면_프로바이더를_부르지_않는다(
    monkeypatch: pytest.MonkeyPatch, media: Path
) -> None:
    """D2가 `--media` 경로에서도 같은 헬퍼로 걸린다(D5)."""
    (media.parent / "talk.ko.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n손으로 고친 원문\n", encoding="utf-8"
    )
    stt = FakeSttProvider([(0.0, 1.0, "기계 전사")])
    _patch_both(monkeypatch, stt)

    result = runner.invoke(app, _args(media))

    assert result.exit_code == 0, result.output
    assert stt.calls == []


def test_out이_전사_자막과_번역_자막_모두에_걸린다(
    monkeypatch: pytest.MonkeyPatch, media: Path, tmp_path: Path
) -> None:
    _patch_both(monkeypatch, FakeSttProvider([(0.0, 1.0, "안녕")]))
    out = tmp_path / "subs"

    result = runner.invoke(app, _args(media, "--out", str(out)))

    assert result.exit_code == 0, result.output
    assert (out / "talk.ko.srt").is_file()
    assert (out / "talk.en.srt").is_file()


def test_STT_설정이_없으면_종료_코드_2다(media: Path) -> None:
    result = runner.invoke(
        app,
        [
            "translate",
            "--media",
            str(media),
            "--to",
            "en",
            "--base-url",
            "http://localhost:11434/v1",
            "--model",
            "m",
        ],
    )

    assert result.exit_code == 2
    assert "--stt-base-url" in result.stderr


def test_잘못된_대상_언어는_전사_전에_거부된다(
    monkeypatch: pytest.MonkeyPatch, media: Path
) -> None:
    """**전사는 targets 검증 뒤에 일어난다.**

    먼저 전사하면 `--to`에 오타를 낸 사용자가 STT 요금을 낸 뒤 exit 2를
    받는다. 기존 코드가 프로파일 검사를 LLM 호출 전에 두는 것(설계 D13)과
    같은 규율이다.
    """
    stt = FakeSttProvider([(0.0, 1.0, "안녕")])
    _patch_both(monkeypatch, stt)

    result = runner.invoke(
        app,
        [
            "translate",
            "--media",
            str(media),
            "--to",
            "en/ko",
            "--base-url",
            "http://localhost:11434/v1",
            "--model",
            "m",
            "--stt-base-url",
            "http://localhost:9000/v1",
            "--stt-model",
            "whisper-1",
        ],
    )

    assert result.exit_code == 2
    assert stt.calls == []


def test_설정의_media가_없는_경로여도_명령줄_자막이_이긴다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """리뷰 지적(HIGH). FR-8.4 후반절이 typer 레이어로 우회당한다.

    click은 `default_map`이 채운 값에도 `exists=True`를 적용한다. 그래서
    설정에 적힌 영상이 없으면 **`--media`를 친 적도 없는 사용자가**
    `Invalid value for '--media'`로 exit 2를 받는다 - 양보 로직이 돌기
    전에 터지므로 "명령줄이 이긴다"가 발동하지 못한다.

    `media`는 설계상 **버려질 수 있는 값**이라 `input`과 사정이 같다.
    존재 검사는 양보가 끝난 뒤 본문에서 한다.
    """
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: EchoProvider())
    cfg = tmp_path / "cuesift.yaml"
    cfg.write_text("input:\n  media: 없는영상.mp4\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--config",
            str(cfg),
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://localhost:11434/v1",
            "--model",
            "m",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "minimal.en.srt").is_file()


def test_없는_media_경로는_종료_코드_2다(tmp_path: Path) -> None:
    """`exists=True`를 본문으로 옮겨도 종료 코드는 2에서 움직이지 않는다.

    **이 테스트는 그 이동의 게이트가 아니다**(재리뷰 지적). `exists=True`를
    되돌리는 변이에서도 click이 exit 2를 내므로 통과한다. 이동을 고정하는
    것은 `test_설정의_media가_없는_경로여도_명령줄_자막이_이긴다` 하나이고,
    여기가 막는 것은 **종료 코드가 66이나 1로 흘러가는 회귀**다. 게이트가
    무엇을 재는지 적어 두지 않으면 다음 사람이 이것을 근거로 안심한다.
    """
    result = runner.invoke(
        app,
        [
            "translate",
            "--media",
            str(tmp_path / "없다.mp4"),
            "--to",
            "en",
            "--base-url",
            "http://localhost:11434/v1",
            "--model",
            "m",
            "--stt-base-url",
            "http://localhost:9000/v1",
            "--stt-model",
            "whisper-1",
        ],
    )

    assert result.exit_code == 2, result.output


def test_용어집_오타는_전사_전에_걸린다(monkeypatch: pytest.MonkeyPatch, media: Path) -> None:
    """리뷰 지적(MEDIUM). 되돌릴 수 없는 비용이 앞서면 안 된다.

    용어집 선검사가 `if dry_run:` 안에만 있었는데 `--media`는 `--dry-run`과
    금지돼 있어 **그 선검사가 영원히 실행되지 않았다.** 그래서 용어집 경로에
    오타를 낸 사용자가 전사 요금을 낸 뒤 exit 66을 받는다.
    """
    stt = FakeSttProvider([(0.0, 1.0, "안녕")])
    _patch_both(monkeypatch, stt)

    result = runner.invoke(app, _args(media, "--glossary", "없는용어집.yaml"))

    assert result.exit_code == 66, result.output
    assert stt.calls == []
    assert "전사 중" not in result.stderr


def test_설정의_media가_디렉터리여도_명령줄_자막이_이긴다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """재리뷰 지적(LOW). `dir_okay=False`도 양보보다 먼저 터진다.

    `exists=True`와 **같은 실패 모드의 좁은 잔재**다. 둘 다 typer 층에서
    판정하므로 설정에서 온 값이 버려지기 전에 사용자를 막는다. 하나만
    떼면 결함의 절반이 남는다.
    """
    monkeypatch.setattr("cuesift.cli._build_provider", lambda **_: EchoProvider())
    (tmp_path / "영상디렉터리").mkdir()
    cfg = tmp_path / "cuesift.yaml"
    cfg.write_text(f"input:\n  media: {tmp_path / '영상디렉터리'}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--config",
            str(cfg),
            "translate",
            str(_FIXTURES / "minimal.srt"),
            "--to",
            "en",
            "--out",
            str(tmp_path),
            "--base-url",
            "http://localhost:11434/v1",
            "--model",
            "m",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "minimal.en.srt").is_file()
