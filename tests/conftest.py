"""테스트 공용 헬퍼 — rich 렌더링 정규화.

**왜 필요한가.** `typer`는 `--help`와 사용법 오류를 `rich`로 그린다. 그 출력에는 우리가
쓴 적 없는 **박스 테두리**가 섞이고 **강제 개행**이 들어간다. 둘 다 우리 계약이 아닌데
테스트가 그것까지 검사하면 **호스트 플랫폼에 따라 결과가 갈린다.**

실측 (2026-08-13):

| 환경 | `rich`의 모서리 문자 | cp949 |
| --- | --- | --- |
| Windows (`legacy_windows=True`) | `┌┐└┘` U+250C·2510·2514·2518 | **인코딩 된다** |
| Linux CI (`legacy_windows=False`) | `╭╮╰╯` U+256D~2570 | **인코딩 안 된다** |
| 실제 실행 + 리다이렉트 | **박스를 아예 안 그린다** | 무관 |

**세 번째 줄이 핵심이다** — 사용자가 `cuesift --help > help.txt`를 하면 `rich`는 박스를
그리지 않는다(터미널이 아니라서). 즉 박스 문자는 **`CliRunner`가 만들어 낸 산물**이지
사용자가 만나는 것이 아니다. 그런데 그것을 검사하면 **Windows에서는 통과하고 Linux CI에서만
실패한다.** 실제로 그렇게 됐다.

같은 이유로 **긴 문자열·경로를 통째로 단언하면 안 된다.** `rich`가 박스 폭에 맞춰 개행을
넣는데, 그 위치는 임시 디렉터리 경로 길이에 따라 달라져 **Windows와 Linux에서 다른 곳에서
끊긴다.** 실측: 로컬에서는 `cp949-spec.yaml`이 살아남고 `utf-8로 읽을 수 없다`가 끊겼는데,
CI에서는 정확히 반대였다.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def pytest_configure(config: pytest.Config) -> None:
    """게이트 설정이 바뀌었으면 **수집 전에** 실행을 멈춘다 (설계 §9.2).

    이 검사가 `tests/test_translate_api.py`가 아니라 여기 있는 이유는 하나다 -
    **테스트로 두면 자기를 무력화하는 변이에 같이 쓸려 나간다.** 실측:
    `addopts` 끝에 `-m live`를 덧붙이면 뒤의 `-m`이 이겨 `1 skipped,
    832 deselected`가 되는데(훅을 넣기 전 실측), 그 832개에 **감시자 자신이
    들어 있다.** exit 0이라 CI는 초록이고 832개가 한 번도 돌지 않은 것은
    아무도 모른다.

    이 저장소가 CI 5회 연속 실패를 못 본 것이 정확히 이 형태다 -
    "통과했나"는 초록인데 **"무엇을 대상으로 통과했나"가 통째로 바뀌었다.**

    `pytest_configure`는 마커 필터링보다 **먼저** 돌고 deselect의 대상이
    아니라서 이 자리만이 안전하다. 명령줄의 `-m live`는 여기 걸리지 않는다 -
    보는 것은 `addopts`(기본값)이지 이번 실행의 마커식이 아니다.
    """
    problems: list[str] = []

    # pytest.ini·tox.ini가 생기면 pyproject보다 그쪽이 이긴다. 우리 설정이
    # 통째로 무시된 채 초록이 나는 경로다.
    if config.inipath != _REPO_ROOT / "pyproject.toml":
        problems.append(f"pytest가 읽은 설정이 우리 pyproject가 아니다: {config.inipath}")

    # 미등록 마커를 에러로 만드는 플래그. 꺼지면 설계 §9.2의 전제가 무너진다.
    if not config.getoption("strict_markers"):
        problems.append("--strict-markers가 꺼졌다")

    # **다시 shlex로 쪼개면 안 된다.** `getini("addopts")`는 pytest가 이미
    # 분리해 둔 리스트라, 합쳤다 다시 나누면 `"not live"`가 `"not"`과
    # `"live"` 두 토큰이 되어 아래 비교가 정상 설정에서도 실패한다(실측).
    addopts = list(config.getini("addopts"))
    if addopts.count("-m") != 1:
        problems.append(f"addopts의 -m이 하나가 아니다(뒤가 이긴다): {addopts}")
    elif addopts[addopts.index("-m") + 1] != "not live":
        problems.append(f"addopts의 기본 제외식이 'not live'가 아니다: {addopts}")

    if problems:
        raise pytest.UsageError("게이트 설정이 어긋났다: " + " / ".join(problems))


# 유니코드 Box Drawing 블록. `rich`의 패널 테두리가 전부 여기 있다.
_BOX_DRAWING = re.compile(r"[─-╿]")

# ANSI 이스케이프(색·커서). `CliRunner`는 보통 색을 끄지만 환경에 따라 샌다.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_rich_decoration(text: str) -> str:
    """`rich`가 **그린** 것만 지운다. 우리가 **쓴** 문자열은 그대로 둔다.

    인코딩 계약(`cp949`에서 출력이 깨지지 않는가)을 검사할 때 쓴다. 테두리를 남기면
    호스트 플랫폼의 `legacy_windows` 값을 검사하는 셈이 되고, 그것은 우리 계약이 아니다.
    """
    return _ANSI.sub("", _BOX_DRAWING.sub("", text))


def normalize_rich_message(text: str) -> str:
    """부분 문자열 단언용 — 테두리·ANSI를 지우고 **공백을 전부 없앤다.**

    `rich`의 강제 개행이 어디에 떨어지든 같은 결과를 내게 만든다. 단언하는 쪽도 같은
    함수를 통과시켜야 하므로 `assert normalize(needle) in normalize(haystack)` 형태로 쓴다.

    **공백을 지우는 것이 단언을 약하게 만들지 않는다** — 확인하려는 것은 "이 정보가
    메시지에 들어 있는가"이지 "줄바꿈이 어디 있는가"가 아니다. 오히려 개행 위치에
    의존하던 이전 단언이 **내용이 맞아도 실패**해서 계약을 검증하지 못했다.
    """
    return re.sub(r"\s+", "", strip_rich_decoration(text))
