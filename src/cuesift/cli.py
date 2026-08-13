"""cuesift CLI 진입점.

요구사항정의서 §8.1(FR-8.1~8.5)의 커맨드 표면을 정의한다.
`check`는 배선이 끝나 실제로 동작한다. `translate`·`transcribe`는 아직
인자 스키마만 확정한 골격이라 EXIT_NOT_IMPLEMENTED로 종료한다.

**종료 코드 다섯이 서로 겹치지 않는 것이 이 파일의 계약이다.**

| 코드 | 언제 | 근거 |
| --- | --- | --- |
| 0 | 위반 없음, 또는 `--fail-on none` | |
| 1 | 규격 위반 발견 | FR-7.5 |
| 2 | 명령줄이 틀림 (파일 없음·디렉터리·프로파일 해석 실패) | typer 관행 |
| 66 | 파일 내용이 틀림 (자막 아님·utf-8 아님·읽을 수 없음) | `sysexits.h` EX_NOINPUT |
| 70 | 미구현 | |

**1을 진단 실패에 쓰지 않는 것이 핵심이다.** 1은 "규격 위반 발견"이므로
파일을 못 읽은 것을 1로 내면 CI가 "자막이 깨졌다"와 "경로가 틀렸다"에
같은 대응을 하게 되고, 사용자는 멀쩡한 자막을 고치려 든다.
"""

from __future__ import annotations

import errno
import os
import sys
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import IO, Annotated

import typer

from cuesift import __version__
from cuesift.ingest import IngestError, load_subtitle
from cuesift.spec import (
    SpecProfile,
    SpecViolation,
    TrackViolation,
    check_track,
    load_builtin,
    load_profile,
)

# CI가 "미구현"과 "검수 실패"를 구분할 수 있도록 종료 코드를 분리한다.
# `check`는 이제 1을 쓰므로(FR-7.5) 70은 남은 두 골격 커맨드의 표식이다.
EXIT_NOT_IMPLEMENTED = 70

# sysexits.h EX_NOINPUT — 파일 내용이 틀렸다는 뜻이다. 명령줄이 틀린 2와 구분한다.
# CI가 둘을 구분하지 못하면 "경로 오타"와 "자막이 깨졌다"에 같은 대응을 하게 된다.
EXIT_BAD_INPUT = 66

app = typer.Typer(
    name="cuesift",
    # em dash(U+2014)를 쓰지 않는다. 이 문자열은 `--help`로 출력되는데
    # cp949는 U+2014를 인코딩하지 못한다(실측). `·`(U+00B7)는 인코딩되므로 남긴다.
    help="AI 자막 번역·검수 트리아지 엔진. 사람이 정말 봐야 할 자막만 걸러냅니다.",
    no_args_is_help=True,
    add_completion=False,
)


class FailOn(StrEnum):
    """FR-7.5 — 어느 심각도부터 CI를 실패시킬지.

    **v0.1에서 `hard`와 `any`는 같은 결과를 낸다.** 규격 위반 7종이 전부 같은
    등급이기 때문이다(설계 §5.1). 등급을 나누려면 배정의 출처가 필요한데
    1차 출처인 Netflix TTSG에 위반 등급 구분이 없고, 요구사항정의서 §11 R8이
    "출처 없는 수치를 기본값으로 넣지 않음"을 명시한다.

    이름을 `soft`·`never`에서 바꾼 것은 요구사항정의서가 단일 진실 원천이기
    때문이다. `soft`는 v0.1에 존재하지 않는 등급을 가리킨다.
    """

    hard = "hard"
    any = "any"
    none = "none"


def _not_implemented(command: str) -> None:
    # `typer.secho`는 `_echo`를 지나지 않는다. 진입점의 `_TolerantOutput`이 이 경로도
    # 덮지만, 닫힌 파이프에서 여기서 예외가 새면 아래 `typer.Exit(70)`에 도달하지 못해
    # **70이 조용한 0이 된다**(실측된 회귀). 방어를 쓰기 지점에 함께 둔다.
    try:
        typer.secho(
            f"'{command}'는 아직 구현되지 않았습니다 (골격 단계). "
            f"진행 상황: https://github.com/withwooyong/cuesift/issues",
            fg=typer.colors.YELLOW,
            err=True,
        )
    except OSError as exc:
        if not _is_closed_output(exc):
            raise
        _discard_stream(sys.stderr)
    raise typer.Exit(EXIT_NOT_IMPLEMENTED)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cuesift {__version__}")
        raise typer.Exit(0)


def _harden_output_streams() -> None:
    """인코딩할 수 없는 문자가 프로세스를 죽이지 못하게 한다.

    **출력에 실리는 것은 우리 리터럴만이 아니다** — 사용자가 준 파일 경로와 `--spec`
    경로가 그대로 들어간다. Windows 기본 로케일(cp949)에서 인코딩할 수 없는 문자가
    그 안에 있으면 리다이렉트 시 `UnicodeEncodeError`로 프로세스가 죽고 **종료 코드 1**이
    나간다. 이 저장소에서 1은 "규격 위반 발견"이므로 **위반 0건인 깨끗한 파일이 CI에서
    실패로 읽힌다.** 실측된 사례: `Amélie.srt`(U+00E9) · `S01E01 – ko.srt`(U+2013).
    이모지·간체 한자·NBSP도 같다. 자막 파일명에 흔한 문자들이다.

    **em dash 금지는 우리가 쓰는 리터럴만 통제하고 사용자 입력이 흐르는 이 경로는
    못 막는다.** 그래서 규칙이 아니라 스트림 설정으로 닫는다.

    **하중을 받는 것은 stdout 하나뿐이다.** 4방향 변이로 실측한 결과, 이 함수를 통째로
    꺼도 깨지는 것은 `typer.echo`가 stdout에 쓰는 경로뿐이었다. stderr 경로(exit 66의
    `IngestError` 메시지)와 click의 오류 렌더(exit 2)는 **click이 자기 스트림에 이미
    `backslashreplace`를 걸어** 하드닝 없이도 통과한다.

    그럼에도 stderr까지 걸고 `check()`가 아니라 그룹 콜백에서 부르는 이유는 둘이다.

    1. click 내부 동작에 기대는 것은 이 저장소가 반복해 지적한 "열거는 계약이 아니라
       관찰"과 같은 형태다. click이 stderr를 언제까지 감싸 줄지는 우리 계약이 아니다.
    2. `translate`·`transcribe`가 구현되면 같은 문제를 각자 다시 풀어야 한다.

    그룹 콜백은 서브커맨드 인자 검증보다 먼저 돈다(실측: 콜백 → 인자 검증 → 본문).
    **다만 `--help`·`--version`은 eager 옵션이라 콜백보다도 먼저 렌더되므로 여기가
    닿지 않는다** — 그쪽은 리터럴에서 em dash를 빼는 것으로만 막을 수 있고,
    `test_help_output_is_encodable_in_the_cp949_locale`이 그것을 고정한다.

    `reconfigure`가 없는 스트림은 건너뛴다. `io.StringIO`로 stdout을 갈아 끼우고
    `app()`을 부르는 호출자가 있으면 `AttributeError`로 죽는데, 그것이야말로 이
    함수가 막으려던 종류의 사고다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


# 하류가 파이프를 먼저 닫았을 때 나오는 errno. **플랫폼마다 다른 것이 함정이다.**
# POSIX는 `BrokenPipeError`(EPIPE)지만 **Windows는 평범한 `OSError` errno 22(EINVAL)**이고
# `isinstance(exc, BrokenPipeError)`가 False다(실측). `except BrokenPipeError`만 다는 해법은
# Linux에서만 동작하고 개발 플랫폼에서는 조용히 안 먹는다.
#
# **`OSError`를 통째로 삼키지 않는 이유**는 디스크 가득 참(ENOSPC)이다. 리다이렉트 중
# ENOSPC를 삼키면 잘린 출력이 종료 코드 0으로 나가 "검사하지 않고 통과하는 게이트"가 된다.
_CLOSED_OUTPUT_ERRNOS = frozenset({errno.EPIPE, errno.EINVAL})


def _is_closed_output(exc: OSError) -> bool:
    """하류가 먼저 닫은 파이프인가. 그것은 오류가 아니라 `head`·`less`의 정상 동작이다."""
    return exc.errno in _CLOSED_OUTPUT_ERRNOS


def _discard_stream(stream: IO[str]) -> None:
    """스트림의 fd를 `os.devnull`로 갈아 끼워 이후 쓰기를 무해하게 만든다.

    **이것이 없으면 종료 코드가 120으로 덮인다.** 방출 지점에서 예외를 삼켜도
    인터프리터가 종료할 때 `sys.stdout`을 다시 flush하고, 그 flush가 터지면 CPython이
    "Exception ignored"를 찍고 **120으로 끝낸다**(실측: 그대로 두면 120, dup2하면 0).
    파이썬 객체를 바꾸는 것으로는 부족하고 **fd 자체**를 갈아 끼워야 하는 이유가 그것이다.

    **실패한 스트림만 넘겨야 한다.** stdout이 파이프이고 stderr가 터미널인
    `cuesift check bad.srt --spec ko | head -1`에서 stderr까지 죽이면
    사용자가 진단 메시지를 잃는다.

    fd가 없는 스트림(`CliRunner`의 인메모리 래퍼, `io.StringIO`)은 건너뛴다 —
    `fileno()`가 `io.UnsupportedOperation`을 내는데 그것은 `OSError`이자 `ValueError`다.
    """
    try:
        fileno = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return

    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
    except OSError:  # pragma: no cover - devnull을 못 여는 환경은 재현 수단이 없다
        return
    try:
        os.dup2(devnull, fileno)
    finally:
        os.close(devnull)


class _TolerantOutput:
    """닫힌 파이프에 쓰는 것을 무해하게 만드는 프록시 (설계 §7.1).

    **종료 코드를 지키는 유일하게 균일한 방법이다.** 예외를 나중에 잡는 방식은
    "누가 썼는가"에 따라 구멍이 난다 — 종료 코드 2는 click의 `UsageError.show()`가,
    70은 `typer.secho`가 쓰므로 커맨드 본문의 방어가 닿지 않는다. 실측된 회귀:
    `cuesift check nope.srt --spec ko 2>&1 | head -0`이 2가 아니라 **0**으로 나갔고,
    `transcribe`도 70이 아니라 0이었다. **120은 시끄럽지만 0은 조용히 CI를 통과시킨다.**
    쓰기 지점 자체를 무해하게 만들면 어느 코드 경로가 쓰든 같은 결과가 된다.

    **프록시 패턴은 click의 선례다** — `PacifyFlushWrapper`가 같은 목적으로
    `__getattr__` 위임을 쓴다. `isatty`·`encoding`·`fileno`·`buffer` 같은 기능 탐지가
    그대로 통과해야 rich와 click이 정상 동작한다.

    **부수 효과로 플랫폼 차이도 사라진다.** click의 `_main`은 `errno == EPIPE`일 때만
    `sys.exit(1)`을 하는데(typer/core.py) POSIX는 EPIPE, Windows는 EINVAL이라
    같은 사고가 Linux에서는 1, Windows에서는 우리 처리로 갔다. 여기서 막으면
    click의 그 분기에 애초에 도달하지 않는다.

    `ENOSPC`는 그대로 올린다 — 삼키면 잘린 출력이 성공으로 보고된다.
    """

    def __init__(self, wrapped: IO[str]) -> None:
        self.wrapped = wrapped
        self.downstream_closed = False

    def write(self, data: str) -> int:
        if self.downstream_closed:
            return len(data)
        try:
            return self.wrapped.write(data)
        except OSError as exc:
            if not _is_closed_output(exc):
                raise
            self._give_up()
            return len(data)

    def flush(self) -> None:
        if self.downstream_closed:
            return
        try:
            self.wrapped.flush()
        except OSError as exc:
            if not _is_closed_output(exc):
                raise
            self._give_up()

    def _give_up(self) -> None:
        """이 스트림만 포기한다. **다른 스트림은 건드리지 않는다.**

        stdout이 파이프이고 stderr가 터미널인 `check bad.srt --spec ko | head -1`에서
        stderr까지 버리면 사용자가 진단 메시지를 잃는다.
        """
        self.downstream_closed = True
        _discard_stream(self.wrapped)

    def __getattr__(self, attr: str) -> object:
        return getattr(self.wrapped, attr)


def _echo(message: str = "", *, err: bool = False) -> None:
    """커맨드 본문의 출력. 닫힌 파이프에서도 **종료 코드를 지킨다.**

    `_TolerantOutput`이 설치되면 여기까지 예외가 오지 않지만, 이 방어를 남겨 두는 것은
    `app()`을 직접 부르는 호출자(테스트·라이브러리 사용)가 프록시를 못 받기 때문이다.
    그때 예외가 본문을 빠져나가면 `check()`가 `typer.Exit(1)`에 도달하지 못해
    **위반을 찾고도 종료 코드가 1이 아니게 된다.**
    """
    stream = sys.stderr if err else sys.stdout
    try:
        typer.echo(message, err=err)
    except OSError as exc:
        if not _is_closed_output(exc):
            raise
        _discard_stream(stream)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="버전을 출력하고 종료합니다.",
        ),
    ] = False,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            # `--help`로 출력되는 문자열이므로 em dash를 쓰지 않는다(전역 제약).
            #
            # **현재형으로 약속하지 않는다.** 이전 판은 "기본: ./cuesift.yaml"과
            # "CLI 인자가 설정 파일보다 우선합니다"를 현재형으로 적었는데 둘 다 거짓이다 —
            # `./cuesift.yaml`은 읽히지 않고 우선순위 해결도 존재하지 않는다.
            help="설정 파일 경로 (FR-8.4). 아직 구현되지 않아 지정해도 무시됩니다. "
            "구현되면 CLI 인자가 설정 파일보다 우선하게 됩니다.",
        ),
    ] = None,
) -> None:
    """공통 옵션."""
    _harden_output_streams()
    if config is not None:
        # 설계 D12 — **조용한 무시는 이 저장소의 규율에 어긋난다.**
        # 경고가 없으면 사용자는 자기 규격으로 검사됐다고 믿는데 실제로는 내장
        # 기본값으로 검사되고 **종료 코드 0**이 나간다. 그것이 이 저장소가 1급으로
        # 금지한 "검사하지 않고 통과하는 게이트"다.
        #
        # **`_harden_output_streams()` 뒤여야 한다.** 이 줄에 사용자가 준 경로가
        # 그대로 실리므로, 하드닝 전에 쓰면 cp949로 인코딩할 수 없는 경로에서
        # `UnicodeEncodeError`가 나고 종료 코드 1("규격 위반 발견")로 오보된다.
        _echo(
            f"경고: --config는 아직 구현되지 않았습니다 (FR-8.4). "
            f"지정한 '{config}'는 무시되고 CLI 인자만 반영됩니다.",
            err=True,
        )


@app.command()
def translate(
    input: Annotated[Path, typer.Argument(help="자막 파일(.srt/.vtt/...) 또는 영상 파일")],
    to: Annotated[str, typer.Option("--to", help="대상 언어 (쉼표 구분, 예: en,ja,th,vi)")],
    source_lang: Annotated[str | None, typer.Option("--source-lang", help="원문 언어")] = None,
    review_budget: Annotated[
        str | None,
        typer.Option("--review-budget", help="사람이 검수할 상위 비율 (예: 10%)"),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="실행하지 않고 비용만 추정합니다.")
    ] = False,
) -> None:
    """FR-8.1: 번역·검수 전 파이프라인을 실행합니다."""
    _not_implemented("translate")


def _resolve_profile(spec: str) -> tuple[SpecProfile, str]:
    """`--spec` 값을 프로파일과 표시용 label로 바꾼다 (FR-5.3·설계 §8·§7.2).

    **존재 여부가 아니라 확장자로 가른다.** 존재 여부로 가르면 오타 난 파일
    경로가 "내장 이름이 없다"는 틀린 진단을 받는다 — 인제스트가 mp4를
    `decode` 오류로 보고하지 않으려고 확장자를 먼저 본 것과 같은 판단이다.
    **라우팅만 소문자로 본다.** Windows는 파일명 대소문자를 구분하지 않아
    `my-spec.YAML`이 정상인 파일명이고 이 프로젝트의 개발 플랫폼이 Windows인데,
    `load_profile`에는 원본을 넘겨야 한다 — CI의 Linux는 구분한다.

    **label을 여기서 만드는 이유**는 설계 §7.2의 헤더가 "엉뚱한 프로파일로
    통과한 것을 알 수 없다"를 막기 때문이다. `name: ko`인 사용자 파일은 규격
    이름만으로는 내장 `ko`와 구별되지 않아 하필 FR-5.3 경로에서 헤더가 죽는다.
    출처를 label에 실어 구별하되, **확장자 판정을 이 함수 밖으로 복제하지
    않으려고** 호출자가 아니라 여기서 만든다.

    예외는 열거하지 않는다. 열거는 계약이 아니라 관찰이라 로더가 새 예외를 낼
    때마다 뒤처지고, 뒤처진 쪽으로 샌 예외는 종료 코드 1("규격 위반 발견")이 된다.
    실제로 이 튜플이 두 번 넓어지고도 세 번째 누락이 남아 있었다.

    **대신 `load_profile`이 내용 오류를 전부 `ValueError`로 정규화한다는 계약에
    기댄다.** 이 두 줄이 짧은 것은 그 계약 덕분이지 안전해서가 아니다 —
    `spec/profile.py`의 정규화가 느슨해지면 여기가 조용히 무방비가 된다.
    """
    try:
        if spec.lower().endswith((".yaml", ".yml")):
            profile = load_profile(Path(spec))
            return profile, f"{profile.name} ({spec})"
        profile = load_builtin(spec)
        return profile, profile.name
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--spec") from exc


# duration_short가 14자로 가장 길다. 한 칸을 더 둬야 수치와 붙지 않는다.
#
# **이 폭이 정렬을 지탱하는 것은 kind 7종이 전부 ASCII이기 때문이다.** `f"{kind:<15}"`는
# **글자 수**로 패딩하는데 터미널은 **표시 폭**으로 그린다. 한글 kind(예: `줄길이초과`)를
# 추가하면 5글자가 10칸을 차지해 그 줄만 5칸 밀리고, **부분 문자열 단언은 밀린 줄도
# 통과시키므로 어느 테스트도 울리지 않는다.** 새 kind는 ASCII로 짓거나, 그럴 수 없다면
# `spec/counting.py`의 `text_width`로 폭을 재서 패딩해야 한다.
_KIND_WIDTH = 15


def _format_timecode(ms: int) -> str:
    """`00:01:23.400`으로 고정한다 (설계 §7.3).

    SRT는 쉼표(`,400`), VTT는 마침표(`.400`)를 쓰므로 입력 포맷을 따라가면
    같은 도구의 출력이 파일마다 달라진다. 1차 좌표는 큐 번호이고 타임코드는
    보조이므로 표기를 하나로 고정하는 편이 낫다.

    **음수는 부호를 살린다.** 이전 판의 `max(ms, 0)`은 `-3000`을 `00:00:00.000`으로
    만들었고, 그것을 남긴 근거는 "음수는 `Segment.__post_init__`과 인제스트 경계가 이미
    막는다"였다 — **그 전제가 거짓이었다.** 둘 다 역전(`end < start`)만 봤고 부호는
    아무도 안 봤다. 그 결과 `(-5000, -1000)`짜리 트랙이 **exit 0 · "위반 없음"으로
    통과했다**(실측).

    지금은 `_require_non_negative_timecodes`가 인제스트 경계에서 66으로 막으므로 이
    함수에 음수가 도달하는 것 자체가 상류의 결함이다. 그래도 클램프를 되살리지 않는 것은
    클램프가 **적극적으로 거짓을 만들기** 때문이다 — 검수자는 `00:00:00.000`을 믿고
    찾아가고 거기엔 아무것도 없다. `loader.py`가 못 박은 "조용히 틀린 답은 크래시보다
    나쁘다"가 여기에도 적용된다.

    `abs`로 자릿수를 만들고 부호를 따로 붙이는 이유는 `divmod`에 음수를 그대로 흘리면
    파이썬의 바닥 나눗셈이 `divmod(-3000, 1000) == (-3, 0)`을 내어 `-1:59:57.000`이
    되기 때문이다. 부호만 앞에 붙이면 나머지 자릿수는 양수와 같은 규칙으로 읽힌다.
    """
    sign = "-" if ms < 0 else ""
    seconds, milliseconds = divmod(abs(ms), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _format_ratio(percent: float) -> str:
    """위반 큐 비율을 적는다. **0이 아닌데 `0.0%`로 보이면 안 된다.**

    `f"{x:.1f}%"`는 2001큐 중 1개(0.049%)를 `0.0%`로 떨어뜨린다. 이 저장소는 "0으로 보이는
    수치"를 1급 결함으로 취급한다 — 검수자가 위반 목록을 눈앞에 두고 요약만 보면
    "0%니까 통과"로 읽는다. 자릿수를 늘리는 대신 `<0.1%`로 적어 **0이 아님을 말한다.**

    반올림이 아니라 절단으로 판정하는 이유는 0.04%와 0.06%가 모두 0이 아니기 때문이다.
    """
    if percent > 0 and percent < 0.05:
        return "<0.1%"
    return f"{percent:.1f}%"


def _format_detail(violation: SpecViolation) -> str:
    """위반 한 건의 수치 부분을 만든다.

    `line_index`는 **0-based**다. 사람이 읽는 좌표는 1부터 세므로 `+1`한다 —
    빼먹어도 테스트 없이는 드러나지 않는 종류의 오차다.
    """
    kind = violation.kind
    if kind == "empty_cue":
        return "텍스트 없음"
    if kind == "overlap":
        return f"{violation.measured:.0f}ms"
    if kind in ("duration_short", "duration_long"):
        sign = "<" if kind == "duration_short" else ">"
        return f"{violation.measured:.0f}ms {sign} {violation.limit:.0f}ms"

    detail = f"{violation.measured} > {violation.limit}"
    if violation.line_index is not None:
        detail = f"{detail}  ({violation.line_index + 1}번째 줄)"
    return detail


def _format_report(
    *,
    source_name: str,
    fmt: str,
    profile_label: str,
    cue_total: int,
    violations: Sequence[TrackViolation],
    event_index: Mapping[str, int],
    limit: int = 0,
) -> list[str]:
    """콘솔 산출물 전체를 만든다 (설계 §7).

    **순수 함수인 것이 요점이다.** `CliRunner` 없이 문자열 입출력으로 직접
    시험할 수 있어야 정렬·자릿수·큐 번호 부여 같은 포맷 결함이 CLI 통합
    테스트에 묻히지 않는다(설계 §7.4).

    `profile_label`은 규격 이름이 아니라 **표시용 label**이다. `_resolve_profile`이
    내장은 `ko`, 사용자 파일은 `ko (./our-spec.yaml)`로 만든다 — 이름만 실으면
    `name: ko`인 사용자 파일이 내장 `ko`와 헤더까지 같아져 구별되지 않는다.

    위반이 없을 때도 검사 대상 개수와 프로파일 이름을 낸다 — 그것이 없으면
    사용자는 엉뚱한 파일이나 엉뚱한 프로파일로 통과한 것을 알 수 없다.

    **요약을 머리와 끝 양쪽에 낸다.** 이전 판은 맨 아래 한 줄이었는데, 위반 682건이면
    686줄이 나가고 26화 × 3언어 매트릭스에서 프로파일을 잘못 물리면 약 5만 줄이 쌓인다.
    로그를 앞에서 남기고 뒤를 자르는 CI에서는 **가장 중요한 한 줄이 가장 먼저** 사라진다.
    양쪽에 두면 절단 방향과 무관하게 살아남고, 중복 2줄은 1204줄의 0.17%다.

    **`limit`은 여기서 적용해야 한다.** 호출부에서 `lines[:N]`으로 자르면 요약 줄까지
    함께 잘려 위 목적이 정확히 무너진다 — 무엇을 자르고 무엇을 남길지는 산출물의
    구조를 아는 이 함수만 판단할 수 있다. `0`은 무제한이고 그것이 기본값인 이유는
    상한을 기본으로 켜면 전체 목록을 파이프로 받던 쓰임이 조용히 잘리기 때문이다.
    """
    # `검사 큐`인 이유: `cue_total`은 필터 **후** 개수라 아래의 `#N`(원본 큐 번호)이
    # 이 수보다 클 수 있다 — `큐 2개` 아래 `#4`가 찍히면 자기모순처럼 읽힌다.
    # 분모를 원본 이벤트 수로 되돌리면 안 된다: 검사 대상이 아닌 주석·드로잉까지 세어
    # 위반 비율이 과소평가되고, 그것은 Recall@Budget 지표를 건드린다.
    head = f"{source_name} ({fmt} · 검사 큐 {cue_total}개 · 프로파일 {profile_label})"
    if not violations:
        # em dash(U+2014)를 쓰지 않는다. cp949 로케일에서 stdout을 리다이렉트하면
        # UnicodeEncodeError로 exit 1이 나고, 이 저장소에서 exit 1은 "규격 위반 발견"이다.
        # 깨끗한 파일이 CI에서 위반으로 읽힌다.
        return [f"{head} - 위반 없음"]

    # **요약을 목록보다 먼저 계산한다.** 절단은 목록만 자르고 요약은 언제나 전체
    # 기준이어야 한다 — 자른 뒤에 세면 `--limit 3`이 "위반 3건"이라는 거짓말을 내고,
    # 그것은 CI 로그를 읽는 사람에게 종료 코드와 모순되는 수치를 준다.
    flagged = len({tv.segment_id for tv in violations})
    ratio = flagged / cue_total * 100 if cue_total else 0.0
    summary = f"위반 {len(violations)}건 · 위반 큐 {flagged}/{cue_total}개 ({_format_ratio(ratio)})"

    lines = [head, summary, ""]
    # 큐 번호 폭을 `cue_total`이 아니라 `event_index`에서 구한다. `cue_total`은
    # 필터 **후** 개수라 주석이 있는 파일에서는 원본 큐 번호의 최대값보다 작고,
    # 폭이 모자라면 자릿수가 큰 줄부터 뒤 열이 통째로 오른쪽으로 밀린다.
    cue_width = len(str(max(event_index.values(), default=0) + 1))
    # **`limit <= 0`이지 `limit == 0`이 아니다.** 음수를 그대로 흘리면 `violations[:-1]`이
    # 되어 **마지막 위반이 조용히 사라진다.** CLI는 typer의 `min=0`이 막지만 이 함수는
    # 라이브러리로도 불리므로 여기서도 닫는다.
    shown = violations if limit <= 0 else violations[:limit]
    for track_violation in shown:
        # **원본 파일의 "이벤트 순번"이지 SRT에 인쇄된 번호가 아니다.**
        # `segment.index + 1`이 아닌 것은 맞다 — 필터가 인덱스를 재부여하므로 주석이 있는
        # 파일에서 둘이 갈라진다(설계 §4.1). 다만 거기까지다: pysubs2가 SRT의 인쇄 번호를
        # 버리므로 번호가 `1,2,4,5`인 파일(3번이 지워진 파일)에서는 파일의 `4`를 `#3`으로
        # 부른다. 진짜 대응은 인쇄 번호를 보존해야 하고 v0.1 범위 밖이다.
        cue = event_index[track_violation.segment_id] + 1
        stamp = _format_timecode(track_violation.start_ms)
        kind = f"{track_violation.violation.kind:<{_KIND_WIDTH}}"
        lines.append(
            f"  #{cue:<{cue_width}}  {stamp}  {kind}{_format_detail(track_violation.violation)}"
        )

    # 잘렸다는 사실을 숨기지 않는다. 고지가 없으면 사용자는 목록이 전부라고 읽고,
    # 그것은 이 저장소가 1급 결함으로 취급하는 "조용한 손실"이다. 상한이 위반 수보다
    # 클 때 고지를 내지 않는 것도 같은 이유다 — `0건 생략`은 그 자체로 거짓말이다.
    omitted = len(violations) - len(shown)
    if omitted:
        lines.append(f"  ... {omitted}건 생략 (전체는 --limit 0)")

    lines.append("")
    lines.append(summary)
    return lines


# **아래 독스트링은 `cuesift check --help`의 첫 화면에 그대로 뜬다** — typer가 커맨드
# 독스트링 **전체**를 help로 만든다. 그래서 설계 근거는 독스트링이 아니라 여기 둔다.
# 사용자에게 `collect_all`·`fuse`·`triage`·`설계 D3`를 보여 줄 이유가 없다.
#
# **`check`는 신호 엔진을 통과하지 않는다**(설계 D3). `collect_all`→`fuse`→`triage`가
# 얹는 넷(점수화·hard_fail·융합·트리아지)을 이 명령이 하나도 쓰지 않기 때문이다.
# 심각도가 단일 등급이고 예산도 순위도 없다. 규격 판정의 원천은 `spec/check.py` 하나이고
# translate 경로와 여기가 양쪽 다 그것을 쓴다.
@app.command()
def check(
    input: Annotated[
        Path,
        # `readable=False`는 typer의 기본 `readable=True`를 끈다. 켜져 있으면 typer가
        # 본문에 닿기 전에 `os.access(path, os.R_OK)`를 보고(`typer/models.py`)
        # **읽을 수 없는 파일을 종료 코드 2로 낸다.** POSIX의 mode 000은 거기서 걸리고
        # Windows의 배타 잠금은 `os.access`를 통과해 66이 되므로, 켜 두면 **같은 사고가
        # 플랫폼마다 다른 코드**를 낸다. 위 표가 "읽을 수 없음 = 66"이라고 단언하므로
        # 판정을 인제스트 한 곳으로 모은다.
        typer.Argument(exists=True, dir_okay=False, readable=False, help="검사할 자막 파일"),
    ],
    spec: Annotated[
        str,
        typer.Option("--spec", help="규격 프로파일 이름(예: ko) 또는 .yaml 파일 경로"),
    ],
    fail_on: Annotated[
        FailOn,
        # help 문자열은 `--help`로 출력되므로 em dash를 쓰지 않는다(전역 제약).
        typer.Option(
            "--fail-on",
            help="hard와 any는 v0.1에서 같다. 위반 1건이면 종료 코드 1. none은 보고만 하고 항상 0",
        ),
    ] = FailOn.hard,
    limit: Annotated[
        int,
        # `min=0`은 typer가 **본문에 닿기 전에** 종료 코드 2로 거른다. 음수를 본문까지
        # 흘리면 `violations[:-1]`로 마지막 위반이 조용히 사라진다(설계상 `_format_report`도
        # 따로 막지만, 잘못된 명령줄은 명령줄 오류로 보고되는 편이 진단이 정확하다).
        # help 문자열은 `--help`로 나가므로 em dash를 쓰지 않는다(전역 제약).
        typer.Option("--limit", min=0, help="위반 목록을 N건까지만 출력한다. 0은 무제한(기본)"),
    ] = 0,
) -> None:
    """FR-8.2: 자막 규격 검사만 수행합니다 (CI 게이트)."""
    # `_resolve_profile`은 프로파일과 **표시용 라벨**을 함께 낸다. 라벨이 따로 필요한 것은
    # `profile.name`이 YAML의 `name` 필드라서, `--spec ./our-spec.yaml`인데 그 파일이
    # `name: ko`면 헤더가 내장 `ko`로 검사한 것과 **바이트 단위로 같아지기** 때문이다.
    # 설계 §7.2가 헤더를 둔 이유("엉뚱한 프로파일로 통과한 것을 알 수 없다")가 FR-5.3
    # 경로에서 정확히 무효화된다.
    profile, profile_label = _resolve_profile(spec)

    try:
        result = load_subtitle(input)
    except IngestError as exc:
        # 진단 실패는 산출물이 아니라 실행 실패 보고다. stderr로 낸다(설계 §7.1).
        # `IngestError` 하나만 잡으면 되는 것은 `loader.py`가 자기 실패를 전부 이
        # 타입으로 모으기 때문이다 — `OSError`까지 포함한다. 여기서 예외를 열거하기
        # 시작하면 로더가 새 실패를 낼 때마다 뒤처지고, 샌 예외는 미처리 traceback으로
        # 종료 코드 1이 되어 "규격 위반 발견"으로 오보된다.
        _echo(str(exc), err=True)
        raise typer.Exit(EXIT_BAD_INPUT) from exc

    violations = check_track(result.segments, profile)

    # 위반 목록은 이 명령의 정상 산출물이므로 stdout이다(설계 D9).
    # 사용자 경로가 이 줄에 실리지만 인코딩 사고는 `_harden_output_streams`가 막는다.
    for line in _format_report(
        # 이름이 아니라 **경로 전체**를 넘긴다. `input.name`만 넘기면 디렉터리를 순회하며
        # 로그를 합치는 스크립트에서 `ko/ep01.srt`와 `ja/ep01.srt`가 같은 줄로 보이고,
        # 헤더의 목적("엉뚱한 파일로 통과한 것을 알 수 있게")이 정확히 무너진다.
        # `IngestError` 메시지도 전체 경로를 쓰므로 표기가 일관된다.
        source_name=str(input),
        fmt=result.format,
        profile_label=profile_label,
        cue_total=len(result.segments),
        violations=violations,
        event_index=result.event_index,
        limit=limit,
    ):
        _echo(line)

    # **종료 코드는 `limit`을 보지 않는다.** 판정의 결과이지 출력의 결과가 아니다 —
    # 3건만 보여준다고 위반이 3건인 것이 아니고, 여기가 흔들리면 CI 게이트가
    # 출력 옵션에 좌우된다(`test_limit_does_not_change_the_exit_code`가 고정한다).
    if violations and fail_on is not FailOn.none:
        raise typer.Exit(1)


@app.command()
def transcribe(
    input: Annotated[Path, typer.Argument(help="영상 또는 오디오 파일")],
    source_lang: Annotated[str | None, typer.Option("--source-lang", help="원문 언어")] = None,
) -> None:
    """FR-8.3: STT로 원문 자막만 생성합니다."""
    _not_implemented("transcribe")


def run() -> None:
    """콘솔 스크립트 진입점 (`pyproject.toml`의 `[project.scripts]`).

    **`app`을 직접 진입점으로 두면 `cuesift --help | less`가 종료 코드 120을 낸다.**
    `--help`·`--version`·사용법 오류(2)·미구현(70)의 출력은 커맨드 본문 밖에서 일어나
    `_echo`가 닿지 않는다.

    **종료 코드를 여기서 바꾸지 않는 것이 계약이다.** 이전 판은 닫힌 파이프를 잡아
    `SystemExit(0)`으로 바꿨는데, 그것이 **exit 2와 exit 70을 조용한 0으로 만들었다**
    (실측). 지금은 출력 지점을 무해하게 만들어 각 커맨드가 고른 코드가 그대로 나가게 한다.

    | 층 | 무엇 | 지키는 것 |
    | --- | --- | --- |
    | 1 | `_TolerantOutput` (여기서 설치) | 어느 코드 경로가 쓰든 쓰기가 실패하지 않는다 |
    | 2 | `_echo`·`_not_implemented` (커맨드 본문) | `app()`을 직접 부르는 호출자용 **부분** 방어 |
    | 3 | 아래 `finally` | 종료 flush가 120을 만들지 못하게 한다 |

    **2층은 0·1·66(`_echo`)과 70(`_not_implemented`)만 덮는다.** 종료 코드 2는 click의
    `UsageError.show()`가 쓰므로 본문에 방어할 지점이 없다 — **1층 없이는 못 막는다.**
    `run()`을 거치는 배포 경로는 1층이 전부 덮으므로 실사용 위험은 없고,
    `app()`을 직접 부르는 테스트·라이브러리 호출자에게만 해당한다.

    `ENOSPC`는 어느 층도 삼키지 않는다 — 잘린 출력이 성공으로 보고되면 안 된다.
    """
    sys.stdout = _TolerantOutput(sys.stdout)  # type: ignore[assignment]
    sys.stderr = _TolerantOutput(sys.stderr)  # type: ignore[assignment]
    try:
        app()
    finally:
        # 버퍼에 남은 것을 여기서 흘려보낸다. 프록시가 닫힌 파이프를 이미 삼키므로
        # 여기서 터지는 것은 진짜 I/O 오류뿐이고, 그때는 **올라가야 한다.**
        sys.stdout.flush()
        sys.stderr.flush()


if __name__ == "__main__":  # pragma: no cover
    run()
