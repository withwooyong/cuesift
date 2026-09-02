"""`cuesift transcribe` 배선 검증 (FR-8.3 · 설계 §4.2·§5).

**네트워크를 타지 않는다.** `_build_stt_provider`를 monkeypatch해 가짜를
꽂는다 - `_build_provider`를 꽂는 `test_cli_translate.py`의 형제다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fakes.stt import FakeSttProvider, SequenceSttProvider
from typer.testing import CliRunner

from cuesift import cli
from cuesift.cli import app
from cuesift.stt.provider import Transcript, TranscriptCue
from cuesift.translate.provider import FatalProviderError, RetryableProviderError

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """사용자 환경이 테스트 결과를 바꾸지 못하게 한다."""
    for name in (
        "CUESIFT_STT_BASE_URL",
        "CUESIFT_STT_MODEL",
        "CUESIFT_STT_API_KEY",
        "CUESIFT_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def _patch_stt(monkeypatch: pytest.MonkeyPatch, provider: object) -> None:
    monkeypatch.setattr("cuesift.cli._build_stt_provider", lambda **_: provider)


@pytest.fixture
def media(tmp_path: Path) -> Path:
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"not really a video")
    return path


def _args(media: Path, *extra: str) -> list[str]:
    return [
        "transcribe",
        str(media),
        "--stt-base-url",
        "http://localhost:9000/v1",
        "--stt-model",
        "whisper-1",
        *extra,
    ]


def test_출력이_ko_srt다(monkeypatch: pytest.MonkeyPatch, media: Path) -> None:
    """G1. `suffix` 인자가 없던 원형은 `talk.ko.mp4`를 낸다.

    **확장자만 다르고 예외는 없다** - 플레이어가 열지 못하는 파일이 생기고
    종료 코드는 0이다.
    """
    _patch_stt(monkeypatch, FakeSttProvider([(0.0, 1.0, "안녕하세요")]))

    result = runner.invoke(app, _args(media))

    assert result.exit_code == 0, result.output
    assert (media.parent / "talk.ko.srt").is_file()
    assert not (media.parent / "talk.ko.mp4").exists()


def test_종료_코드가_70이_아니다(monkeypatch: pytest.MonkeyPatch, media: Path) -> None:
    """G7. 스텁이 남아 있으면 죽는다."""
    _patch_stt(monkeypatch, FakeSttProvider([(0.0, 1.0, "안녕")]))

    result = runner.invoke(app, _args(media))

    assert result.exit_code != 70, result.output


def test_이미_있는_자막을_재사용하고_프로바이더를_부르지_않는다(
    monkeypatch: pytest.MonkeyPatch, media: Path
) -> None:
    """G4. 설계 D2.

    **`provider.calls == []`는 결과 확인으로 대체되지 않는다** - 전사하고
    버려도 결과 파일은 같다. 호출이 일어나지 않았음을 직접 봐야 한다.
    """
    existing = media.parent / "talk.ko.srt"
    existing.write_text("1\n00:00:01,000 --> 00:00:02,000\n손으로 고친 원문\n", encoding="utf-8")
    provider = FakeSttProvider([(0.0, 1.0, "기계 전사")])
    _patch_stt(monkeypatch, provider)

    result = runner.invoke(app, _args(media))

    assert result.exit_code == 0, result.output
    assert provider.calls == []
    # 덮어쓰면 사용자가 손으로 고친 원문이 예고 없이 사라진다.
    assert "손으로 고친 원문" in existing.read_text(encoding="utf-8")
    # 조용히 재사용하지 않는다. 알림 줄이 유일한 방어다(설계 R2).
    assert "재사용" in result.stderr


def test_out_디렉터리로_낸다(monkeypatch: pytest.MonkeyPatch, media: Path, tmp_path: Path) -> None:
    _patch_stt(monkeypatch, FakeSttProvider([(0.0, 1.0, "안녕")]))
    out = tmp_path / "subs"

    result = runner.invoke(app, _args(media, "--out", str(out)))

    assert result.exit_code == 0, result.output
    assert (out / "talk.ko.srt").is_file()


def test_source_lang이_출력_이름과_프로바이더에_함께_간다(
    monkeypatch: pytest.MonkeyPatch, media: Path
) -> None:
    provider = FakeSttProvider([(0.0, 1.0, "こんにちは")], language="ja")
    _patch_stt(monkeypatch, provider)

    result = runner.invoke(app, _args(media, "--source-lang", "ja"))

    assert result.exit_code == 0, result.output
    assert (media.parent / "talk.ja.srt").is_file()
    assert provider.languages == ["ja"]


def test_STT_설정이_없으면_종료_코드_2다(media: Path) -> None:
    result = runner.invoke(app, ["transcribe", str(media)])

    assert result.exit_code == 2
    # 오류 메시지가 두 통로를 모두 적는다(설계 R4).
    assert "--stt-base-url" in result.stderr
    assert "CUESIFT_STT_BASE_URL" in result.stderr


def test_환경변수로도_설정된다(monkeypatch: pytest.MonkeyPatch, media: Path) -> None:
    monkeypatch.setenv("CUESIFT_STT_BASE_URL", "http://localhost:9000/v1")
    monkeypatch.setenv("CUESIFT_STT_MODEL", "whisper-1")
    _patch_stt(monkeypatch, FakeSttProvider([(0.0, 1.0, "안녕")]))

    result = runner.invoke(app, ["transcribe", str(media)])

    assert result.exit_code == 0, result.output


def test_재시도_소진은_종료_코드_69다(monkeypatch: pytest.MonkeyPatch, media: Path) -> None:
    """외부 서비스가 요청을 거부한 것이다. 66(파일 사정)과 갈린다."""
    _patch_stt(monkeypatch, SequenceSttProvider([RetryableProviderError("429")]))
    # 실제로 기다리지 않는다.
    monkeypatch.setattr("cuesift.stt.retry.time.sleep", lambda _: None)

    result = runner.invoke(app, _args(media))

    assert result.exit_code == 69, result.output


def test_인증_실패도_종료_코드_69다(monkeypatch: pytest.MonkeyPatch, media: Path) -> None:
    _patch_stt(monkeypatch, SequenceSttProvider([FatalProviderError("401 unauthorized")]))

    result = runner.invoke(app, _args(media))

    assert result.exit_code == 69, result.output


def test_없는_영상은_종료_코드_2다(tmp_path: Path) -> None:
    result = runner.invoke(app, _args(tmp_path / "없다.mp4"))

    assert result.exit_code == 2


def test_재시도_알림이_stderr로_나간다(monkeypatch: pytest.MonkeyPatch, media: Path) -> None:
    """설계 D4. 사용자가 알고 싶은 것은 진행률이 아니라
    "멈춰 있는 것인가 기다리는 것인가"다."""
    _patch_stt(
        monkeypatch,
        SequenceSttProvider(
            [
                RetryableProviderError("429 rate limited", retry_after_s=5.0),
                Transcript(
                    cues=(TranscriptCue(start_s=0.0, end_s=1.0, text="안녕"),),
                    language="ko",
                    model="fake",
                ),
            ]
        ),
    )
    monkeypatch.setattr("cuesift.stt.retry.time.sleep", lambda _: None)

    result = runner.invoke(app, _args(media))

    assert result.exit_code == 0, result.output
    assert "전사 중" in result.stderr
    assert "5.0" in result.stderr
    assert "2/4" in result.stderr


def test_STT_키를_빈_문자열로_두면_LLM_키가_폴백되지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """리뷰 지적(HIGH). **폴백을 끌 수 있어야 한다.**

    `or`로 쓰면 `CUESIFT_STT_API_KEY=""`가 "없음"과 뭉뚱그려져 번역용 키가
    **다른 호스트인** STT 엔드포인트의 `Authorization` 헤더에 실린다. 키가
    필요 없는 로컬 whisper를 쓰려는 사용자에게 차단 수단이 없어진다.

    빈 문자열을 헤더에 싣지 않는 것(원래의 근거)은 그대로다 - 반환이
    `None`이어야 하고 `""`이면 안 된다.
    """
    monkeypatch.setenv("CUESIFT_STT_API_KEY", "")
    monkeypatch.setenv("CUESIFT_API_KEY", "sk-llm-secret")
    monkeypatch.setenv("CUESIFT_STT_BASE_URL", "http://localhost:9000/v1")
    monkeypatch.setenv("CUESIFT_STT_MODEL", "whisper-1")

    _, _, api_key = cli._resolve_stt(None, None, None)

    assert api_key is None


def test_STT_키가_없으면_LLM_키로_폴백한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """폴백 자체는 유지한다. 같은 조직의 키를 두 번 쓰게 하지 않는다."""
    monkeypatch.delenv("CUESIFT_STT_API_KEY", raising=False)
    monkeypatch.setenv("CUESIFT_API_KEY", "sk-shared")
    monkeypatch.setenv("CUESIFT_STT_BASE_URL", "http://localhost:9000/v1")
    monkeypatch.setenv("CUESIFT_STT_MODEL", "whisper-1")

    _, _, api_key = cli._resolve_stt(None, None, None)

    assert api_key == "sk-shared"


def test_전사_자막_자리에_디렉터리가_있으면_재사용하지_않는다(
    monkeypatch: pytest.MonkeyPatch, media: Path
) -> None:
    """리뷰 지적(MEDIUM). `exists()`는 디렉터리에도 참이다.

    **전사를 한 번도 하지 않고 exit 0으로 디렉터리 경로를 stdout에 찍으면
    파이프 계약이 깨진다** - `cuesift transcribe talk.mp4 | xargs ...`가
    자막이 아닌 것을 받는다. 쓸 수 없는 자리라는 사실은 `write_subtitle`이
    내는 66으로 드러나야 한다.
    """
    (media.parent / "talk.ko.srt").mkdir()
    provider = FakeSttProvider([(0.0, 1.0, "안녕")])
    _patch_stt(monkeypatch, provider)

    result = runner.invoke(app, _args(media))

    # 66은 "파일 사정"이다. 쓸 수 없는 자리라는 사실이 그것으로 드러난다.
    assert result.exit_code == 66, result.output
    # **전사는 실제로 일어나야 한다.** 재사용으로 건너뛰면 호출이 0회다.
    assert len(provider.calls) == 1
    # `tmp_path`가 이 테스트의 한글 이름을 품고 있어 "재사용" 두 글자만
    # 보면 경로 문자열에 걸린다. 알림 줄을 통째로 본다.
    assert "이미 있어 재사용한다" not in result.stderr
