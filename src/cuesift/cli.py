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

import sys
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Annotated

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
    typer.secho(
        f"'{command}'는 아직 구현되지 않았습니다 (골격 단계). "
        f"진행 상황: https://github.com/withwooyong/cuesift/issues",
        fg=typer.colors.YELLOW,
        err=True,
    )
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

    **`check()`가 아니라 그룹 콜백에서 부르는 이유**는 종료 코드 2가 여기 걸려 있기
    때문이다. `exists=True` 위반 메시지는 click이 렌더하는데, 그 렌더는 서브커맨드
    본문보다 **먼저** 일어난다(실측: 그룹 콜백 → 인자 검증 → 본문). `check()` 안에서
    부르면 exit 2 경로는 이미 지나간 뒤라 손대지 못하고, 없는 파일 이름에 é가 있으면
    2가 아니라 1이 나간다. stderr까지 함께 거는 것은 `IngestError` 메시지가 경로를
    담아 stderr로 나가기 때문이다 — 그쪽이 죽으면 66이 1로 바뀐다.

    `reconfigure`가 없는 스트림은 건너뛴다. `io.StringIO`로 stdout을 갈아 끼우고
    `app()`을 부르는 호출자가 있으면 `AttributeError`로 죽는데, 그것이야말로 이
    함수가 막으려던 종류의 사고다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


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
            help="설정 파일 경로 (기본: ./cuesift.yaml). "
            "FR-8.4: CLI 인자가 설정 파일보다 우선합니다.",
        ),
    ] = None,
) -> None:
    """공통 옵션."""
    _harden_output_streams()


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
_KIND_WIDTH = 15


def _format_timecode(ms: int) -> str:
    """`00:01:23.400`으로 고정한다 (설계 §7.3).

    SRT는 쉼표(`,400`), VTT는 마침표(`.400`)를 쓰므로 입력 포맷을 따라가면
    같은 도구의 출력이 파일마다 달라진다. 1차 좌표는 큐 번호이고 타임코드는
    보조이므로 표기를 하나로 고정하는 편이 낫다.
    """
    seconds, milliseconds = divmod(max(ms, 0), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


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

    lines = [head, ""]
    # 큐 번호 폭을 `cue_total`이 아니라 `event_index`에서 구한다. `cue_total`은
    # 필터 **후** 개수라 주석이 있는 파일에서는 원본 큐 번호의 최대값보다 작고,
    # 폭이 모자라면 자릿수가 큰 줄부터 뒤 열이 통째로 오른쪽으로 밀린다.
    cue_width = len(str(max(event_index.values(), default=0) + 1))
    for track_violation in violations:
        # 원본 파일의 큐 번호다. `segment.index + 1`이 아니다 — 필터가 인덱스를
        # 재부여하므로 주석이 있는 파일에서 둘이 갈라진다(설계 §4.1).
        cue = event_index[track_violation.segment_id] + 1
        stamp = _format_timecode(track_violation.start_ms)
        kind = f"{track_violation.violation.kind:<{_KIND_WIDTH}}"
        lines.append(
            f"  #{cue:<{cue_width}}  {stamp}  {kind}{_format_detail(track_violation.violation)}"
        )

    flagged = len({tv.segment_id for tv in violations})
    ratio = flagged / cue_total * 100 if cue_total else 0.0
    lines.append("")
    lines.append(f"위반 {len(violations)}건 · 위반 큐 {flagged}/{cue_total}개 ({ratio:.1f}%)")
    return lines


@app.command()
def check(
    input: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="검사할 자막 파일"),
    ],
    spec: Annotated[
        str,
        typer.Option("--spec", help="규격 프로파일 이름(예: ko) 또는 .yaml 파일 경로"),
    ],
    fail_on: Annotated[
        FailOn,
        # help 문자열은 `--help`로 출력되므로 em dash를 쓰지 않는다(전역 제약).
        typer.Option("--fail-on", help="hard와 any는 v0.1에서 같다. 위반 1건이면 종료 코드 1"),
    ] = FailOn.hard,
) -> None:
    """FR-8.2: 자막 규격 검사만 수행합니다 (CI 게이트).

    **신호 엔진을 통과하지 않는다**(설계 D3). `collect_all`→`fuse`→`triage`가
    얹는 넷(점수화·hard_fail·융합·트리아지)을 이 명령이 하나도 쓰지 않기
    때문이다. 심각도가 단일 등급이고 예산도 순위도 없다. 규격 판정의 원천은
    `spec/check.py` 하나이고 translate 경로와 여기가 양쪽 다 그것을 쓴다.
    """
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
        typer.echo(str(exc), err=True)
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
    ):
        typer.echo(line)

    if violations and fail_on is not FailOn.none:
        raise typer.Exit(1)


@app.command()
def transcribe(
    input: Annotated[Path, typer.Argument(help="영상 또는 오디오 파일")],
    source_lang: Annotated[str | None, typer.Option("--source-lang", help="원문 언어")] = None,
) -> None:
    """FR-8.3: STT로 원문 자막만 생성합니다."""
    _not_implemented("transcribe")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
