"""하류가 파이프를 먼저 닫아도 종료 코드 계약이 유지되는지 (설계 §7.1·FR-8.2).

**`CliRunner`로는 이 결함을 절대 잡을 수 없다** — 인메모리 스트림이라 파이프를 만들지
않는다. 그래서 나머지 스위트가 전부 초록인 채로 `cuesift check x.srt --spec ko | head -20`이
종료 코드 **120**을 냈다. 실제 프로세스를 띄우고 하류를 먼저 죽여야 재현된다.

`PYTHONUNBUFFERED=1`을 함께 보는 이유는 그것이 **Docker 파이썬 이미지와 다수 CI의
기본값**이기 때문이다. 버퍼링 여부에 따라 실패 지점이 달라져 증상이 120과 1로 갈린다.

아래쪽 단위 테스트들은 같은 주제를 **프로세스 안에서** 본다. 서브프로세스 테스트는
계약(종료 코드)을 보지만 커버리지 도구에 잡히지 않고, 무엇보다 **"삼키면 안 되는 오류"**
(디스크 가득 참)를 전혀 지나가지 않는다. 두 층이 서로 다른 것을 검증한다.
"""

from __future__ import annotations

import errno
import io
import os
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from cuesift import cli
from cuesift.cli import _discard_stream, _echo, _is_closed_output, run

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"

# `-c`로 진입점 함수를 직접 부른다. 설치된 `cuesift` 실행 파일을 찾아 쓰면 PATH·플랫폼별
# 파일명(`.exe`)에 의존해 CI에서 조용히 skip될 수 있고, skip된 게이트는 없는 게이트다.
# 실행 파일이 `run`을 가리키는지는 아래 메타데이터 테스트가 따로 고정한다.
_BOOTSTRAP = "from cuesift.cli import run; run()"


def _exit_code_with_a_closed_pipe(
    args: list[str], *, merge_stderr: bool = False, unbuffered: bool = False
) -> int:
    """하류가 **이미 죽은** 파이프에 물려 CLI를 돌리고 종료 코드를 돌려준다.

    하류를 먼저 `wait()`로 거둔 뒤에 상류를 띄우는 것이 요점이다. 둘을 동시에 띄우면
    상류가 파이프 버퍼(보통 64KB) 안에서 출력을 끝내고 정상 종료할 수 있어 **경합으로
    테스트가 아무것도 검증하지 않는다.** 읽기 끝이 확실히 닫힌 뒤에 쓰면 크기와 무관하게
    반드시 실패한다.
    """
    env = dict(os.environ)
    # 부모 로케일이 자식 결과를 바꾸지 않도록 고정한다(이 테스트의 주제는 인코딩이 아니다).
    env["PYTHONIOENCODING"] = "utf-8"
    if unbuffered:
        env["PYTHONUNBUFFERED"] = "1"
    else:
        env.pop("PYTHONUNBUFFERED", None)

    downstream = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.PIPE)
    assert downstream.stdin is not None
    downstream.wait()  # 읽기 끝이 닫힌다. 쓰기 끝은 아직 이 프로세스가 들고 있다.

    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _BOOTSTRAP, *args],
            env=env,
            stdout=downstream.stdin,
            stderr=downstream.stdin if merge_stderr else subprocess.DEVNULL,
        )
        return proc.wait(timeout=60)
    finally:
        downstream.stdin.close()


# (라벨, 인자, 기대 종료 코드)
#
# **`merge_stderr`를 케이스마다 고정하지 않는다.** 이전 판은 없는 파일 케이스만
# `merge_stderr=False`로 두고 `transcribe`(70)를 아예 빠뜨려, exit 2와 exit 70이
# **조용한 0**이 되는 회귀를 스위트 전체가 초록인 채로 통과시켰다.
# 그 플래그가 결함을 가리고 있었으므로 이제 모든 케이스에서 양쪽을 다 돈다.
_CONTRACT = [
    ("0 깨끗한 파일", ["check", str(FIXTURES / "minimal.srt"), "--spec", "ko"], 0),
    ("1 위반 있음", ["check", str(FIXTURES / "check_violations.ass"), "--spec", "ko"], 1),
    ("2 없는 파일", ["check", str(FIXTURES / "없는파일.srt"), "--spec", "ko"], 2),
    ("2 없는 프로파일", ["check", str(FIXTURES / "minimal.srt"), "--spec", "th"], 2),
    ("66 자막 아님", ["check", str(FIXTURES / "cp949.srt"), "--spec", "ko"], 66),
    ("70 미구현", ["transcribe", str(FIXTURES / "minimal.srt")], 70),
    (
        "0 --fail-on none",
        ["check", str(FIXTURES / "overlap.vtt"), "--spec", "ko", "--fail-on", "none"],
        0,
    ),
    ("0 --help", ["--help"], 0),
    ("0 --version", ["--version"], 0),
]


@pytest.mark.parametrize("merge_stderr", [False, True], ids=["stderr별도", "2>&1"])
@pytest.mark.parametrize("unbuffered", [False, True], ids=["buffered", "unbuffered"])
@pytest.mark.parametrize(
    ("label", "args", "expected"), _CONTRACT, ids=[case[0] for case in _CONTRACT]
)
def test_closed_pipe_preserves_the_exit_code(
    label: str, args: list[str], expected: int, merge_stderr: bool, unbuffered: bool
):
    """파이프가 닫힌 것은 오류가 아니라 `head`·`less`의 정상 동작이다.

    실측된 두 세대의 증상:

    | 판 | 증상 |
    | --- | --- |
    | 배선 직후 | 버퍼링 **120** · `PYTHONUNBUFFERED=1` **1**. `--fail-on none`도 1 |
    | fix round 1 | 0·1·66은 옳으나 `2>&1`에서 **2와 70이 0** (진입점이 코드를 덮어썼다) |

    **`!= 120`이나 `!= 0`으로 단언하면 안 된다** — 두 세대의 증상이 각각
    정상 코드(1, 0)와 값이 겹쳐 뒤바뀌어도 통과한다.
    """
    code = _exit_code_with_a_closed_pipe(args, merge_stderr=merge_stderr, unbuffered=unbuffered)

    assert code == expected, f"{label}: exit {code} (기대 {expected})"


def test_the_console_script_is_wired_to_run_not_app():
    """`pyproject.toml`이 `cli:app`으로 되돌아가면 `--help | less`가 다시 120을 낸다.

    위 테스트들은 `run`을 직접 부르므로 **배선이 끊겨도 전부 통과한다.**
    설치 메타데이터를 봐야 그 공백이 닫힌다.

    editable 설치 후 `pyproject.toml`만 고치고 재설치하지 않으면 여기가 빨개지는데,
    그때 실제로 설치된 실행 파일도 낡은 상태이므로 **그 실패는 참이다.**
    """
    scripts = {ep.name: ep.value for ep in entry_points(group="console_scripts")}

    assert scripts.get("cuesift") == "cuesift.cli:run", (
        f"진입점이 {scripts.get('cuesift')!r}다. `pip install -e .`를 다시 돌렸는지 확인한다"
    )


def test_closed_pipe_is_recognised_on_both_platforms():
    """POSIX와 Windows가 **다른 예외**를 낸다 — 한쪽만 보면 다른 쪽에서 조용히 안 먹는다.

    실측: Windows는 `BrokenPipeError`가 아니라 평범한 `OSError` errno 22(EINVAL)다.
    `except BrokenPipeError`만 다는 해법이 개발 플랫폼에서 무력한 이유가 이것이다.
    """
    assert _is_closed_output(BrokenPipeError(errno.EPIPE, "Broken pipe")) is True
    assert _is_closed_output(OSError(errno.EINVAL, "Invalid argument")) is True


def test_a_full_disk_is_never_mistaken_for_a_closed_pipe():
    """**삼키면 안 되는 오류를 삼키지 않는지 본다.**

    `except OSError`로 통째로 삼키면 `cuesift check big.srt --spec ko > out.txt`가
    디스크 가득 참(ENOSPC)에서 **잘린 출력을 종료 코드 0으로** 내보낸다 —
    이 저장소가 가장 경계하는 "검사하지 않고 통과하는 게이트"다.

    서브프로세스 테스트는 이 경로를 **하나도 지나가지 않는다.** 파이프만 닫아 보기 때문이다.
    """
    assert _is_closed_output(OSError(errno.ENOSPC, "No space left on device")) is False
    assert _is_closed_output(OSError(errno.EACCES, "Permission denied")) is False


def test_echo_reraises_an_error_that_is_not_a_closed_pipe(monkeypatch):
    """ENOSPC는 그대로 올라가야 한다. 삼키면 잘린 출력이 성공으로 보고된다."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    def raise_enospc(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(cli.typer, "echo", raise_enospc)

    with pytest.raises(OSError) as caught:
        _echo("한 줄")
    assert caught.value.errno == errno.ENOSPC


def test_echo_swallows_a_closed_pipe(monkeypatch):
    """닫힌 파이프는 오류가 아니다 — 예외가 본문을 빠져나가면 종료 코드가 틀어진다.

    `sys.stdout`을 `io.StringIO`로 바꾸는 것은 **의도적이다.** 진짜 `sys.stdout`을 두면
    `_discard_stream`이 pytest의 캡처 fd를 devnull로 덮어써 이후 테스트의 출력이 사라진다.
    `StringIO`는 `fileno()`가 없어 조기 반환하므로 그 사고 없이 분기만 지나간다.
    """
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    def raise_epipe(*args, **kwargs):
        raise BrokenPipeError(errno.EPIPE, "Broken pipe")

    monkeypatch.setattr(cli.typer, "echo", raise_epipe)

    _echo("한 줄")  # 예외가 나지 않으면 통과다


def test_discard_stream_sends_further_writes_to_devnull(tmp_path):
    """`dup2`가 **fd 자체**를 갈아 끼우는지 값으로 확인한다.

    파이썬 객체만 바꾸면 인터프리터의 종료 flush가 원래 fd로 나가 다시 터지고,
    그때 종료 코드가 120으로 덮인다. 여기서는 임시 파일로 확인한다 —
    `sys.stdout`으로 시험하면 pytest 프로세스의 출력이 영구히 사라진다.
    """
    target = tmp_path / "out.txt"
    with target.open("w", encoding="utf-8") as handle:
        handle.write("갈아 끼우기 전")
        handle.flush()
        _discard_stream(handle)
        handle.write("갈아 끼운 뒤")
        handle.flush()

    assert target.read_text(encoding="utf-8") == "갈아 끼우기 전"


def test_discard_stream_ignores_a_stream_without_a_file_descriptor():
    """`CliRunner`의 인메모리 스트림처럼 fd가 없는 것은 건너뛴다.

    `io.StringIO().fileno()`는 `io.UnsupportedOperation`을 내는데 그것은
    `OSError`이자 `ValueError`다. 잡지 않으면 테스트 실행 중에 죽는다.
    """
    _discard_stream(io.StringIO())  # 예외가 나지 않으면 통과다


def test_run_preserves_the_exit_code_that_the_command_chose(monkeypatch):
    """진입점 래퍼가 정상 종료 코드를 건드리면 안 된다."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    monkeypatch.setattr(cli, "app", lambda: (_ for _ in ()).throw(SystemExit(66)))

    with pytest.raises(SystemExit) as caught:
        run()
    assert caught.value.code == 66


def test_run_never_rewrites_an_exit_code_on_a_closed_pipe(monkeypatch):
    """**진입점이 종료 코드를 바꾸면 안 된다.**

    이전 판은 닫힌 파이프를 잡아 `SystemExit(0)`으로 바꿨는데, 그것이 exit 2와 exit 70을
    조용한 0으로 만들었다 — click의 `UsageError.show()`와 `typer.secho`가 커맨드 본문
    **밖에서** 쓰기 때문에 예외가 진입점까지 올라왔기 때문이다.
    120은 시끄럽지만 0은 조용히 CI를 통과시킨다.

    이 테스트는 그 회귀를 고정한다 — 진입점은 예외를 코드로 번역하지 않는다.
    닫힌 파이프는 `_TolerantOutput`이 **쓰기 지점에서** 무해하게 만든다.
    """
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    def raise_epipe():
        raise BrokenPipeError(errno.EPIPE, "Broken pipe")

    monkeypatch.setattr(cli, "app", raise_epipe)

    with pytest.raises(BrokenPipeError):
        run()


def test_run_installs_the_tolerant_proxies(monkeypatch):
    """프록시를 설치하지 않으면 click이 쓰는 경로(exit 2·70)가 다시 열린다."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    seen: dict[str, object] = {}

    def capture():
        seen["stdout"] = sys.stdout
        seen["stderr"] = sys.stderr

    monkeypatch.setattr(cli, "app", capture)
    run()

    assert isinstance(seen["stdout"], cli._TolerantOutput)
    assert isinstance(seen["stderr"], cli._TolerantOutput)


def test_tolerant_output_swallows_a_closed_pipe_but_not_a_full_disk():
    """`ENOSPC`를 삼키면 잘린 출력이 성공으로 보고된다."""
    closed = cli._TolerantOutput(_FlushFails(errno.EPIPE))
    closed.flush()  # 예외가 나지 않아야 한다
    assert closed.downstream_closed is True

    full = cli._TolerantOutput(_FlushFails(errno.ENOSPC))
    with pytest.raises(OSError) as caught:
        full.flush()
    assert caught.value.errno == errno.ENOSPC
    assert full.downstream_closed is False


def test_tolerant_output_gives_up_only_on_its_own_stream():
    """stdout이 파이프이고 stderr가 터미널인 `... | head -1`에서 진단이 사라지면 안 된다.

    이전 판은 한쪽이 실패하면 **양쪽을** devnull로 갈아 끼웠다 —
    `_discard_stream` 독스트링이 "실패한 스트림만"이라고 적었는데 호출부가 어겼다.
    """
    out = cli._TolerantOutput(_FlushFails(errno.EPIPE))
    err = cli._TolerantOutput(io.StringIO())

    out.flush()

    assert out.downstream_closed is True
    assert err.downstream_closed is False
    err.write("진단은 살아 있어야 한다")
    assert err.wrapped.getvalue() == "진단은 살아 있어야 한다"


class _WriteFails(io.StringIO):
    """write에서만 터지는 스트림."""

    def __init__(self, err: int) -> None:
        super().__init__()
        self._err = err

    def write(self, data: str) -> int:
        raise OSError(self._err, os.strerror(self._err))


def test_tolerant_output_swallows_a_closed_pipe_on_write_but_not_a_full_disk():
    """쓰기 지점도 flush와 같은 규칙을 따라야 한다 — 대부분의 출력은 write로 나간다."""
    closed = cli._TolerantOutput(_WriteFails(errno.EPIPE))
    assert closed.write("한 줄") == len("한 줄"), "쓴 만큼 반환해야 호출자가 정상으로 본다"
    assert closed.downstream_closed is True

    full = cli._TolerantOutput(_WriteFails(errno.ENOSPC))
    with pytest.raises(OSError) as caught:
        full.write("한 줄")
    assert caught.value.errno == errno.ENOSPC


def test_tolerant_output_stops_touching_a_stream_it_gave_up_on():
    """포기한 뒤에는 원본을 다시 건드리지 않는다.

    매번 다시 시도하면 큰 리포트에서 실패한 쓰기가 줄 수만큼 반복된다.
    """
    inner = _WriteFails(errno.EPIPE)
    proxy = cli._TolerantOutput(inner)
    proxy.write("첫 줄")  # 여기서 포기한다

    # 포기 후에는 원본 write를 부르지 않으므로 예외가 나지 않아야 한다.
    assert proxy.write("둘째 줄") == len("둘째 줄")
    proxy.flush()


def test_not_implemented_survives_a_closed_stderr(monkeypatch):
    """`typer.secho`가 터져도 **70에 도달해야 한다.**

    실측된 회귀: 이 방어가 없고 진입점 프록시도 없으면 `transcribe ... 2>&1 | head -0`이
    70이 아니라 **조용한 0**으로 나갔다. 프록시가 있으면 여기까지 오지 않지만,
    `app()`을 직접 부르는 호출자는 프록시를 받지 못한다.
    """
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    def raise_epipe(*args, **kwargs):
        raise BrokenPipeError(errno.EPIPE, "Broken pipe")

    monkeypatch.setattr(cli.typer, "secho", raise_epipe)

    # `typer.Exit`는 click 예외라 `SystemExit`이 아니다 — click이 나중에 종료 코드로 바꾼다.
    with pytest.raises(cli.typer.Exit) as caught:
        cli._not_implemented("transcribe")
    assert caught.value.exit_code == cli.EXIT_NOT_IMPLEMENTED


def test_not_implemented_reraises_a_full_disk(monkeypatch):
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    def raise_enospc(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(cli.typer, "secho", raise_enospc)

    with pytest.raises(OSError) as caught:
        cli._not_implemented("transcribe")
    assert caught.value.errno == errno.ENOSPC


def test_tolerant_output_proxies_feature_detection():
    """`isatty`·`encoding`·`fileno`가 통과해야 click과 rich가 정상 동작한다.

    click의 `PacifyFlushWrapper`가 같은 이유로 `__getattr__` 위임을 쓴다.
    """
    inner = io.StringIO()
    proxy = cli._TolerantOutput(inner)

    assert proxy.isatty() is False
    assert proxy.writable() is True
    proxy.write("가")
    assert inner.getvalue() == "가"


class _FlushFails(io.StringIO):
    """flush에서만 터지는 스트림. `fileno()`가 없어 `_discard_stream`은 조기 반환한다."""

    def __init__(self, err: int) -> None:
        super().__init__()
        self._err = err

    def flush(self) -> None:
        raise OSError(self._err, os.strerror(self._err))


def test_a_failing_final_flush_does_not_overwrite_the_exit_code(monkeypatch):
    """**이 경로가 120의 진짜 출처다.**

    방출 지점에서 예외를 삼켜도 버퍼에 남은 것이 종료 시 flush되고, 그것이 터지면
    CPython이 "Exception ignored"를 찍고 **120으로 끝낸다.** 진입점에서 미리 흘려보내고
    실패하면 fd를 갈아 끼워야 커맨드가 고른 코드가 살아남는다.
    """
    monkeypatch.setattr(sys, "stdout", _FlushFails(errno.EPIPE))
    monkeypatch.setattr(sys, "stderr", _FlushFails(errno.EPIPE))
    monkeypatch.setattr(cli, "app", lambda: (_ for _ in ()).throw(SystemExit(1)))

    with pytest.raises(SystemExit) as caught:
        run()
    assert caught.value.code == 1, "종료 flush 실패가 코드를 덮었다"


def test_a_failing_final_flush_still_reports_a_full_disk(monkeypatch):
    """디스크가 찼는데 조용히 끝나면 잘린 출력이 성공으로 보고된다."""
    monkeypatch.setattr(sys, "stdout", _FlushFails(errno.ENOSPC))
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    monkeypatch.setattr(cli, "app", lambda: (_ for _ in ()).throw(SystemExit(0)))

    with pytest.raises(OSError) as caught:
        run()
    assert caught.value.errno == errno.ENOSPC


def test_run_reraises_an_error_that_is_not_a_closed_pipe(monkeypatch):
    """ENOSPC를 0으로 바꾸면 디스크가 찬 CI가 초록으로 통과한다."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    def raise_enospc():
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(cli, "app", raise_enospc)

    with pytest.raises(OSError) as caught:
        run()
    assert caught.value.errno == errno.ENOSPC
