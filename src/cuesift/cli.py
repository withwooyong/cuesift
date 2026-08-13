"""cuesift CLI 진입점.

요구사항정의서 §8.1(FR-8.1~8.5)의 커맨드 표면을 정의한다.
현재 각 서브커맨드는 **동작하지 않는 골격**이며, 인자 스키마만 확정한 상태다.
구현이 붙기 전까지 EXIT_NOT_IMPLEMENTED로 종료한다.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from cuesift import __version__
from cuesift.spec import SpecProfile, SpecViolation, TrackViolation, load_builtin, load_profile

# CI가 "미구현"과 "검수 실패"를 구분할 수 있도록 종료 코드를 분리한다.
# FR-8.2의 --fail-on은 향후 1을 쓰고, 70은 미구현 표식으로 남긴다.
EXIT_NOT_IMPLEMENTED = 70

app = typer.Typer(
    name="cuesift",
    help="AI 자막 번역·검수 트리아지 엔진 — 사람이 정말 봐야 할 자막만 걸러냅니다.",
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
            help="설정 파일 경로 (기본: ./cuesift.yaml). "
            "FR-8.4 — CLI 인자가 설정 파일보다 우선합니다.",
        ),
    ] = None,
) -> None:
    """공통 옵션."""


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
    """FR-8.1 — 번역·검수 전 파이프라인을 실행합니다."""
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
    profile_name: str,
    cue_total: int,
    violations: Sequence[TrackViolation],
    event_index: Mapping[str, int],
) -> list[str]:
    """콘솔 산출물 전체를 만든다 (설계 §7).

    **순수 함수인 것이 요점이다.** `CliRunner` 없이 문자열 입출력으로 직접
    시험할 수 있어야 정렬·자릿수·큐 번호 부여 같은 포맷 결함이 CLI 통합
    테스트에 묻히지 않는다(설계 §7.4).

    `profile_name`은 규격 이름이 아니라 **표시용 label**이다. `_resolve_profile`이
    내장은 `ko`, 사용자 파일은 `ko (./our-spec.yaml)`로 만든다 — 이름만 실으면
    `name: ko`인 사용자 파일이 내장 `ko`와 헤더까지 같아져 구별되지 않는다.

    위반이 없을 때도 검사 대상 개수와 프로파일 이름을 낸다 — 그것이 없으면
    사용자는 엉뚱한 파일이나 엉뚱한 프로파일로 통과한 것을 알 수 없다.
    """
    head = f"{source_name} ({fmt} · 큐 {cue_total}개 · 프로파일 {profile_name})"
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
    input: Annotated[Path, typer.Argument(help="검사할 자막 파일")],
    spec: Annotated[str, typer.Option("--spec", help="규격 프로파일 이름 (예: th)")],
    fail_on: Annotated[
        FailOn, typer.Option("--fail-on", help="이 심각도 이상이면 종료 코드 ≠ 0")
    ] = FailOn.hard,
) -> None:
    """FR-8.2 — 자막 규격 검사만 수행합니다 (CI 게이트)."""
    _not_implemented("check")


@app.command()
def transcribe(
    input: Annotated[Path, typer.Argument(help="영상 또는 오디오 파일")],
    source_lang: Annotated[str | None, typer.Option("--source-lang", help="원문 언어")] = None,
) -> None:
    """FR-8.3 — STT로 원문 자막만 생성합니다."""
    _not_implemented("transcribe")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
