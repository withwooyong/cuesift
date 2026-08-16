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
import shlex
import tomllib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

_GATE_HINT = (
    " || 이 검사는 `live` 마커 게이트를 지킨다(설계 §9.2). "
    "`-c`·`--override-ini`로 임시로 덮는 것도 여기 걸린다 - "
    "설정을 바꾸려면 pyproject.toml을 직접 고쳐라."
)


def _markexpr_problems(addopts: list[str]) -> list[str]:
    """`-m` 기본 제외식이 온전한가. **두 자리에서 부른다** (아래 참조)."""
    if addopts.count("-m") != 1:
        # 뒤의 `-m`이 이기므로 첫 번째만 보는 검사는 덧붙이기로 뚫린다.
        return [f"addopts의 -m이 하나가 아니다(뒤가 이긴다): {addopts}"]
    if addopts[addopts.index("-m") + 1] != "not live":
        return [f"addopts의 기본 제외식이 'not live'가 아니다: {addopts}"]
    return []


def _check_on_import() -> None:
    """**임포트되는 것만으로 돈다.** 훅도 아니고 테스트도 아니다.

    아래 `pytest_configure`가 이미 같은 것을 보는데 왜 또 보는가 - **두
    방어선의 실패 모드가 다르기 때문이다.** 실측(2026-08-16):

    | 변이 | 훅 | 이 검사 |
    | --- | --- | --- |
    | `addopts`에 `-m live` 덧붙임 | 잡는다 (exit 4) | 잡는다 |
    | 훅 함수 개명(`pytest_` 접두사 제거) | **무력화** — pluggy가 등록하지 않는다 | 무관 |
    | 위 **둘을 동시에** | 무력화 | **잡는다** |

    셋째 행이 이 함수의 존재 이유다. 훅이 끊기면 `-m live` 덧붙임이 되살아나고,
    그것을 감시하는 테스트(`test_게이트_훅이_pytest에_실제로_등록돼_있다`)마저
    840개와 함께 deselect되어 **exit 0으로 초록이 난다**(실측). 모듈 최상위
    코드는 **개명할 이름도 없고 deselect의 대상도 아니라서** 그 조합에서
    유일하게 살아남는다.

    대신 `config`가 없어 pytest가 **실제로 읽은** ini가 무엇인지는 모른다 -
    그쪽은 훅만 볼 수 있다. 그래서 둘 다 필요하고, 둘은 서로 다른 경로로
    같은 사실에 도달한다(이쪽은 pyproject 원문 + `shlex`, 훅은 `getini`).
    """
    ini = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = ini["tool"]["pytest"]["ini_options"]["addopts"]
    problems = _markexpr_problems(shlex.split(addopts))
    if problems:
        raise pytest.UsageError("게이트 설정이 어긋났다: " + " / ".join(problems) + _GATE_HINT)


_check_on_import()


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
    #
    # **양쪽 다 `resolve()`해야 한다.** `_REPO_ROOT`는 `Path(__file__).resolve()`라
    # 링크가 풀려 있는데 `config.inipath`는 pytest의 `absolutepath()`에서 와
    # 풀리지 않는다. 맞추지 않으면 리포를 junction·`subst` 드라이브·symlink
    # 경유로 열었을 때 **정상 설정에서도 exit 4**가 난다.
    if config.inipath is None or config.inipath.resolve() != (_REPO_ROOT / "pyproject.toml"):
        problems.append(f"pytest가 읽은 설정이 우리 pyproject가 아니다: {config.inipath}")

    # 미등록 마커를 에러로 만드는 플래그. 꺼지면 설계 §9.2의 전제가 무너진다.
    if not config.getoption("strict_markers"):
        problems.append("--strict-markers가 꺼졌다")

    # **다시 shlex로 쪼개면 안 된다.** `getini("addopts")`는 pytest가 이미
    # 분리해 둔 리스트라, 합쳤다 다시 나누면 `"not live"`가 `"not"`과
    # `"live"` 두 토큰이 되어 아래 비교가 정상 설정에서도 실패한다(실측).
    # `_check_on_import`가 pyproject 원문을 shlex로 읽는 것과 **다른 경로**로
    # 같은 사실에 도달한다.
    problems += _markexpr_problems(list(config.getini("addopts")))

    if problems:
        # 힌트를 함께 낸다. `-c other.ini`나 `--override-ini=addopts=`도 여기
        # 걸리는데(설계 의도다), 그 사실을 모르는 사람은 "게이트 설정이
        # 어긋났다"만 보고 **자기가 뭔가 깼다고 오해한다.**
        raise pytest.UsageError("게이트 설정이 어긋났다: " + " / ".join(problems) + _GATE_HINT)


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
