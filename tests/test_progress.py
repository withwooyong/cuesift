"""진행 표시와 비대화형 감지 (FR-8.5)."""

from __future__ import annotations

import io
import os

import pytest

from cuesift.progress import (
    ProgressReporter,
    ProgressUpdate,
    detect_style,
    env_flag,
    resolve_style,
)


def test_진행_표시가_모든_테스트에서_기본으로_꺼져_있다() -> None:
    # **이 단언이 픽스처의 게이트다.** 픽스처가 사라지면 진행 줄이 기존
    # stderr 단언에 섞여 수십 건이 한꺼번에 죽는데, 그때 원인은 진행
    # 표시가 아니라 각 테스트의 문제처럼 보인다 (설계 §9 R2).
    assert os.environ["CUESIFT_PROGRESS"] == "0"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        (" on ", True),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("off", False),
        ("", False),
        ("   ", False),
    ],
)
def test_환경변수를_3상으로_읽는다(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    # 판독 규칙이 두 곳에 생기면 `CUESIFT_PROGRESS=false`가 참이 되는 날이
    # 온다 (설계 §5). 이 표가 그 규칙의 단일 출처다.
    monkeypatch.setenv("CUESIFT_TEST_FLAG", raw)
    assert env_flag("CUESIFT_TEST_FLAG") is expected


def test_환경변수가_없으면_None이다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUESIFT_TEST_FLAG", raising=False)
    assert env_flag("CUESIFT_TEST_FLAG") is None


class _FakeTTY(io.StringIO):
    """`isatty()`를 조작할 수 있는 스트림."""

    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        # **`super()`를 먼저 부른다.** 닫힌 스트림에서 `ValueError`가 나는 것이
        # 실물의 동작이고, 그것이 `test_닫힌_스트림은_plain이다`가 재는 계약이다.
        # 곧장 `self._tty`를 내면 닫아도 `interactive`가 나와 가짜가 실물보다
        # 관대해진다 - 그러면 `detect_style`의 `except ValueError` 갈래를
        # 아무도 검사하지 않는다.
        super().isatty()
        return self._tty


@pytest.mark.parametrize(
    ("tty", "ci", "term", "expected"),
    [
        (True, None, None, "interactive"),
        (False, None, None, "plain"),
        # **TTY를 주는 CI가 이 표의 존재 이유다.** `isatty`만 보면
        # `docker run -t`나 일부 self-hosted 러너에서 `\r`이 로그 파일에
        # 그대로 남는다 (설계 D8).
        (True, "true", None, "plain"),
        (True, None, "dumb", "plain"),
        (True, "1", "dumb", "plain"),
        (False, "true", "dumb", "plain"),
        # `CI=`(빈 문자열)는 세우지 않은 것과 같다. GitHub Actions는
        # `CI=true`를 세우고, 빈 값을 비대화형으로 읽으면 로컬에서
        # `CI=`로 지운 사용자가 갱신을 못 받는다.
        (True, "", None, "interactive"),
        (True, None, "xterm-256color", "interactive"),
    ],
)
def test_감지_진리표(
    monkeypatch: pytest.MonkeyPatch,
    tty: bool,
    ci: str | None,
    term: str | None,
    expected: str,
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    if ci is not None:
        monkeypatch.setenv("CI", ci)
    if term is not None:
        monkeypatch.setenv("TERM", term)
    assert detect_style(_FakeTTY(tty)) == expected


def test_닫힌_스트림은_plain이다(monkeypatch: pytest.MonkeyPatch) -> None:
    # 닫힌 스트림의 `isatty()`는 `ValueError`를 낸다. 제어문자를 쓰지 않는
    # 쪽이 안전하다.
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    stream = _FakeTTY(True)
    stream.close()
    assert detect_style(stream) == "plain"


@pytest.mark.parametrize(
    ("enabled", "tty", "expected"),
    [
        (False, True, "off"),
        (False, False, "off"),
        (True, True, "interactive"),
        # **플래그는 켜고 끄기만 정한다** (설계 D7). `--progress`를 CI에서
        # 줘도 `\r`이 아니라 이정표 줄이 나와야 한다.
        (True, False, "plain"),
        (None, True, "interactive"),
        (None, False, "plain"),
    ],
)
def test_스타일은_언제나_감지가_정한다(
    monkeypatch: pytest.MonkeyPatch, enabled: bool | None, tty: bool, expected: str
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    assert resolve_style(enabled, _FakeTTY(tty)) == expected


def test_interactive는_한_줄을_덮어쓴다() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter("interactive", stream)
    reporter.phase("[en] 번역")
    reporter.update(ProgressUpdate(41, 412))
    reporter.update(ProgressUpdate(340, 412))
    reporter.done("완료 (실패 0)")
    text = stream.getvalue()
    assert text.count("\n") == 1
    assert "\r" in text
    assert "41/412 (9%)" in text
    assert "340/412 (82%)" in text
    # **확정 줄에도 패딩이 붙는다.** `완료 (실패 0)`은 앞선 `340/412 (82%)`보다
    # 짧아서, 밀어 내지 않으면 개행 뒤에도 이전 줄 꼬리가 화면에 남는다
    # (`test_짧아진_줄이_이전_글자를_남기지_않는다`와 같은 이유다). 그래서
    # 끝나는 것은 `완료 (실패 0)`가 아니라 공백까지 포함한 줄이다.
    assert text.endswith("\n")
    assert text.rstrip("\n").rstrip(" ").endswith("완료 (실패 0)")


def test_짧아진_줄이_이전_글자를_남기지_않는다() -> None:
    # `1000/4120` 뒤에 `340/412`가 오면 앞선 줄의 꼬리가 남는다.
    # 패딩이 없으면 `340/412 (82%)0`처럼 보인다.
    stream = io.StringIO()
    reporter = ProgressReporter("interactive", stream)
    reporter.phase("[en] 번역")
    reporter.update(ProgressUpdate(1000, 4120))
    long_len = len(stream.getvalue().rsplit("\r", 1)[-1])
    reporter.update(ProgressUpdate(340, 412))
    short = stream.getvalue().rsplit("\r", 1)[-1]
    assert len(short) == long_len
    assert short.rstrip().endswith("(82%)")


def test_plain은_10퍼센트포인트마다_낸다() -> None:
    # 배치마다 내면 4000세그먼트에서 언어당 수백 줄이 된다 (설계 D12).
    stream = io.StringIO()
    reporter = ProgressReporter("plain", stream)
    reporter.phase("[en] 번역")
    for done in range(1, 101):
        reporter.update(ProgressUpdate(done, 100))
    lines = stream.getvalue().splitlines()
    assert len(lines) == 10
    assert lines[0] == "[en] 번역 10/100 (10%)"
    assert lines[-1] == "[en] 번역 100/100 (100%)"
    assert "\r" not in stream.getvalue()


def test_plain은_100퍼센트를_항상_낸다() -> None:
    # 10%p 규칙만 두면 마지막 조각이 10%p에 못 미칠 때 진행이 97%에서
    # 끝난 것처럼 보인다 (설계 D13).
    stream = io.StringIO()
    reporter = ProgressReporter("plain", stream)
    reporter.phase("[en] Tier 1")
    reporter.update(ProgressUpdate(97, 100))
    reporter.update(ProgressUpdate(100, 100))
    lines = stream.getvalue().splitlines()
    assert lines[-1] == "[en] Tier 1 100/100 (100%)"


def test_off는_아무것도_쓰지_않는다() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter("off", stream)
    reporter.phase("[en] 번역")
    reporter.update(ProgressUpdate(1, 2))
    reporter.done()
    reporter.clear()
    assert stream.getvalue() == ""


def test_clear는_떠_있는_줄을_지운다() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter("interactive", stream)
    reporter.phase("[en] 번역")
    reporter.update(ProgressUpdate(1, 2))
    painted = len(stream.getvalue().rsplit("\r", 1)[-1])
    reporter.clear()
    tail = stream.getvalue().split("\r")[-2:]
    assert tail[0] == " " * painted
    assert tail[1] == ""


def test_clear는_두_번_불러도_한_번만_지운다() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter("interactive", stream)
    reporter.phase("[en] 번역")
    reporter.update(ProgressUpdate(1, 2))
    reporter.clear()
    before = stream.getvalue()
    reporter.clear()
    assert stream.getvalue() == before


class _BrokenStream(io.StringIO):
    """첫 쓰기에서 닫힌 파이프를 흉내 낸다."""

    def write(self, s: str) -> int:  # type: ignore[override]
        raise OSError(32, "Broken pipe")


def test_쓰기_실패는_전파되지_않고_영구_비활성화한다() -> None:
    # 진행 표시는 부수적이다. 닫힌 파이프에서 예외가 새면 `_TolerantOutput`과
    # `_echo`가 지켜 온 종료 코드 계약이 깨진다 (설계 D10).
    reporter = ProgressReporter("interactive", _BrokenStream())
    reporter.phase("[en] 번역")
    reporter.update(ProgressUpdate(1, 2))
    reporter.done()
    reporter.clear()
    assert reporter.disabled is True


def test_총량이_0이면_나누지_않는다() -> None:
    # 세그먼트 0개짜리 자막은 실재한다(빈 파일). ZeroDivisionError로
    # 죽으면 번역이 아니라 진행 표시가 파이프라인을 무너뜨린다.
    stream = io.StringIO()
    reporter = ProgressReporter("plain", stream)
    reporter.phase("[en] 번역")
    reporter.update(ProgressUpdate(0, 0))
    assert stream.getvalue() == ""


def test_전역_리포터는_기본이_없다() -> None:
    from cuesift import progress

    assert progress.active() is None


def test_echo가_쓰기_전에_진행_줄을_지운다() -> None:
    # `\r` 진행 줄이 떠 있는 중에 경고가 나가면 두 문장이 한 줄에 겹친다
    # (설계 D11). `_translate_one`은 용어집 실패·캐시 경고를 그 자리에서 낸다.
    from cuesift import progress
    from cuesift.cli import _echo

    stream = io.StringIO()
    reporter = ProgressReporter("interactive", stream)
    reporter.phase("[en] 번역")
    reporter.update(ProgressUpdate(340, 412))
    painted = len(stream.getvalue().rsplit("\r", 1)[-1])

    progress.install(reporter)
    try:
        _echo("[en] 용어집을 읽지 못했다", err=True)
    finally:
        progress.install(None)

    # 진행 줄을 공백으로 밀어 낸 흔적이 있어야 한다.
    assert "\r" + " " * painted + "\r" in stream.getvalue()


def test_echo는_stdout_경로에서도_지운다() -> None:
    # 대화형 터미널에서 stdout과 stderr는 같은 tty다. `_tier1_warn`은
    # 의도적으로 stdout으로 나간다(cli.py `_tier1_warn` 독스트링).
    from cuesift import progress
    from cuesift.cli import _echo

    stream = io.StringIO()
    reporter = ProgressReporter("interactive", stream)
    reporter.phase("[en] Tier 1")
    reporter.update(ProgressUpdate(1, 20))
    before = stream.getvalue()

    progress.install(reporter)
    try:
        _echo("[en] Tier 1이 돌지 않았다: 후보 0건")
    finally:
        progress.install(None)

    assert stream.getvalue() != before


def test_리포터가_없으면_clear는_무해하다() -> None:
    from cuesift import progress

    progress.install(None)
    progress.clear_active()  # 예외가 없어야 한다
