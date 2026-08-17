"""문자 단위 유사도 (설계 §9 · §3.2)."""

from __future__ import annotations

from cuesift.signals.similarity import similarity

# 설계 §3.2의 실측 7쌍.
#
# **정확한 소수점이 아니라 순서 관계를 검사한다.** difflib 구현이 바뀌면
# 소수점은 흔들리지만 "negation과 paraphrase가 분리되지 않는다"는 주장은
# 그대로여야 한다. 그 주장이 §12 Q4가 열려 있는 근거다.
NEGATION = [
    ("I cannot agree with you", "I can agree with you"),
    ("それはできません", "それはできます"),
    ("He did not come to the party", "He came to the party"),
    ("彼は来なかった", "彼は来た"),
]
PARAPHRASE = [
    ("彼は来なかった", "彼は現れなかった"),
    ("He did not come to the party", "He didn't show up at the party"),
]
UNRELATED = [
    ("He did not come to the party", "The weather is nice today"),
]


def test_같은_문자열은_1이다():
    assert similarity("안녕하세요", "안녕하세요") == 1.0


def test_빈_문자열_쌍은_1이다():
    assert similarity("", "") == 1.0


def test_한쪽만_비면_0이다():
    assert similarity("", "안녕") == 0.0
    assert similarity("안녕", "") == 0.0


def test_전각과_반각을_같게_본다():
    """`struct.number_missing`의 전각 숫자 미탐과 같은 부류를 막는다."""
    assert similarity("１２３", "123") == 1.0


def test_무관한_문장이_가장_낮다():
    lowest_related = min(similarity(a, b) for a, b in NEGATION + PARAPHRASE)
    for a, b in UNRELATED:
        assert similarity(a, b) < lowest_related


def test_negation과_paraphrase가_분리되지_않는다():
    """설계 §3.2 — **이 테스트가 실패하면 Q4가 닫힌 것이다.**

    문자 단위 유사도로 의미 반전과 정상 변이가 갈린다면 임계값 하나로 두
    집단이 나뉜다. 착수 시점 실측은 갈리지 않음을 보였고(negation 0.727~0.930,
    paraphrase 0.759~0.800), 유사도 구현을 바꿀 때 이 테스트가 다시 물어본다.

    실패하면 지우지 말고 요구사항정의서 §12 Q4를 갱신할 것.
    """
    neg = [similarity(a, b) for a, b in NEGATION]
    para = [similarity(a, b) for a, b in PARAPHRASE]
    # 두 집단의 범위가 겹친다 = 어떤 임계값으로도 분리 불가.
    assert min(neg) < max(para)
    assert min(para) < max(neg)
