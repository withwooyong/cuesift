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

import re

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
