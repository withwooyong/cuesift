"""negation 정답지 잡음 분류 (FR-4.2 · 설계 §8.3).

이월 19번 감사가 ja negation 라벨의 상당수를 비문·부자연 일본어로 판정했으나
그 판정 규칙이 리포에 남지 않아 **정상 반전 부분집합에서의 Recall** 을 따로
낼 수단이 없었다. 이 모듈이 그 수단이다.

**CI 게이트로 쓰지 않는다.** 벤치 보고 지표로만 쓴다 - 규칙이 언어학적
휴리스틱이라 오분류 여지가 남기 때문이다(각 함수 docstring의 한계 참고).

판정은 mutated_text 문자열만 본다("Consumes: 없다" - 외부 자원 없이 순수
문자열 판정, 태스크 6 브리프 Interfaces). source_text 는 bench/run.py 의
dump_audit 가 내는 (원문, 역주입문) 쌍 인터페이스를 그대로 받기 위한 자리이며
현재 규칙은 사용하지 않는다.
"""

from __future__ import annotations

import re

CLEAN = "clean"
BROKEN_FIXED_FORM = "broken_fixed_form"
STRANDED_ADVERB = "stranded_adverb"
UNNATURAL = "unnatural"
NPI_STRANDED = "npi_stranded"
MULTI_NEGATION = "multi_negation"

# --- 영어(ASCII) 패턴 : 단어 경계(\b)가 실재하므로 쓸 수 있다 ---

# 문두 "Yet," 과 "but yet" 은 NPI가 아니라 접속사다(이월19 오탐 2건 실측:
# en-00508 "Yet, I can use the toilet." · en-01919 "...but yet they have...").
# 이 화이트리스트가 없으면 뒤의 NPI 판정이 이 접속사 용법의 "yet" 을
# 부정 호응이 끊어진 것으로 오판한다.
_EN_YET_CONJUNCTION = re.compile(r"^\s*yet\s*,|\bbut yet\b", re.IGNORECASE)

# 부정 표지. 축약형은 앞 글자가 이미 단어 문자라 n 앞에 경계가 없다 -
# \bn't\b 는 don't·doesn't·didn't·isn't 를 전부 놓친다. 그래서 선행 경계 없이
# 접미 n't\b 만 요구한다(뒤는 공백·구두점이 오므로 경계가 실재한다).
_EN_NEGATION = re.compile(r"\bnot\b|n't\b|\bnever\b|\bno\b|\bnothing\b|\bnone\b", re.IGNORECASE)

# 부정과 호응해야 하는 극성어(NPI). 문장 어디에도 부정이 없으면 좌초된 것이다.
# en-04438 "we did study any of these animals." 실측 - any 호응 부정이 통째로
# 빠졌다(원문은 "연구를 하지 않았습니다").
_EN_NPI = re.compile(r"\bany\w*\b|\beither\b|\bat all\b|\byet\b", re.IGNORECASE)

# en-01164 "I don't know, but we certainly can if we don't try." 실측 -
# can't 하나만 제거되고 don't 가 둘 남아 부정 표지 카운트(len(negations)>=2)
# 로 multi_negation 이 걸린다(HANDOFF.md:185).

# --- 일본어(CJK) 패턴 : 단어 경계가 없다. 뒤따르는 글자를 배제해 이웃 어절을
# 물지 않게 한다(이월19 오탐 5건 - 「しか」가 「しかし」를 잡았다) ---

# 접속사 「しかし」제외. 「しか」단독은 부정 술어와 호응해야 하는 부사(~만)다.
# 「全く」・「ほとんど」도 같은 성질의 호응부사(HANDOFF.md:79 부류 C - 표본
# 71건 중 11건, 전체 313건 중 30건)이지만 「しかし」같은 충돌어가 없어
# 부정선행배제가 필요 없다. ja-02266 "全く分かります"(원문은 "전혀
# 모릅니다")・ja-03950 "ほとんど登場しました"(원문은 "언급이 거의
# 없었습니다") 실측.
_JA_STRANDED_ADVERB = re.compile(r"しか(?!し)|全く|ほとんど")

# 「しか」가 호응해야 하는 부정 술어 어미. 이 중 하나가 있으면 정상 반전이다.
# 「ません」·「ない」는 실측 픽스처(ja-01114 등)로 검증됐다. 「ぬ」·「ず」
# 종결 분기는 문어체 부정 종결(예: 「知らず」)을 놓치지 않으려는 일반화이며
# **실측 픽스처 중 이 분기를 실제로 태운 것은 없다.** 제거하면 문어체 부정
# 종결을 놓쳐 stranded_adverb 오탐이 늘 수 있으므로 남겨두되, 근거 없음을
# 여기 남긴다.
_JA_NEGATIVE_PREDICATE = re.compile(r"ません|ない|ぬ(?:、|。|$)|ず(?:、|。|$)")

# 형용사 연용형(く) 뒤에 부정 「ありません」대신 긍정 「あります」가 붙은
# 깨진형. ja-04677 "面白くありますからね" 실측 시그니처(面白くありません→
# 面白くあります 로 부정 조동사만 긍정으로 치환된 결과)다.
# 한계: 「多くあります」(부사 多く+존재동사, 정상 문장)처럼 형용사 연용형이
# 아닌 부사 활용과 문자열로는 구분되지 않는다 - 실측 픽스처 밖의 위양성
# 가능성을 배제하지 못한다.
_JA_BROKEN_ADJECTIVE_NEGATIVE = re.compile(r"く(?:あります|ありました)")

# 부정 고정형 관용구가 긍정으로 깨진 경우(HANDOFF.md:79 부류 B, 형용사가
# 아니라 동사 활용). 「なければなりません」(반드시 ~해야 한다)이
# 「なければなります」로, 「とは限りません」(반드시 ~는 아니다)이
# 「とは限ります」로 깨진다 - 둘 다 부정 조동사 자리만 긍정으로 치환된
# 같은 유형의 결함이다. ja-00711 "注意しなければなります"・ja-04583
# "変わらなければなります"・ja-02250 "だけとは限ります"・ja-01891
# "そうとは限ります" 실측. ja-01750 "なければ なります" 처럼 공백이 낀
# 표기도 있어(자막 포맷 변이) 공백을 허용한다 - 개행은 아니므로
# _strip_newlines 대상이 아니고 여기서 직접 흡수해야 한다.
_JA_BROKEN_MODAL_IDIOM = re.compile(r"なければ\s*なります|とは限ります")

# 코퍼스 「ではありません/でもありません」이 부정 조동사만 긍정으로 치환된
# 깨진형(HANDOFF.md:79 부류 D - 표본 71건 중 11건, 전체 313건 중 62건으로
# 결함 세 부류 중 가장 크다). 비문은 아니고 부자연한 것 - broken_fixed_form
# 과 달리 문법적으로는 성립해 별도 카테고리(unnatural)로 둔다.
# ja-02039 "これは地下水ではあります"・ja-02488 "言うまでもあります"
# (でも 변이)・ja-04450 "わけでもあります" 실측. 「じゃ」구어형은 71건
# 표본에서 발견되지 않아 이 정규식에 넣지 않았다 - 지어내지 않는다.
_JA_UNNATURAL_COPULA = re.compile(r"で[はも]あります")

# 부정 표지 총계. 2회 이상이면 중복 부정으로 의미가 흔들린 것으로 본다.
# 「かないますし」처럼 고정 관용구(適う 활용)에 우연히 "ない" 부분 문자열이
# 실린 경우 1회로는 걸리지 않게 임계값을 2로 둔다(ja-01081 실측).
_JA_NEGATIVE_COUNT = re.compile(r"ません|ない")


def _strip_newlines(text: str, lang: str) -> str:
    """자막 개행을 지운 판정용 사본을 만든다.

    자막은 화면 폭에 맞춰 어절 중간에서 줄바꿈된다(이월19 ja-02192 실측 -
    「かもし」+개행+「れません」이 원래 한 단어 「かもしれません」였다).

    ja: 어절 경계가 문자 자체에 없으므로 그냥 이어붙인다 - 공백을 넣으면
    「かもし」+「れません」자리가 공백으로 갈라져 오히려 매칭이 깨진다.
    en: 줄바꿈은 화면 폭 제한상 보통 공백 자리를 대신하므로 스페이스로
    치환한다 - 그냥 이어붙이면 "but yet\\nthey" 가 "yetthey" 로 붙어
    단어 경계(\\b) 매칭 자체가 깨진다.
    """
    if lang == "ja":
        return text.replace("\n", "")
    return text.replace("\n", " ")


def classify(source_text: str, mutated_text: str, lang: str) -> str:
    """negation 뮤테이션 결과를 6종 카테고리 중 하나로 분류한다(FR-4.2).

    반환값: "clean"(정상 반전) · "broken_fixed_form" · "stranded_adverb" ·
    "unnatural" · "npi_stranded" · "multi_negation" 중 하나.

    source_text 는 현재 규칙에서 쓰이지 않는다 - 모듈 docstring의 인터페이스
    계약 설명을 참고.
    """
    del source_text  # 인터페이스 계약 유지용 - 위 독스트링 참고

    if lang == "en":
        text = _strip_newlines(mutated_text, lang)
        return _classify_en(text)
    if lang == "ja":
        text = _strip_newlines(mutated_text, lang)
        return _classify_ja(text)
    return CLEAN


def _classify_en(text: str) -> str:
    if _EN_YET_CONJUNCTION.search(text):
        return CLEAN
    negations = _EN_NEGATION.findall(text)
    if len(negations) >= 2:
        return MULTI_NEGATION
    if _EN_NPI.search(text) and not negations:
        return NPI_STRANDED
    return CLEAN


def _classify_ja(text: str) -> str:
    # **분기 순서가 곧 부류의 우선순위다** (HANDOFF.md:79 의 부류 표).
    # 한 문장이 여러 부류를 만족할 때 더 심한 결함을 반환한다 - 부류 B·C 는
    # 비문(`なければなります`·`全く分かります`)이고 부류 D 는 문법에 맞되
    # 부자연할 뿐이라, D 를 C 보다 앞에 두면 비문이 부자연으로 내려가
    # 결함의 크기가 과소 보고된다.
    #
    # **오늘의 데이터로는 이 순서를 검증할 수 없다.** ja negation 라벨 71건에
    # 두 부류가 겹치는 세그먼트가 0건이라(2026-09-05 실측) 순서를 뒤집어도
    # 분포가 15/45/6/5 그대로다. 규칙을 넓히는 날 그 0건이 깨지므로, 순서는
    # `test_한_문장이_두_부류를_만족하면_더_심한_쪽으로_분류된다` 가 합성
    # 문자열로 고정한다.
    #
    # `multi_negation` 이 마지막인 이유는 부정 표지의 **개수만** 세는 가장
    # 약한 신호이기 때문이다 - 앞의 셋은 깨진 형태를 특정한다.
    if _JA_BROKEN_ADJECTIVE_NEGATIVE.search(text) or _JA_BROKEN_MODAL_IDIOM.search(text):
        return BROKEN_FIXED_FORM
    if _JA_STRANDED_ADVERB.search(text) and not _JA_NEGATIVE_PREDICATE.search(text):
        return STRANDED_ADVERB
    if _JA_UNNATURAL_COPULA.search(text):
        return UNNATURAL
    if len(_JA_NEGATIVE_COUNT.findall(text)) >= 2:
        return MULTI_NEGATION
    return CLEAN
