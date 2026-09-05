"""코사인 유사도 (FR-4.2 · 설계 §5.1).

**손으로 계산한 값과 대조한다.** 라이브러리를 부르지 않으므로 기댓값을
구현과 같은 방법으로 만들면 서로를 검증하지 못한다.
"""

from __future__ import annotations

import math

import pytest

from cuesift.embed import cosine


def test_같은_벡터는_1이다():
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_직교_벡터는_0이다():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_반대_방향은_음수다():
    # **치역이 [-1, 1]이라는 것이 이 테스트의 요점이다.** 문자 단위
    # `similarity`는 [0, 1]이었고, 신호가 쓰는 clamp가 이제 실제로
    # 값을 자른다 (설계 §5.1).
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_손계산값과_일치한다():
    # a·b = 1*3 + 2*4 = 11,  |a| = sqrt(5),  |b| = 5
    expected = 11.0 / (math.sqrt(5.0) * 5.0)
    assert cosine([1.0, 2.0], [3.0, 4.0]) == pytest.approx(expected)


def test_영벡터는_거부한다():
    # **0.0을 내면 "완전히 다르다"로 읽혀 위험도 1.0이 된다.** 실제로는
    # 방향이 없어 판정이 불가능한 것이고, 둘은 다르다.
    with pytest.raises(ValueError, match="영벡터"):
        cosine([0.0, 0.0], [1.0, 2.0])


def test_차원이_다르면_거부한다():
    # 임베딩 모델이 바뀌면 차원이 달라진다. 조용히 짧은 쪽에 맞추면
    # 캐시나 설정 실수가 "유사도가 낮다"로 위장된다.
    with pytest.raises(ValueError, match="차원"):
        cosine([1.0, 2.0], [1.0, 2.0, 3.0])
