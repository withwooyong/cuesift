"""프롬프트 조립 (FR-2.2 맥락 윈도우, FR-2.3 용어집, FR-2.8 작품 맥락)."""

from __future__ import annotations

import pytest

from cuesift.glossary import Glossary, GlossaryEntry
from cuesift.segment.models import Segment
from cuesift.translate.prompt import build_messages


def _numberless_lines(user_content: str) -> list[str]:
    """번호도 헤더도 아닌 줄. 하나라도 있으면 세그먼트 경계가 흐려진다.

    "한 줄이 세그먼트 하나"가 이 프롬프트의 전제다. 번호가 붙지 않은 줄이
    나가면 모델이 그 줄을 앞 세그먼트에 붙일지 새 것으로 볼지 추론해야 한다.
    """
    return [
        line
        for line in user_content.splitlines()
        if line and not line.startswith("[") and not line.startswith("## ")
    ]


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
    # 멤버십만 보면(`"ko" in ...`) source와 target을 뒤바꾼 변이가 통과한다.
    # 그 증상은 조용하다 - 모델이 "ja 자막을 ko로"를 받고 한국어 원문을
    # 한국어로 되돌리는데, 개수도 번호도 맞으므로 파서도 엔진도 못 잡는다.
    # 그래서 방향까지 묶어 단언한다.
    messages = build_messages([_seg(0, "안녕")], source_lang="ko", target_lang="ja")
    assert "ko 자막을 ja로" in messages[0].content


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


def test_번역_대상_절의_헤더_문구를_고정한다() -> None:
    # 이 문구가 대상 절과 맥락 절을 가르는 유일한 표지다. 프롬프트를 되읽어
    # 대상 구간을 찾는 쪽(엔진 테스트의 가짜 프로바이더)이 이것을 키로 삼으므로,
    # 바꾸면 이 모듈은 초록인 채 그쪽이 빈 목록을 내고 원인이 보이지 않는다.
    body = build_messages([_seg(0, "가")], source_lang="ko", target_lang="en")[1].content
    assert "## 번역 대상" in body


def test_여러_줄_자막이_번호_없는_줄로_새지_않는다() -> None:
    # 두 줄 자막은 한국어 자막의 기본 형태다. 이스케이프하지 않으면 둘째 줄이
    # 번호 없이 나가고, 프롬프트를 되읽는 파서가 그 줄을 조용히 버린다
    # (개수도 번호도 맞으므로 아무 테스트도 빨개지지 않는다).
    body = build_messages(
        [_seg(10, "첫째 줄입니다\n둘째 줄입니다"), _seg(11, "다음 세그먼트")],
        source_lang="ko",
        target_lang="en",
    )[1].content
    # 본문이 통째로 비어도 아래 목록 비교는 통과한다. 실제 표기를 먼저 못 박는다.
    assert "[10] 첫째 줄입니다\\n둘째 줄입니다" in body
    assert _numberless_lines(body) == []


def test_맥락_세그먼트의_줄바꿈도_이스케이프한다() -> None:
    # 맥락에서 새면 번호 경계가 똑같이 흐려진다. 대상 절만 막으면 반쪽이다.
    body = build_messages(
        [_seg(10, "대상")],
        before=[_seg(9, "앞 첫째\n앞 둘째")],
        after=[_seg(11, "뒤 첫째\n뒤 둘째")],
        source_lang="ko",
        target_lang="en",
    )[1].content
    assert "[9] 앞 첫째\\n앞 둘째" in body
    assert "[11] 뒤 첫째\\n뒤 둘째" in body
    assert _numberless_lines(body) == []


def test_세그먼트_하나가_한_줄이라고_지시한다() -> None:
    # 이스케이프만 하고 규칙을 안 주면 모델이 번역문에서 두 줄로 되돌린다.
    system = build_messages([_seg(0, "가")], source_lang="ko", target_lang="en")[0].content
    assert "한 줄이다" in system


def test_주어진_항목만_번역하라고_지시한다() -> None:
    # 빠지면 모델이 맥락까지 번역해 여분의 id가 돌아오고 배치가 통째로 폐기된다.
    system = build_messages([_seg(0, "가")], source_lang="ko", target_lang="en")[0].content
    assert "주어진 항목만" in system


def test_번호를_그대로_쓰라고_지시한다() -> None:
    # 빠지면 id 누락·여분으로 배치가 폐기된다. 번호가 응답을 세그먼트로
    # 되돌리는 유일한 수단이라 하나만 어긋나도 배치 전체가 못 쓰게 된다.
    system = build_messages([_seg(0, "가")], source_lang="ko", target_lang="en")[0].content
    assert "빠뜨리거나 더하지 마라" in system


def test_JSON_하나로만_내라고_지시한다() -> None:
    # "JSON을 포함한다" 정도로 약해지면 산문 머리말이 붙어 파싱이 실패한다.
    # response_format을 쓰지 않으므로(설계 §4.3) 이 문장이 유일한 방어다.
    system = build_messages([_seg(0, "가")], source_lang="ko", target_lang="en")[0].content
    assert "다른 말 없이 JSON 하나로만" in system


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


def test_id를_따옴표_없는_정수로_쓰라고_지시한다() -> None:
    """실측 근거: qwen2.5:3b가 `{"id": "0"}`을 낸다 (2026-08-16 live 실행).

    파서는 id에 정수를 요구하는데(`batch.py`의 `_check_contract`) 이 프롬프트는
    `<번호>`라는 자리표시자만 주고 **타입을 말하지 않았다.** 모델이 문자열을
    고른 것은 지시 위반이라기보다 명세의 공백이다.

    실측(각 3회, temperature=0): 자리표시자만 있는 현행은 3/3 실패,
    예시를 정수 리터럴로 바꾸거나 타입 문장을 더하면 3/3 성공.

    타입 문장과 정수 리터럴 예시를 **둘 다** 거는 것은 각각이 단독으로도
    충분했기 때문이다 - 어느 쪽이 효과를 냈는지 모델마다 다를 수 있어
    하나만 남기면 다른 모델에서 되돌아갈 여지가 있다.

    파서가 정수로 왕복하는 문자열을 받아 주게 됐어도 이 지시는 남는다.
    파서 완화는 그물이고, 애초에 정수로 받는 편이 왕복 검사를 거치지 않는다.
    """
    system = build_messages([_seg(0, "가")], source_lang="ko", target_lang="en")[0].content
    # 예시가 정수 리터럴이어야 한다. `"id": "<번호>"`처럼 따옴표가 붙으면
    # 모델이 그 표기를 그대로 흉내 낸다.
    assert '{"id": 0,' in system
    assert "따옴표 없는 정수" in system


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
    #
    # **등재 순을 (탄소중립, 기후변화)로 두는 것이 이 테스트의 핵심이다.**
    # 뒤집으면(기후변화 먼저) 등재 순이 코드포인트 정렬 순과 같아져서
    # (기 U+AE30 < 탄 U+D0C4), 중간에 `sorted()`가 끼어도 통과한다.
    # 지금 데이터는 등재 순 · 언급 순 · 정렬 순이 **셋 다 다르다.**
    glossary = Glossary(
        entries=(
            GlossaryEntry(source="탄소중립", targets=("carbon neutrality",)),
            GlossaryEntry(source="기후변화", targets=("climate change",)),
        )
    )
    mentioned_ab = build_messages(
        [_seg(0, "기후변화 대응"), _seg(1, "탄소중립 목표")],
        source_lang="ko",
        target_lang="en",
        glossary=glossary,
    )[0].content
    mentioned_ba = build_messages(
        [_seg(0, "탄소중립 목표"), _seg(1, "기후변화 대응")],
        source_lang="ko",
        target_lang="en",
        glossary=glossary,
    )[0].content

    # 두 용어가 실제로 주입됐는지 먼저 본다. 하나도 안 들어갔다면 아래
    # 순서 단언과 문자열 비교가 둘 다 공허하게 통과한다.
    assert "carbon neutrality" in mentioned_ab and "climate change" in mentioned_ab
    assert mentioned_ab.index("carbon neutrality") < mentioned_ab.index("climate change")
    # 같은 용어 집합이면 언급 순서와 무관하게 같은 문자열이어야 한다.
    assert mentioned_ab == mentioned_ba


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


def test_배치_안에서_번호가_중복돼도_거부한다() -> None:
    # 같은 가드가 잡지만 여기엔 맥락이 아예 없다. 에러 문구가 맥락을 지목하면
    # 두 파일을 이어 붙인 입력을 디버깅할 때 맥락 윈도우를 엉뚱하게 뒤진다.
    with pytest.raises(ValueError, match="두 번 나온다"):
        build_messages([_seg(5, "가"), _seg(5, "나")], source_lang="ko", target_lang="en")
