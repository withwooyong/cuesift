"""극성 표지 판정 테스트 (설계 2026-09-06 §4.1 · D4)."""

import pytest

from cuesift.polarity import has_polarity_marker, supported_languages


def test_지원_언어는_셋이다():
    assert supported_languages() == frozenset({"ko", "en", "ja"})


def test_None과_빈_문자열은_표지가_없다():
    assert not has_polarity_marker(None, "ko")
    assert not has_polarity_marker("", "ko")
    assert not has_polarity_marker("   ", "ko")


def test_미지원_언어는_항상_False다():
    # 프랑스어 부정문이지만 목록이 없으므로 판정하지 않는다 (D5).
    assert not has_polarity_marker("Je ne sais pas", "fr")


@pytest.mark.parametrize(
    "text",
    ["그렇지 않습니다", "아무도 없다", "그건 아니라고 봅니다", "안 갔어요", "못 했습니다"],
)
def test_한국어_표지를_잡는다(text):
    assert has_polarity_marker(text, "ko")


def test_못은_띄어쓰기_변이를_모두_잡는다():
    # `못하` 와 `못\s` 가 각각 한쪽을 맡는다. 하나만 있으면 반쪽이 샌다.
    #
    # **`못했습니다`·`못합니다` 를 쓰면 안 된다.** `하`(U+D558)에 받침이 붙거나
    # (`합` U+D569) 활용되면(`했` U+D588) 별개 완성형이 되어 `못하` 가 닿지
    # 못한다. 실측으로 걸러 `못하다` 를 골랐다.
    assert has_polarity_marker("못하다", "ko")
    assert has_polarity_marker("못 했습니다", "ko")


@pytest.mark.parametrize(
    "text",
    ["I can't do that", "It is NOT true", "there is no way", "never mind", "without doubt"],
)
def test_영어_표지를_잡는다(text):
    assert has_polarity_marker(text, "en")


def test_영어는_대소문자를_가리지_않는다():
    assert has_polarity_marker("NEVER", "en")


def test_영어_단어_경계가_있다():
    # `no` 가 `nostalgia` 를 물면 안 된다.
    assert not has_polarity_marker("nostalgia", "en")


@pytest.mark.parametrize("text", ["わかりません", "行かなかった", "それはない", "行かずに"])
def test_일본어_표지를_잡는다(text):
    assert has_polarity_marker(text, "ja")


def test_しか는_부정_호응이라_표지다():
    # 다른 표지가 섞이지 않은 문장이어야 `しか` 자체를 검사한다.
    assert has_polarity_marker("残りは一つしか", "ja")


def test_접속사_しかし는_표지가_아니다():
    # CJK 는 단어 경계가 없어 `しか` 가 `しかし` 를 문다 (D4 · CLAUDE.md 실측 전례).
    assert not has_polarity_marker("しかし、それは違います", "ja")


def test_개행이_표지를_쪼개도_잡는다():
    # **표지 자체가 쪼개지는 문자열이어야 게이트가 성립한다.**
    # `かもし\nれません` 은 `ません` 이 통째로 남아 개행을 안 지워도
    # 잡히므로 이 게이트를 검사하지 못한다(실측으로 걸렀다).
    assert has_polarity_marker("かもしれま\nせん", "ja")
    assert has_polarity_marker("아\n니", "ko")


def test_개행을_지우면_오히려_잃는_것도_잡는다():
    # 개행을 지우면 단어가 붙어 `\b` 경계가 깨진다 - `do\nnot` -> `donot`.
    # 실측: en 트랙 번역문 52건이 그 경로로 사라진다. 그래서 원본도 함께 본다.
    assert has_polarity_marker("do\nnot go", "en")
    assert has_polarity_marker("안\n갔다", "ko")


def test_개행이_배제_룩어헤드를_뚫지_못한다():
    # 원본을 보면 개행이 배제 글자를 가려 룩어헤드가 통과한다.
    # D4 가 막으려던 오탐이 되살아나므로 ja 는 원본 패스에서 뺀다.
    assert not has_polarity_marker("しか\nし、それは違います", "ja")
    assert not has_polarity_marker("無\n限に広がる", "ja")
    assert not has_polarity_marker("不\n思議な話", "ja")


def test_원본_패스_언어는_지원_언어의_부분집합이다():
    # 실제 상수를 임포트해 검사한다. 목록을 지어 넘기면 코드와 갈라진다.
    from cuesift.polarity import _RAW_PASS

    assert supported_languages() >= _RAW_PASS


def test_어휘_목록을_테스트가_지어_넘기지_않는다():
    # 실제 패턴을 임포트해 검사한다. 테스트 안에서 만든 정규식을 검사하면
    # 코드와 갈라져도 통과한다 (리포트 caveat 전례).
    from cuesift.polarity import _PATTERNS

    assert set(_PATTERNS) == supported_languages()
    assert _PATTERNS["ja"].search("残りは一つしか") is not None
