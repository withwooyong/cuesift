"""배치 분할과 응답 검증 (FR-2.4 경계 보존)."""

from __future__ import annotations

import json

import pytest

from cuesift.segment.models import Segment
from cuesift.translate.batch import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONTEXT_WINDOW,
    InvalidResponseError,
    iter_batches,
    parse_translations,
)


def _segs(n: int) -> list[Segment]:
    return [
        Segment(
            id=f"s{i}",
            index=i,
            start_ms=i * 1000,
            end_ms=i * 1000 + 900,
            source_text=f"문장{i}",
        )
        for i in range(n)
    ]


def test_기본_배치_크기는_10이다() -> None:
    # 폴백 비용의 비대칭 때문이다 - 배치 20이 깨지면 20회 개별 호출이
    # 되지만 10이 깨지면 10회다 (설계 §8.2).
    assert DEFAULT_BATCH_SIZE == 10
    assert DEFAULT_CONTEXT_WINDOW == 3


def test_배치를_크기대로_자른다() -> None:
    windows = list(iter_batches(_segs(25), size=10, context_window=0))
    assert [len(w.batch) for w in windows] == [10, 10, 5]


def test_빈_입력은_배치를_내지_않는다() -> None:
    assert list(iter_batches([], size=10, context_window=3)) == []


def test_첫_배치는_앞_맥락이_없다() -> None:
    first = next(iter(iter_batches(_segs(20), size=5, context_window=3)))
    assert first.before == ()
    assert [s.index for s in first.after] == [5, 6, 7]


def test_마지막_배치는_뒤_맥락이_없다() -> None:
    last = list(iter_batches(_segs(10), size=5, context_window=3))[-1]
    assert [s.index for s in last.before] == [2, 3, 4]
    assert last.after == ()


def test_맥락_윈도우가_입력보다_크면_있는_만큼만() -> None:
    # 슬라이스 음수 인덱스 사고를 막는다. before를 max(0,...) 없이
    # segments[start-cw:start]로 계산하면 음수 시작이 뒤에서부터 세어
    # 앞 맥락이 잘린다.
    #
    # context_window는 입력 수보다 **조금만** 커야 이 사고가 드러난다.
    # 10처럼 크게 잡으면 n+start-cw까지 음수가 되어 파이썬이 0으로 조여
    # 주고, max(0,...)이 없는 구현과 결과가 같아진다. 5에서만 갈린다
    # ([0,1]이어야 할 것이 [1]이 된다).
    for context_window in (5, 10):
        windows = list(iter_batches(_segs(4), size=2, context_window=context_window))
        assert windows[0].before == ()
        assert [s.index for s in windows[1].before] == [0, 1]


def test_맥락_윈도우_0이면_맥락이_없다() -> None:
    windows = list(iter_batches(_segs(10), size=5, context_window=0))
    # 개수를 먼저 못 박지 않으면 iter_batches가 아무것도 내지 않을 때
    # 루프 본문이 한 번도 돌지 않은 채 통과한다.
    assert len(windows) == 2
    for window in windows:
        assert window.before == () and window.after == ()


def test_어떤_설정에서도_배치와_맥락이_정의대로_나온다() -> None:
    # Task 3의 build_messages가 빈 배치를 ValueError로 거부하므로, 빈
    # 배치를 하나라도 내면 engine이 즉시 죽는다. 누락·중복·순서 뒤바뀜도
    # 같은 자리에서 걸린다.
    #
    # **맥락까지 전수로 보는 이유**: 맥락이 통째로 사라져도 어떤 게이트도
    # 울리지 않는다. 빈 맥락은 빈 배치도 번호 충돌도 아니라서 Task 3의
    # 가드 둘을 모두 지나가고, Tier 0 신호도 잡지 못한다. FR-2.2만 조용히
    # 무효가 된 채 번역 품질로만 나타난다. context_window=1·2가 여기
    # 없으면 "cw가 1이면 맥락을 비운다"는 회귀가 통과한다.
    #
    # 기대값은 max(0,...)를 쓰지 않는다 - 구현을 베끼면 같은 실수를 함께
    # 한다. 정의 그대로 앞은 접두의 마지막 cw개, 뒤는 접미의 처음 cw개다.
    checked = 0
    for n in range(26):
        segments = _segs(n)
        for size in range(1, 13):
            for cw in range(6):
                windows = list(iter_batches(segments, size=size, context_window=cw))
                assert [s.index for w in windows for s in w.batch] == list(range(n))
                assert all(w.batch for w in windows)
                start = 0
                for window in windows:
                    end = start + len(window.batch)
                    expected_before = [s.index for s in segments[:start]][-cw:] if cw else []
                    expected_after = [s.index for s in segments[end:]][:cw]
                    assert [s.index for s in window.before] == expected_before
                    assert [s.index for s in window.after] == expected_after
                    start = end
                checked += len(windows)
    # 올림 나눗셈은 배치 분할의 정의이지 구현에서 베껴 온 값이 아니다.
    # 이 단언이 없으면 iter_batches가 늘 빈 목록을 내도 위가 전부 통과한다.
    assert checked == 6 * sum(-(-n // size) for n in range(26) for size in range(1, 13))


def test_size가_1보다_작으면_반복_전에_거부한다() -> None:
    # 제너레이터 본문에서 검사하면 첫 next()까지 예외가 미뤄져, 설정값
    # 오타가 호출 지점에서 멀리 떨어진 곳에서 터진다.
    with pytest.raises(ValueError, match="size"):
        iter_batches(_segs(3), size=0)


def test_context_window가_음수면_반복_전에_거부한다() -> None:
    with pytest.raises(ValueError, match="context_window"):
        iter_batches(_segs(3), context_window=-1)


def test_정상_응답을_파싱한다() -> None:
    raw = json.dumps({"translations": [{"id": 0, "text": "hello"}, {"id": 1, "text": "world"}]})
    assert parse_translations(raw, [0, 1]) == {0: "hello", 1: "world"}


def test_응답_순서가_기대_순서와_달라도_번호로_짝짓는다() -> None:
    # **이 모듈이 존재하는 이유가 이 테스트다.** 반환을 위치 짝짓기
    # (dict(zip(sorted(expected), texts)))로 바꾸면 개수·번호 검증을 전부
    # 통과하면서 번역문이 다른 세그먼트에 붙는다. 응답 순서가 기대 순서와
    # 같은 데이터만 쓰면 그 구현이 구별되지 않는다.
    raw = json.dumps({"translations": [{"id": 1, "text": "B"}, {"id": 0, "text": "A"}]})
    assert parse_translations(raw, [0, 1]) == {0: "A", 1: "B"}


def test_코드_펜스를_벗겨_낸다() -> None:
    # 모델이 ```json 펜스를 두르는 것은 매우 흔하다. 형식의 껍데기지
    # 내용 계약 위반이 아니므로, 이것 때문에 폴백을 돌리면 비용만 든다.
    raw = '```json\n{"translations": [{"id": 0, "text": "hello"}]}\n```'
    assert parse_translations(raw, [0]) == {0: "hello"}


def test_언어_표기가_없는_펜스도_벗겨_낸다() -> None:
    raw = '```\n{"translations": [{"id": 0, "text": "hello"}]}\n```'
    assert parse_translations(raw, [0]) == {0: "hello"}


def test_산문_머리말이_붙은_펜스를_벗겨_낸다() -> None:
    # 머리말은 모델의 습관이라 매 호출 재현된다. 여기서 거부하면 배치
    # 하나가 N번의 개별 호출로 바뀌는데, 그 N번도 같은 모델이 같은
    # 머리말을 붙여 되돌아온다.
    raw = (
        '알겠습니다. 아래가 번역입니다.\n```json\n{"translations": [{"id": 0, "text": "hi"}]}\n```'
    )
    assert parse_translations(raw, [0]) == {0: "hi"}


def test_펜스_뒤_꼬리말은_JSON에_섞이지_않는다() -> None:
    raw = (
        "번역했습니다.\n```json\n"
        '{"translations": [{"id": 0, "text": "hi"}]}\n'
        "```\n필요하시면 더 다듬어 드리겠습니다."
    )
    assert parse_translations(raw, [0]) == {0: "hi"}


def test_네_백틱_펜스도_벗겨_낸다() -> None:
    # CommonMark는 백틱 4개 이상의 펜스를 허용한다. 닫는 줄을 `== "```"`로
    # 좁히면 이 응답이 통째로 거부된다.
    raw = '````\n{"translations": [{"id": 0, "text": "hello"}]}\n````'
    assert parse_translations(raw, [0]) == {0: "hello"}


def test_닫는_줄에_언어_표기가_붙어도_벗겨_낸다() -> None:
    raw = '```json\n{"translations": [{"id": 0, "text": "hello"}]}\n```json'
    assert parse_translations(raw, [0]) == {0: "hello"}


@pytest.mark.parametrize(("opener", "closer"), [("```json", "```"), ("````", "````")])
def test_계약을_만족하는_블록이_둘이면_거부한다(opener: str, closer: str) -> None:
    # 초안을 낸 뒤 수정본을 내거나 형식을 먼저 복창하는 모델에서 나온다.
    # 앞의 것을 택하면 예외도 폴백도 없이 초안이 채택되고 진짜 답이
    # 버려진다 - 모델이 만든 모호성은 추측하지 않고 폴백으로 보낸다.
    #
    # **4백틱 조합이 따로 있는 이유**: 닫는 줄 판정을 `== "```"`로 좁히면
    # 두 블록이 하나로 합쳐지고, raw_decode가 머리의 값만 읽어 두 번째
    # 후보가 조용히 사라진다. 모호성이 거부가 아니라 임의 채택이 되는데,
    # 4백틱 블록 **하나**로는 그 완화가 드러나지 않는다 - 뒤에 남은
    # 백틱을 raw_decode가 잡담으로 보고 넘기기 때문이다.
    draft = json.dumps({"translations": [{"id": 0, "text": "<번역문>"}]})
    final = json.dumps({"translations": [{"id": 0, "text": "Hello"}]})
    raw = "\n".join(
        [
            "먼저 형식을 확인하겠습니다.",
            opener,
            draft,
            closer,
            "실제 번역입니다.",
            opener,
            final,
            closer,
        ]
    )
    with pytest.raises(InvalidResponseError, match="추측하지 않는다"):
        parse_translations(raw, [0])


def test_이어_붙은_JSON이_둘이면_거부한다() -> None:
    # 펜스 없이 값 두 개를 이어 붙인 형태다. 머리의 값만 읽고 말면 뒤의
    # 것이 조용히 사라진다 - 위의 펜스 두 블록과 같은 사고다.
    raw = '{"translations": [{"id": 0, "text": "A"}]}{"translations": [{"id": 0, "text": "B"}]}'
    with pytest.raises(InvalidResponseError, match="추측하지 않는다"):
        parse_translations(raw, [0])


def test_정상_JSON_뒤에_무관한_펜스가_붙어도_받는다() -> None:
    # 계약을 만족하는 후보가 하나뿐이므로 모호하지 않다. 첫 펜스 블록을
    # 무조건 택하는 구현은 여기서 'cuesift check out.srt'를 파싱하려다
    # 정상 응답을 거부한다.
    raw = (
        '{"translations": [{"id": 0, "text": "hello"}]}\n\n'
        "참고로 규격 검사는 아래로 돌리시면 됩니다.\n"
        "```bash\ncuesift check out.srt\n```"
    )
    assert parse_translations(raw, [0]) == {0: "hello"}


def test_퇴화_응답도_InvalidResponseError로_낸다() -> None:
    # json이 깊은 중첩에서 RecursionError를 낸다. 그것을 잡지 않으면
    # 퇴화 응답이 폴백 경로 밖으로 나가 실행 전체가 트레이스백으로 끝난다.
    # struct.degeneration을 1급 신호로 두는 프로젝트에서 퇴화는 실재한다.
    raw = "[" * 20000 + "]" * 20000
    with pytest.raises(InvalidResponseError):
        parse_translations(raw, [0])


def test_펜스_없는_산문_속_JSON은_거부한다() -> None:
    # 관대함의 경계다. 펜스는 모델이 그은 경계지만 산문 속 중괄호는
    # 우리가 추측하는 경계이고, 추측이 빗나가면 InvalidResponseError가
    # 아니라 '엉뚱한 곳을 자른 파싱 성공'이 된다.
    raw = '번역 결과입니다: {"translations": [{"id": 0, "text": "hi"}]}'
    with pytest.raises(InvalidResponseError):
        parse_translations(raw, [0])


def test_번역문의_개행을_보존한다() -> None:
    # 프롬프트가 여러 줄 자막을 두 글자 \n으로 표기하게 하고, json.loads가
    # 그것을 진짜 개행으로 푼다. 여기서 정규화하면 규격 검사의 줄 수·줄당
    # 문자 판정이 통째로 무의미해진다.
    raw = json.dumps({"translations": [{"id": 0, "text": "첫 줄\n둘째 줄"}]})
    assert parse_translations(raw, [0]) == {0: "첫 줄\n둘째 줄"}


def test_번역문의_앞뒤_공백을_보존한다() -> None:
    # strip()으로 정규화하면 공백만 있는 번역이 빈 문자열이 되어,
    # empty_translation 판정이 engine이 아니라 여기서 몰래 내려진다.
    # 무엇이 빈 번역인지 정하는 것은 engine의 일이다 (설계 §7.1).
    raw = json.dumps({"translations": [{"id": 0, "text": " hi "}, {"id": 1, "text": "  "}]})
    assert parse_translations(raw, [0, 1]) == {0: " hi ", 1: "  "}


def test_JSON이_아니면_거부한다() -> None:
    with pytest.raises(InvalidResponseError):
        parse_translations("죄송합니다, 번역할 수 없습니다.", [0])


def test_id가_누락되면_거부한다() -> None:
    raw = json.dumps({"translations": [{"id": 0, "text": "hello"}]})
    with pytest.raises(InvalidResponseError, match="누락"):
        parse_translations(raw, [0, 1])


def test_없는_id가_섞이면_거부한다() -> None:
    # 맥락 세그먼트를 번역해 돌려준 경우다. 지시 불이행 신호이므로
    # 배치를 폐기한다 (설계 §7.1).
    raw = json.dumps({"translations": [{"id": 0, "text": "a"}, {"id": 9, "text": "b"}]})
    with pytest.raises(InvalidResponseError, match="여분"):
        parse_translations(raw, [0])


def test_translations_키가_없으면_거부한다() -> None:
    with pytest.raises(InvalidResponseError, match="translations"):
        parse_translations(json.dumps({"result": []}), [0])


def test_최상위가_배열이면_거부한다() -> None:
    with pytest.raises(InvalidResponseError):
        parse_translations(json.dumps([{"id": 0, "text": "a"}]), [0])


@pytest.mark.parametrize("payload", ["translations를 못 만들었습니다", None, 5, True])
def test_최상위가_객체가_아니면_거부한다(payload: object) -> None:
    # 최상위 dict 검사가 없거나 `or`의 순서가 뒤집히면 여기서 TypeError가
    # 샌다. 폴백은 InvalidResponseError만 받으므로 그 예외는 배치 강등이
    # 아니라 실행 중단이 된다.
    #
    # **문자열만으로는 순서 반전을 잡지 못한다** - 문자열에 대한 `in`은
    # 부분 문자열 검사로 성립해 두 번째 조건을 그냥 통과시킨다. 순서가
    # 뒤집혔을 때 실제로 터지는 것은 null·숫자·불리언이다.
    with pytest.raises(InvalidResponseError):
        parse_translations(json.dumps(payload), [0])


def test_translations가_배열이_아니면_거부한다() -> None:
    raw = json.dumps({"translations": {"0": "hello"}})
    with pytest.raises(InvalidResponseError, match="배열"):
        parse_translations(raw, [0])


@pytest.mark.parametrize("items", [["hello"], [[0, "hello"]]])
def test_항목이_객체가_아니면_거부한다(items: list) -> None:
    # 번호를 통째로 생략하거나(["hello"]) 쌍으로 낸([[0, "hello"]]) 경우다.
    # 위치로 기대 id에 짝지으면 개수만 맞으면 통과해, 순서가 밀린 응답이
    # 조용히 다른 세그먼트에 붙는다. 배열 모양을 따로 두는 것은 dict만
    # 걸러 내는 검사를 느슨하게 고쳤을 때 item.get이 AttributeError로 새기
    # 때문이다 - 그 예외는 폴백이 받지 않는다.
    with pytest.raises(InvalidResponseError):
        parse_translations(json.dumps({"translations": items}), [0])


def test_text가_문자열이_아니면_거부한다() -> None:
    raw = json.dumps({"translations": [{"id": 0, "text": 42}]})
    with pytest.raises(InvalidResponseError, match="text"):
        parse_translations(raw, [0])


def test_text_키가_없으면_거부한다() -> None:
    raw = json.dumps({"translations": [{"id": 0}]})
    with pytest.raises(InvalidResponseError, match="text"):
        parse_translations(raw, [0])


def test_id가_정수가_아니면_거부한다() -> None:
    # match를 "id"로 두면 안 된다. "id가 누락됐다"에도 매치해서, 타입
    # 검사를 문자열까지 받아 주도록 완화해도 (누락 검사가 대신 걸려)
    # 테스트가 통과한다. 이 테스트가 이름 붙인 분기를 고정하려면 "정수"다.
    raw = json.dumps({"translations": [{"id": "0", "text": "a"}]})
    with pytest.raises(InvalidResponseError, match="정수"):
        parse_translations(raw, [0])


def test_id_키가_없으면_거부한다() -> None:
    # get("id", 0)처럼 기본값을 두면 번호 없는 항목이 0번 세그먼트의
    # 번역으로 조용히 접힌다. 개수까지 맞아떨어져 폴백도 돌지 않는다.
    raw = json.dumps({"translations": [{"text": "a"}]})
    with pytest.raises(InvalidResponseError, match="정수"):
        parse_translations(raw, [0])


def test_id가_불리언이면_거부한다() -> None:
    # bool은 int의 하위 타입이라 isinstance(True, int)가 참이다. 걸러 내지
    # 않으면 {"id": true}가 1번 세그먼트의 번역으로 접혀 들어간다.
    raw = json.dumps({"translations": [{"id": True, "text": "a"}]})
    with pytest.raises(InvalidResponseError, match="정수"):
        parse_translations(raw, [1])


def test_id가_중복되면_거부한다() -> None:
    # dict로 접으면 조용히 마지막 것이 이겨 개수 검증을 통과한다.
    raw = json.dumps({"translations": [{"id": 0, "text": "a"}, {"id": 0, "text": "b"}]})
    with pytest.raises(InvalidResponseError, match="중복"):
        parse_translations(raw, [0])


def test_빈_문자열은_파싱_단계에서_거부하지_않는다() -> None:
    # 빈 번역은 배치 폐기가 아니라 그 세그먼트만의 실패다. 판정은
    # engine이 한다 (설계 §7.1).
    raw = json.dumps({"translations": [{"id": 0, "text": ""}]})
    assert parse_translations(raw, [0]) == {0: ""}


def test_기대_id가_비면_빈_결과를_낸다() -> None:
    # 개별 폴백이 배치를 1개씩으로 강등하는 경로에서 기대 목록이 비는 일은
    # 없어야 하지만, 비었을 때 조용히 아무 응답이나 통과시키면 안 된다.
    assert parse_translations(json.dumps({"translations": []}), []) == {}
