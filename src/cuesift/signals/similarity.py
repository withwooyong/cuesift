"""문자 단위 유사도 (설계 §9 · FR-4.1).

**의미가 아니라 형태를 잰다.** 의미 반전과 정상 변이를 분리하지 못한다
(설계 §3.2의 실측 7쌍). 요구사항정의서 §12 Q4가 열려 있는 이유이며,
교체할 때는 이 함수 하나만 갈아 끼우면 된다.
"""

from __future__ import annotations

import difflib
import unicodedata


def similarity(a: str, b: str) -> float:
    """문자 단위 유사도 0.0~1.0 (FR-4.1).

    **단어로 나누지 않는 이유는 ja에 공백이 없기 때문이다.** 단어 경계
    분할이 CJK를 전부 깨뜨린 전례가 이 저장소에 있다.

    NFKC로 정규화하지 않으면 전각·반각이 다른 문자가 되어, 같은 번역이
    표기 폭 하나로 "흔들렸다"고 판정된다 - `struct.number_missing`의 전각
    숫자 미탐과 같은 부류다.

    `autojunk=False`가 아니면 difflib이 200자 이상 입력에서 빈출 요소를
    junk로 취급해 유사도를 실제보다 낮게 낸다. 자막 한 줄은 짧지만
    `detail`에 담기는 문자열은 길어질 수 있다.

    엄밀히는 편집거리(Levenshtein)가 아니라 Ratcliff-Obershelp다. 직접
    구현하지 않는 것은 표준 라이브러리에 검증된 것이 있는데 새로 쓰면
    버그 위험만 늘기 때문이다.
    """
    na = unicodedata.normalize("NFKC", a)
    nb = unicodedata.normalize("NFKC", b)
    if na == nb:
        # 빈 문자열 쌍도 여기서 1.0이 된다 - 둘 다 "같다"가 맞다.
        return 1.0
    if not na or not nb:
        # 한쪽만 비면 공통 부분이 없다. difflib도 0.0을 내지만 명시하는
        # 편이 경계 조건을 코드에 남긴다.
        return 0.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()
