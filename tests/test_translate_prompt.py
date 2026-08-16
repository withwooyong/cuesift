"""프롬프트 조립 (FR-2.2 맥락 윈도우, FR-2.3 용어집, FR-2.8 작품 맥락)."""

from __future__ import annotations

import pytest

from cuesift.glossary import Glossary, GlossaryEntry
from cuesift.segment.models import Segment
from cuesift.translate.prompt import build_messages


def _seg(index: int, text: str) -> Segment:
    return Segment(
        id=f"s{index}",
        index=index,
        start_ms=index * 1000,
        end_ms=index * 1000 + 900,
        source_text=text,
    )


def test_시스템과_유저_두_메시지를_낸다() -> None:
    messages = build_messages([_seg(0, "안녕")], source_lang="ko", target_lang="en")
    assert [m.role for m in messages] == ["system", "user"]


def test_언어쌍이_시스템_메시지에_들어간다() -> None:
    messages = build_messages([_seg(0, "안녕")], source_lang="ko", target_lang="ja")
    assert "ko" in messages[0].content
    assert "ja" in messages[0].content


def test_대상_세그먼트가_전역_인덱스로_표시된다() -> None:
    # 배치 내 지역 번호를 쓰면 맥락과 번호 공간이 갈라져 모델이 어느 것이
    # 대상인지 구별할 근거를 잃는다 (설계 §5.1).
    messages = build_messages([_seg(10, "가"), _seg(11, "나")], source_lang="ko", target_lang="en")
    assert "[10]" in messages[1].content
    assert "[11]" in messages[1].content


def test_앞뒤_맥락이_들어가되_번역대상과_구분된다() -> None:
    messages = build_messages(
        [_seg(10, "대상")],
        before=[_seg(9, "앞")],
        after=[_seg(11, "뒤")],
        source_lang="ko",
        target_lang="en",
    )
    body = messages[1].content
    assert "[9]" in body and "[11]" in body
    # 맥락은 번역하지 말라는 지시가 반드시 있어야 한다. 없으면 모델이
    # 맥락까지 번역해 개수 검증이 실패하고 폴백이 헛돈다.
    assert body.count("번역하지 말") >= 2


def test_맥락이_없으면_그_절을_넣지_않는다() -> None:
    # 빈 절을 넣으면 모델이 빈 지시를 해석하려 든다.
    body = build_messages([_seg(0, "가")], source_lang="ko", target_lang="en")[1].content
    # 유저 메시지가 통째로 비어도 아래 `not in`은 통과한다. 먼저 대상 절이
    # 실제로 조립됐는지 확인해 공허한 통과를 막는다.
    assert "[0] 가" in body
    assert "번역하지 말" not in body


def test_용어집은_배치에_등장하는_것만_넣는다() -> None:
    glossary = Glossary(
        entries=(
            GlossaryEntry(source="기후변화", targets=("climate change",)),
            GlossaryEntry(source="탄소중립", targets=("carbon neutrality",)),
        )
    )
    system = build_messages(
        [_seg(0, "기후변화 이야기")],
        source_lang="ko",
        target_lang="en",
        glossary=glossary,
    )[0].content
    assert "climate change" in system
    # 등장하지 않은 용어는 토큰 낭비다 (설계 §5.3).
    assert "carbon neutrality" not in system


def test_용어집이_비면_그_절을_넣지_않는다() -> None:
    system = build_messages(
        [_seg(0, "아무 말")],
        source_lang="ko",
        target_lang="en",
        glossary=Glossary(),
    )[0].content
    # 시스템 메시지 자체가 비면 아래 `not in`이 공허하게 통과한다.
    assert "번역가" in system
    assert "용어" not in system


def test_맥락에만_있는_용어도_주입한다() -> None:
    # 앞 맥락에 나온 용어를 모델이 번역 대상에서 대명사로 받을 수 있다.
    glossary = Glossary(entries=(GlossaryEntry(source="기후변화", targets=("climate change",)),))
    system = build_messages(
        [_seg(10, "그것은 시급하다")],
        before=[_seg(9, "기후변화 이야기")],
        source_lang="ko",
        target_lang="en",
        glossary=glossary,
    )[0].content
    assert "climate change" in system


def test_작품_맥락은_지정됐을_때만_들어간다() -> None:
    with_ctx = build_messages(
        [_seg(0, "가")], source_lang="ko", target_lang="en", work_context="1920년대 사극, 격식체"
    )[0].content
    without = build_messages([_seg(0, "가")], source_lang="ko", target_lang="en")[0].content
    assert "1920년대 사극" in with_ctx
    # `without`이 통째로 비어도 아래 `not in`은 통과한다.
    assert "번역가" in without
    assert "1920년대 사극" not in without


def test_JSON_응답_형식을_지시한다() -> None:
    # response_format을 쓰지 않기로 했으므로(설계 §4.3, T7) 프롬프트가
    # 유일한 형식 지시다. 이 지시가 빠지면 폴백이 상시 발동한다.
    system = build_messages([_seg(0, "가")], source_lang="ko", target_lang="en")[0].content
    assert "translations" in system
    assert "id" in system and "text" in system


def test_세그먼트를_합치거나_나누지_말라고_지시한다() -> None:
    # FR-2.4의 첫 방어선이다.
    system = build_messages([_seg(0, "가")], source_lang="ko", target_lang="en")[0].content
    assert "합치" in system and "나누" in system


def test_용어_블록은_언급_순서가_아니라_등재_순서로_직렬화된다() -> None:
    # 등재 순과 배치 언급 순을 일부러 **반대로** 짠다. 세그먼트를 돌며 나온
    # 순서대로 모으면 같은 용어 집합인데도 배치 내용에 따라 용어 블록의 줄
    # 순서가 달라진다. 그러면 시스템 메시지 문자열이 갈라져 프롬프트 프리픽스
    # 캐시가 무효화되고 재현성(NFR-3)이 깨진다.
    #
    # tests/test_glossary.py::test_terms_in_반환_순서는_용어집_등재_순이다가
    # 원문 하나에 대해 고정한 계약을, 이 테스트가 배치 단위로 이어받는다.
    glossary = Glossary(
        entries=(
            GlossaryEntry(source="기후변화", targets=("climate change",)),
            GlossaryEntry(source="탄소중립", targets=("carbon neutrality",)),
        )
    )
    listed_order = build_messages(
        [_seg(0, "기후변화 대응"), _seg(1, "탄소중립 목표")],
        source_lang="ko",
        target_lang="en",
        glossary=glossary,
    )[0].content
    reversed_order = build_messages(
        [_seg(0, "탄소중립 목표"), _seg(1, "기후변화 대응")],
        source_lang="ko",
        target_lang="en",
        glossary=glossary,
    )[0].content

    # 두 용어가 실제로 주입됐는지 먼저 본다. 하나도 안 들어갔다면 아래
    # 순서 단언과 문자열 비교가 둘 다 공허하게 통과한다.
    assert "climate change" in listed_order and "carbon neutrality" in listed_order
    assert listed_order.index("climate change") < listed_order.index("carbon neutrality")
    # 같은 용어 집합이면 언급 순서와 무관하게 같은 문자열이어야 한다.
    assert listed_order == reversed_order


def test_같은_용어가_여러_세그먼트에_나와도_한_번만_넣는다() -> None:
    # 중복 주입은 토큰 낭비이자 같은 지시의 반복이다. 배치가 커질수록
    # 용어 블록이 배치 길이에 비례해 부푼다.
    glossary = Glossary(entries=(GlossaryEntry(source="기후변화", targets=("climate change",)),))
    system = build_messages(
        [_seg(5, "기후변화 대응"), _seg(6, "기후변화 지연")],
        before=[_seg(4, "기후변화 서론")],
        source_lang="ko",
        target_lang="en",
        glossary=glossary,
    )[0].content
    assert system.count("climate change") == 1


def test_대응어가_여러_개면_전부_보여준다() -> None:
    # 용어집은 대응어 중 하나만 나와도 통과로 친다(cuesift.glossary 모듈
    # 독스트링). 하나만 주입하면 모델이 나머지를 쓸 수 없어 그 관용이 죽는다.
    glossary = Glossary(
        entries=(GlossaryEntry(source="기후변화", targets=("climate change", "climate crisis")),)
    )
    system = build_messages(
        [_seg(0, "기후변화 대응")],
        source_lang="ko",
        target_lang="en",
        glossary=glossary,
    )[0].content
    assert "climate change" in system and "climate crisis" in system


def test_빈_배치는_거부한다() -> None:
    # 그대로 조립하면 "번역할 것이 없는" 프롬프트가 완성되고, 모델은
    # {"translations": []}를 돌려주며, 개수 검증(기대 0 = 실제 0)도 통과한다.
    # 즉 배치 분할의 버그가 성공한 호출로 위장돼 요금만 나간다.
    with pytest.raises(ValueError, match="비어"):
        build_messages([], source_lang="ko", target_lang="en")


def test_맥락과_번역대상의_번호가_겹치면_거부한다() -> None:
    # 같은 번호가 "번역하지 말 것"과 "번역 대상"에 동시에 실린다. 전역 인덱스를
    # 쓰는 이유 자체(모델이 둘을 구별할 수 있게)가 무너지고, 모델이 그 번호를
    # 빠뜨리면 개수 불일치로 폴백이 상시 발동한다. 맥락 윈도우 계산의 off-by-one이
    # 이 형태로 나온다.
    with pytest.raises(ValueError, match="10"):
        build_messages(
            [_seg(10, "대상")],
            before=[_seg(9, "앞"), _seg(10, "대상")],
            source_lang="ko",
            target_lang="en",
        )
