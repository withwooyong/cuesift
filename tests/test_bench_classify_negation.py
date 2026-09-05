"""negation 정답지 잡음 분류 (FR-4.2 · 설계 §8.3).

**아래 픽스처는 이월 19번이 원문 대조로 잡은 실제 오탐·미탐이다.**
`.superpowers/sdd/2026-09-05-backtranslation-signal/task-6-context.md` 의
판정에 따라 `data/bench/{ja,en}-ko.injected.json`(2026-09-04 감사 산출물,
gitignore 대상)에서 세그먼트 id 로 직접 재확인한 값을 문자열로 고정한다 -
data/ 는 CI 에 없으므로 테스트 실행 시점에 그 파일을 읽지 않는다.
규칙을 다시 짜면 여기서 먼저 걸린다.

미탐 4건 중 「〜ませんか」류(부정 의문형이 자격 자체가 아닌 경우)는 현재
data/bench/*.json 스냅샷(2026-09-04)의 negation 라벨 71+71건 전체를 훑어도
해당하는 실제 문장을 찾지 못해 픽스처에서 뺐다 - 지어내지 않았다.
task-6-report.md 참고.
"""

from __future__ import annotations

import pytest
from bench.classify_negation import classify


@pytest.mark.parametrize(
    ("mutated", "lang"),
    [
        # 「しか」가 접속사「しかし」를 잡았다 - CJK에는 단어 경계가 없다.
        # ja-03156 실측(data/bench/ja-ko.injected.json).
        ("しかし実際にはそうではあります", "ja"),
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


def test_어절_중간_줄바꿈에_뚫리지_않는다():
    # 자막은 화면 폭에 맞춰 어절 중간에서 줄바꿈된다. 판정은 개행을 지운
    # 사본에 해야 한다 - 이월 19번의 ja-02192가 정확히 이 경로였다
    # (「現れるかもし」+ 개행 + 「れません」 = 「現れるかもしれません」).
    broken = "これにはまた非線形な閾値効果が現れるかもし\nれません"
    joined = "これにはまた非線形な閾値効果が現れるかもしれません"
    assert classify("원문", broken, lang="ja") == classify("원문", joined, lang="ja")
