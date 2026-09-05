"""코사인 유사도 (FR-4.2 · 설계 §5.1).

**넘파이를 쓰지 않는다.** 의존성이 런타임 4개로 고정돼 있고, 벡터 하나가
1024차원이라 순수 파이썬으로도 벤치 최대 규모(1,000회)에서 무시할 수 있다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """두 벡터의 코사인 유사도 -1.0~1.0 (FR-4.2).

    **치역이 [-1, 1]이라 호출부의 clamp가 실제로 값을 자른다.** 문자 단위
    `signals.similarity`가 [0, 1]이었던 것과 다르고, `signals/llm.py`의
    clamp 주석이 §12 Q4가 닫히면 벌어질 일로 예견해 둔 상황이다.
    """
    if len(a) != len(b):
        # 임베딩 모델이 바뀌면 차원이 달라진다. 짧은 쪽에 맞춰 자르면
        # 설정 실수가 "유사도가 낮다"로 위장돼 위험도로 새어 든다.
        raise ValueError(f"차원이 다르다: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        # **0.0을 내면 "완전히 다르다"가 되어 위험도 1.0이 된다.**
        # 실제로는 방향이 없어 판정 불가이고, 판정 불가와 최고 위험은 다르다.
        raise ValueError("영벡터는 방향이 없어 코사인이 정의되지 않는다")
    return dot / (norm_a * norm_b)
