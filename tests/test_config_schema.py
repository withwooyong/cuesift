"""매핑표가 CLI 옵션 집합과 어긋나지 않는지 검사한다 (FR-8.4 · 설계 R5)."""

from __future__ import annotations

import typer

from cuesift.cli import app
from cuesift.config.schema import ALLOWED_PATHS, BINDINGS, LEAF_PATHS, SPECIAL_PATHS


def _cli_options() -> set[tuple[str, str]]:
    """(커맨드, 파라미터명) 집합. 위치인자와 --help는 뺀다 (설계 D13)."""
    group = typer.main.get_command(app)
    found: set[tuple[str, str]] = set()
    for name, sub in group.commands.items():
        for param in sub.params:
            if param.param_type_name != "option" or param.name == "help":
                continue
            found.add((name, param.name))
    return found


def test_매핑표가_CLI_옵션_집합과_상등이다() -> None:
    # 부분집합이 아니라 상등을 본다. 한쪽 방향만 보면 매핑표에 남은
    # 죽은 행을 못 잡는다(설계 R5).
    mapped = {target for binding in BINDINGS for target in binding.targets}
    assert mapped == _cli_options()


def test_CLI_옵션은_33개다() -> None:
    # translate 26 + check 3 + transcribe 4. 이 수가 바뀌면 위 상등도
    # 깨지지만, 여기서 먼저 어긋난 쪽을 알려 준다(설계 §5).
    # FR-8.3의 `--media`·`--stt-base-url`·`--stt-model`이 27에서 30으로 올렸고,
    # FR-6.3 ①의 `--review-top-k`가 31로, FR-4.2의 `--embed-base-url`·
    # `--embed-model`이 33으로 올렸다.
    assert len(_cli_options()) == 33


def test_허용_경로는_매핑표에서_파생된다() -> None:
    # 허용 목록을 손으로 두면 "허용은 되는데 아무 데도 안 가는 키"가
    # 생긴다 - 조용히 무시되는 설정이다(설계 §4.1).
    # ruff SIM300이 대문자 이름을 상수로 보므로 파생값을 왼쪽에 둔다.
    # 집합 상등은 방향이 없어 게이트의 힘은 같다.
    derived = frozenset({b.path for b in BINDINGS}) | frozenset(SPECIAL_PATHS)
    assert derived == ALLOWED_PATHS


def test_signals_weights는_잎이다() -> None:
    # 하위 키가 신호 이름이라 미지 키 검사가 내려가면 안 된다.
    assert frozenset({("signals", "weights")}) == LEAF_PATHS
