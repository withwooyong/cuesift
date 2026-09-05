"""negation 정답지 잡음 분류 (FR-4.2 · 설계 §8.3).

**아래 픽스처는 이월 19번이 원문 대조로 잡은 실제 오탐·미탐이다.**
`bench/run.py` 의 `dump_audit` 가 만드는 감사 산출물 `data/bench/{ja,en}-ko.injected.json`
(2026-09-04 스냅샷, gitignore 대상)에서 세그먼트 id 로 직접 재확인한 값을 문자열로
고정한다 - data/ 는 CI 에 없으므로 테스트 실행 시점에 그 파일을 읽지 않는다.
규칙을 다시 짜면 여기서 먼저 걸린다.

미탐 4건 중 「〜ませんか」류(부정 의문형이 자격 자체가 아닌 경우) 픽스처는 의도적으로
없다. 그 부류는 `bench/inject.py` 의 `_JA_NEGATIVE_QUESTION` 이 **주입 단계에서** 배제해
애초에 negation 정답지에 들어오지 않는다(이월 19 · 커밋 5541277). 그 배제 자체는
`tests/test_bench_inject.py` 가 실제 문장 네 건으로 이미 고정한다. **그 배제가 사라지면
이 파일의 분류기에는 이 부류를 잡는 규칙이 없다** - `classify()` 는 부정 의문형이
정답지에 아예 오지 않는다는 것을 전제로 짜여 있다.

아래쪽 `test_ja_negation_라벨_전수_분포_점검에서_찾은_추가_실측` 은 HANDOFF.md:79 부류
표(B 고정형 파손·C 호응부사 잔류·D `ではあります`)의 존재를 확인한 뒤, ja negation 라벨
71건 전체에 `classify()` 를 돌려 분포가 그 표(B=10·C=11·D=11·E=35, 표본 71건 기준)와
자릿수가 맞는지 점검하는 과정에서 추가로 찾은 실제 문장이다 - task-6-report.md 의
분포 비교 절 참고.
"""

from __future__ import annotations

import pytest
from bench.classify_negation import classify


@pytest.mark.parametrize(
    ("mutated", "lang"),
    [
        # 「しか」가 접속사「しかし」를 잡았다 - CJK에는 단어 경계가 없다.
        # ja-01089 실측(data/bench/ja-ko.injected.json). ja-03156 도 같은
        # 부류(しかし)의 실측이지만 「ではあります」(부류 D)를 함께 담고 있어
        # 전체 판정은 clean 이 아니라 unnatural 이다 - 아래
        # test_D부류_ではあります_계열은_unnatural이다 참고.
        ("しかし そう聞いて 違和感があっても\n問題あります", "ja"),
        # 문두 "Yet," 은 NPI가 아니라 접속사다. en-00508 실측
        # (data/bench/en-ko.injected.json).
        ("Yet, I can use the toilet.", "en"),
        # "but yet" 도 접속사다. en-01919 실측 - 자막 개행이 "yet"과 "they"
        # 사이에 있다.
        ("Most of these do contain DNA, but yet\nthey have lifelike properties.", "en"),
        # 앞 절 부정(didn't)이 살아 있으면 either는 여전히 호응 대상을 갖는다.
        # en-02045 실측.
        ("And here's -- this didn't do so well in\ntesting either, I do know why.", "en"),
        # 「礼にかなう」는 긍정형이 실제로 쓰인다 - 고정형이 아니다.
        # ja-01081 실측("礼にも道義にもかないますし").
        ("そんなことをしたら\n礼にも道義にもかないますし", "ja"),
    ],
)
def test_오탐이었던_문장은_clean이다(mutated, lang):
    assert classify("원문", mutated, lang=lang) == "clean"


@pytest.mark.parametrize(
    ("mutated", "lang", "expected"),
    [
        # NPI "any"가 부정과 호응하지 못했다(원문은 "연구를 하지
        # 않았습니다"). en-04438 실측.
        ("And we did study any of these animals.", "en", "npi_stranded"),
        # 「しか」가 부정 술어와 떨어졌다("選択肢があります"는 긍정형).
        # ja-01114 실측.
        ("行儀よく行動するしか選択肢があります", "ja", "stranded_adverb"),
        # 형용사 연용형+긍정(あります) - 부정(ありません)이 깨졌다.
        # ja-04677 실측.
        ("普通の車じゃ 面白くありますからね", "ja", "broken_fixed_form"),
    ],
)
def test_미탐이었던_문장을_잡는다(mutated, lang, expected):
    assert classify("원문", mutated, lang=lang) == expected


@pytest.mark.parametrize(
    ("mutated", "expected"),
    [
        # HANDOFF.md:79 부류 C 예시("全く分かります")와 정확히 같은 유형 -
        # 「全く」・「ほとんど」도 부정과 호응해야 하는 부사다. ja-02266
        # 실측(원문 "전혀 모릅니다").
        ("私には全く分かります", "stranded_adverb"),
        # ja-03950 실측(원문 "언급이 거의 없었습니다", 자막 개행 포함).
        ("手紙には 偉大なる指導者は\nほとんど登場しました", "stranded_adverb"),
        # HANDOFF.md:79 부류 B 예시("なければなります")와 같은 유형 - 동사
        # 활용 고정 관용구가 부정 조동사만 긍정으로 치환됐다. ja-00711 실측
        # (원문 "고려했어야 합니다", 자막 개행 포함).
        ("天気予報士は\nその規則に注意しなければなります", "broken_fixed_form"),
        # ja-01750 실측 - なければ와 なります 사이에 공백이 낀 표기 변이.
        (
            "受かるためには 中高のカリキュラムを\n３か月でマスターしなければ なります",
            "broken_fixed_form",
        ),
        # 「とは限りません」(반드시 ~는 아니다)이 「とは限ります」로 깨진
        # 같은 부류. ja-02250 실측.
        ("カメラだけとは限ります", "broken_fixed_form"),
    ],
)
def test_ja_negation_라벨_전수_분포_점검에서_찾은_추가_실측(mutated, expected):
    assert classify("원문", mutated, lang="ja") == expected


@pytest.mark.parametrize(
    "mutated",
    [
        # HANDOFF.md:79 부류 D - 부정 조동사 「ではありません」이 긍정
        # 「ではあります」로 치환된 깨진형. 비문은 아니고 부자연한 것이라
        # broken_fixed_form 과 별도 카테고리다. ja-02039 실측(단문).
        "これは地下水ではあります",
        # 같은 부류의 「でも」변이. ja-02488 실측(자막 개행 포함).
        "そんなこと当然ですよね\n言うまでもあります",
        # 「しかし」(접속사, stranded_adverb 아님)와 「ではあります」(부류 D)가
        # 한 세그먼트에 함께 있다 - ja-03156 실측. しか(?!し) 화이트리스트가
        # 이 세그먼트를 stranded_adverb 오탐에서는 구해내지만, 그렇다고
        # clean 은 아니고 unnatural 이 맞다는 것을 보이는 회귀 케이스다.
        "しかし実際にはそうではあります",
    ],
)
def test_D부류_ではあります_계열은_unnatural이다(mutated):
    assert classify("원문", mutated, lang="ja") == "unnatural"


def test_두_부정_표지가_남으면_multi_negation이다():
    # can't 하나만 제거되고 don't 가 둘 남았다(HANDOFF.md:185). en-01164 실측
    # (data/bench/en-ko.injected.json) - 자막 개행 포함.
    mutated = "I don't know, but we certainly can if we\ndon't try."
    assert classify("원문", mutated, lang="en") == "multi_negation"


def test_한_문장이_두_부류를_만족하면_더_심한_쪽으로_분류된다():
    """분기 순서가 곧 부류의 우선순위다 - 비문(B·C)이 부자연(D)을 이긴다.

    **아래 두 문장은 실측이 아니라 합성이다.** 이 파일의 다른 픽스처와 성질이
    다르므로 밝혀 둔다 - ja negation 라벨 71건에는 두 부류가 겹치는 세그먼트가
    0건이라(2026-09-05 실측, 순서를 뒤집어도 분포가 15/45/6/5 그대로였다) 실제
    문장으로는 이 순서를 고정할 수단이 없다.

    **고정하지 않으면 규칙을 넓히는 날 조용히 뒤집힌다.** 부류 D 는 문법에 맞되
    부자연할 뿐이라, 비문인 B·C 보다 먼저 잡으면 결함의 크기가 과소 보고된다.
    """
    # B(なければなります) + D(ではあります) - 비문인 B 가 이긴다
    both_b_and_d = "これはしなければなりますし それではあります"
    assert classify("원문", both_b_and_d, lang="ja") == "broken_fixed_form"
    # C(しか + 부정 서술어 없음) + D(ではあります) - 비문인 C 가 이긴다
    both_c_and_d = "それはしかではあります"
    assert classify("원문", both_c_and_d, lang="ja") == "stranded_adverb"


def test_어절_중간_줄바꿈에_뚫리지_않는다():
    # 자막은 화면 폭에 맞춰 어절 중간에서 줄바꿈된다. 판정은 개행을 지운
    # 사본에 해야 한다 - 이월 19번의 ja-02192가 정확히 이 경로였다
    # (「現れるかもし」+ 개행 + 「れません」 = 「現れるかもしれません」).
    broken = "これにはまた非線形な閾値効果が現れるかもし\nれません"
    joined = "これにはまた非線形な閾値効果が現れるかもしれません"
    assert classify("원문", broken, lang="ja") == classify("원문", joined, lang="ja")
