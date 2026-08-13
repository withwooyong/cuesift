"""cuesift CLI 진입점.

요구사항정의서 §8.1(FR-8.1~8.5)의 커맨드 표면을 정의한다.
현재 각 서브커맨드는 **동작하지 않는 골격**이며, 인자 스키마만 확정한 상태다.
구현이 붙기 전까지 EXIT_NOT_IMPLEMENTED로 종료한다.
"""

from __future__ import annotations

import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
import yaml

from cuesift import __version__
from cuesift.spec import SpecProfile, load_builtin, load_profile

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


def _resolve_profile(spec: str) -> SpecProfile:
    """`--spec` 값을 프로파일로 바꾼다 (FR-5.3·설계 §8).

    **존재 여부가 아니라 확장자로 가른다.** 존재 여부로 가르면 오타 난 파일
    경로가 "내장 이름이 없다"는 틀린 진단을 받는다 — 인제스트가 mp4를
    `decode` 오류로 보고하지 않으려고 확장자를 먼저 본 것과 같은 판단이다.

    **두 분기가 같은 예외 집합을 잡아야 해서 try를 하나로 묶었다.** 분기마다
    except를 따로 두면 한쪽만 넓히다 어긋나고, 어긋난 쪽으로 샌 예외는 미처리
    traceback이 되어 **종료 코드 1**로 나간다. 이 저장소에서 1은 "규격 위반
    발견"이라 프로파일 사고가 자막 결함으로 오보되고, 사용자는 멀쩡한 자막을
    고치려 든다. `load_builtin`이 내부에서 `load_profile`을 부르므로 두 분기가
    같은 실패를 공유한다는 점도 이유다.

    잡는 셋은 전부 실측한 것이다. `OSError`는 파일이 없거나 못 읽을 때(`load_builtin`의
    `FileNotFoundError`가 하위), `ValueError`는 필드 누락·값 오류·UTF-8 아님
    (`UnicodeDecodeError`가 하위), `yaml.YAMLError`는 YAML 문법 오류다.
    **셋째를 빼면 안 된다** — 문법 오류는 `OSError`도 `ValueError`도 아니라서
    앞의 둘로는 못 잡는다.
    """
    try:
        if spec.endswith((".yaml", ".yml")):
            return load_profile(Path(spec))
        return load_builtin(spec)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--spec") from exc


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
