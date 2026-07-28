"""텍스트의 표시 폭 계산 (요구사항정의서 FR-5.2, §8.3.1).

`char_counting`이 세 값을 갖는 이유는 언어마다 "한 자"의 뜻이 다르기 때문이다.
en 42자는 라틴 문자 42개, ko 16자는 한글 16자, ja 13자는 전각 13자다.

**`fullwidth`가 반각 라틴도 1.0으로 세는 것은 의도된 선택이다** (결정 D2).
`latin_half`와 같은 계산으로 두면 스키마에 이름이 둘일 이유가 없어지고,
ja 13자/줄은 세 언어 중 가장 좁아 관대한 계산이 화면 넘침으로 직결된다.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum

# East Asian Width가 이 값이면 전각으로 본다.
# W = Wide, F = Fullwidth.
# 유니코드의 Ambiguous 등급(A)은 라틴 악센트 문자(é, É 등)를 일관성 없이
# 분류하므로, 같은 글자의 대소문자가 다른 폭을 갖는 문제가 생긴다.
# 자막의 일관성이 CJK 폰트 렌더링 근사보다 중요하므로, latin_half에서는
# 실제 전각(W·F)만 1.0으로 센다.
_WIDE = frozenset({"W", "F"})


class CharCounting(StrEnum):
    """줄 길이·CPS를 셀 때 쓰는 규칙 (§8.3.1).

    Q5 조사에서 `grapheme`|`cjk_width` 두 값으로는 ko를 표현할 수 없어
    현재의 세 값으로 재정의했다.
    """

    grapheme = "grapheme"
    latin_half = "latin_half"
    fullwidth = "fullwidth"


def _visible_chars(text: str) -> list[str]:
    """결합 문자(악센트 등)를 제외한, 화면에서 자리를 차지하는 문자만 남긴다.

    표준 라이브러리에는 완전한 자소 클러스터 분할이 없다. `regex` 패키지가
    필요하지만 이 프로젝트는 의존성을 늘리지 않는다(Global Constraints).
    `unicodedata.combining()`으로 결합 표시를 걸러내는 근사를 쓴다.

    **한계**: 이모지 ZWJ 시퀀스(가족 이모지 등)는 여전히 여러 자로 센다.
    자막 텍스트에서는 드물고, 과대평가는 규격을 보수적으로 만드는 방향이라
    화면 넘침을 유발하지 않는다.
    """
    return [ch for ch in unicodedata.normalize("NFC", text) if unicodedata.combining(ch) == 0]


def text_width(text: str, mode: CharCounting) -> float:
    """`mode` 규칙으로 `text`의 표시 폭을 잰다."""
    chars = _visible_chars(text)

    if mode is CharCounting.grapheme:
        return float(len(chars))

    if mode is CharCounting.fullwidth:
        # grapheme과 현재 같은 식이지만 분기를 합치지 않는다. grapheme이
        # 나중에 진짜 자소 클러스터 분할로 바뀌어도 fullwidth는 "보이는
        # 문자 = 전각 1"이라는 자체 정의를 유지해야 한다.
        return float(len(chars))

    # latin_half — 전각은 1.0, 그 외는 0.5.
    return sum((1.0 if unicodedata.east_asian_width(ch) in _WIDE else 0.5 for ch in chars), 0.0)
